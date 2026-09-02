"""max-news-bot-omsu — универсальный бот-репостер новостей для мессенджера MAX.

Репостит в указанный канал:
  1) посты из VK-паблика (wall.get, медиа: фото/видео через yt-dlp);
  2) сообщения публичного канала MAX (get_messages БЕЗ членства бота,
     дедупликация по mid — логика проверена на боевом боте администрации).

Режимы доставки апдейтов:
  - POLLING=true  (по умолчанию) — Long Polling, нужен только исходящий интернет;
  - POLLING=false — webhook (нужен публичный HTTPS-URL).

Код основан на боевом боте bot_max (администрация Тавдинского МО), 02.09.2026.
"""

import asyncio
import logging
import shutil
from datetime import datetime

import aiohttp
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from maxapi import Bot, Dispatcher
from maxapi.enums import ChatType
from maxapi.enums.upload_type import UploadType
from maxapi.enums.update import UpdateType
from maxapi.types import BotStarted, Command, MessageCreated
from maxapi.types.input_media import InputMediaBuffer

from .config import Settings, load_settings, validate

# ─── Настройки ────────────────────────────────────────────────────────
settings: Settings = load_settings()

errors = validate(settings)
if errors:
    raise SystemExit("Ошибки конфигурации:\n - " + "\n - ".join(errors))

bot = Bot(token=settings.token)
# use_create_task=True: события обрабатываются в фоне (быстрее отвечаем)
dp = Dispatcher(use_create_task=True)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

# ─── Лимиты (проверены на боевом боте, менять с осторожностью) ────────
MAX_MEDIA_PER_POST = 10  # VK позволяет до 10 фото на пост
MAX_MEDIA_BYTES = 200 * 1024 * 1024  # MAX принимает файлы до 4 ГБ; самоограничение 200 МБ
MAX_TEXT_LEN = 3900  # MAX API принимает до 4000 символов
MAX_VIDEO_DURATION = 1800  # видео > 30 мин пропускаем (вместо файла — ссылка)
VK_DL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Referer": "https://vkvideo.ru/",
}

# ─── State (дедупликация) ─────────────────────────────────────────────
VK_STATE_FILE = f"{settings.state_dir}/vk_last_id"
RSCH_STATE_FILE = f"{settings.state_dir}/rsch_last_mid"

START_TEXT = (
    "Привет! Я бот новостей 🤖\n"
    "📢 Этот чат: {chat_id}\n\n"
    "Команды:\n"
    "/news — последние новости из VK\n"
    "/news_now — проверить и прислать свежие новости\n"
    "/rsch_now — проверить канал предупреждений\n"
    "/time — время\n"
    "/date — дата\n"
    "/echo <текст> — повторю за тобой"
)


# ─── Медиа (пайплайн перенесён из bot_max без изменений) ─────────────

async def _http_get_bytes(
    url: str, max_bytes: int = MAX_MEDIA_BYTES, headers: dict | None = None
) -> bytes | None:
    """Скачивает файл по URL в память (с лимитом размера и заголовками)."""
    try:
        async with aiohttp.ClientSession() as session:
            # Таймаут 300 с: видео 720p ~75 МБ качается ~85-90 с
            async with session.get(url, timeout=300, headers=headers or {}) as resp:
                if resp.status != 200:
                    log.warning("HTTP %s при скачивании %s", resp.status, url[:80])
                    return None
                data = await resp.read()
                if len(data) > max_bytes:
                    log.warning("Файл слишком большой (%d б): %s", len(data), url[:80])
                    return None
                return data
    except Exception as e:
        log.error("Ошибка скачивания %s: %s", url[:80], e)
        return None


async def _vk_video_mp4(owner_id: int, video_id: int, access_key: str | None) -> str | None:
    """Прямая mp4-ссылка на видео через yt-dlp (быстро, без скачивания)."""
    video_url = f"https://vkvideo.ru/video{owner_id}_{video_id}"
    ytdlp = settings.yt_dlp_path if settings.yt_dlp_path != "yt-dlp" else shutil.which("yt-dlp") or "yt-dlp"
    try:
        proc = await asyncio.create_subprocess_exec(
            ytdlp, "-g", "-f", "url720/url1080/mp4/best", "--no-warnings", video_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        lines = (stdout or b"").decode("utf-8", errors="replace").strip().splitlines()
        if lines:
            return lines[0]
        log.warning("yt-dlp не дал URL для %s", video_url)
        return None
    except Exception as e:
        log.error("yt-dlp error %s: %s", video_url, e)
        return None


async def _post_media(post: dict) -> tuple[list, list]:
    """Медиавложения поста (ПАРАЛЛЕЛЬНО, порядок сохраняется).

    Возвращает (media, missing_video_urls).
    """
    media: list = []
    missing_video_urls: list = []

    async def _process_one(idx: int, att: dict) -> tuple[int, object | None, str | None]:
        try:
            if att["type"] == "photo":
                sizes = att["photo"].get("sizes") or []
                if not sizes:
                    return idx, None, None
                best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
                url = best.get("url")
                if not url:
                    return idx, None, None
                data = await _http_get_bytes(url)
                if data:
                    return idx, InputMediaBuffer(
                        buffer=data,
                        filename=f"vk_{best.get('width')}x{best.get('height')}.jpg",
                        type=UploadType.IMAGE,
                    ), None
                return idx, None, None
            elif att["type"] == "video":
                v = att["video"]
                dur = v.get("duration") or 0
                if dur > MAX_VIDEO_DURATION:
                    log.info("Видео %s_%s длинное (%dс) — пропускаем", v.get("owner_id"), v.get("id"), dur)
                    return idx, None, f"https://vkvideo.ru/video{v.get('owner_id')}_{v.get('id')}"
                mp4 = await _vk_video_mp4(v.get("owner_id"), v.get("id"), v.get("access_key"))
                if mp4:
                    data = await _http_get_bytes(mp4, headers=VK_DL_HEADERS)
                    if data:
                        # filename БЕЗ расширения: maxapi добавит .mp4 сам (иначе .mp4.mp4)
                        return idx, InputMediaBuffer(buffer=data, filename="vk_video", type=UploadType.VIDEO), None
                    return idx, None, f"https://vkvideo.ru/video{v.get('owner_id')}_{v.get('id')}"
                return idx, None, f"https://vkvideo.ru/video{v.get('owner_id')}_{v.get('id')}"
        except Exception as e:
            log.error("Ошибка обработки вложения: %s", e)
        return idx, None, None

    atts = []
    for att in (post.get("attachments") or []):
        if att.get("type") in ("photo", "video"):
            atts.append(att)
            if len(atts) >= MAX_MEDIA_PER_POST:
                break

    results = await asyncio.gather(*(_process_one(i, a) for i, a in enumerate(atts)))
    for idx, m, missing in sorted(results, key=lambda r: r[0]):
        if m is not None:
            media.append(m)
        if missing:
            missing_video_urls.append(missing)
    return media, missing_video_urls


# ─── Источник 1: VK-паблик ────────────────────────────────────────────

async def fetch_vk_posts() -> list:
    """Последние посты со стены паблика через VK API wall.get."""
    if not settings.vk_token or not settings.vk_domain:
        log.info("VK-источник выключен (нужны VK_DOMAIN и VK_TOKEN)")
        return []
    params = {
        "access_token": settings.vk_token,
        "domain": settings.vk_domain,
        "count": 5,
        "v": "5.199",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.vk.com/method/wall.get", params=params, timeout=20
        ) as resp:
            data = await resp.json()
    if "error" in data:
        log.error("VK API error: %s", data["error"])
        return []
    return data.get("response", {}).get("items", [])


def _read_state(path: str) -> str:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
        return ""


def _save_state(path: str, value: str) -> None:
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(value)


async def repost_news(force: bool = False) -> int:
    """Присылает новые посты паблика в target_chat_id. Возвращает число отправленных."""
    if not settings.target_chat_id:
        return 0
    items = await fetch_vk_posts()
    if not items:
        return 0

    last_id = int(_read_state(VK_STATE_FILE) or 0)
    fresh = [p for p in items if p["id"] > last_id]
    if force:
        fresh = items[:1] if not fresh else fresh
    if not fresh:
        log.info("Новых постов нет (last_id=%s)", last_id)
        return 0

    sent = 0
    for p in reversed(fresh):  # от старых к новым — хронология в канале
        text = (p.get("text") or "").strip()
        if not text and not (p.get("attachments")):
            continue
        if len(text) > MAX_TEXT_LEN:
            text = text[: MAX_TEXT_LEN - 3] + "..."
        msg = text if text else None
        try:
            media, missing_urls = await _post_media(p)
            if missing_urls:
                extra = "\n\n📹 Видео: " + "\n".join(missing_urls)
                msg = (msg + extra) if msg else extra.strip()
            await bot.send_message(
                chat_id=settings.target_chat_id,
                text=msg,
                attachments=media or None,
            )
            sent += 1
            log.info("Отправлен пост wall%s_%s (медиа: %d)", p["owner_id"], p["id"], len(media))
        except Exception as e:
            log.error("Ошибка отправки поста %s: %s", p["id"], e)
        await asyncio.sleep(1)  # лимит MAX ~2 сообщения/сек

    _save_state(VK_STATE_FILE, str(max(p["id"] for p in fresh)))
    return sent


async def news_loop():
    """Фоновый цикл: проверяет VK каждые news_interval секунд."""
    if not (settings.vk_token and settings.vk_domain and settings.target_chat_id):
        log.info("Репост VK выключен (нужны VK_DOMAIN, VK_TOKEN, TARGET_CHAT_ID)")
        return
    await asyncio.sleep(10)  # дать боту стартовать
    while True:
        try:
            await repost_news()
        except Exception as e:
            log.error("news_loop: %s", e)
        await asyncio.sleep(settings.news_interval)


# ─── Источник 2: канал MAX (РСЧС-стиль, логика как в bot_max) ─────────

async def fetch_rsch_messages() -> list:
    """Последние сообщения канала-источника (свежие идут первыми, до 50 шт)."""
    try:
        res = await bot.get_messages(chat_id=settings.rsch_chat_id)
        msgs = getattr(res, "messages", None) or []
        return list(msgs)
    except Exception as e:
        log.error("Канал-источник: get_messages ошибка: %s", e)
        return []


async def repost_rsch(force: bool = False) -> int:
    """Пересылает новые сообщения канала в target_chat_id.

    Дедупликация по body.mid. При первом запуске (state пуст) ничего не шлём —
    только запоминаем последний mid, чтобы не мусорить старыми сообщениями.
    """
    if not settings.rsch_chat_id or not settings.target_chat_id:
        log.info("Репост канала выключен (нужен RSCH_CHAT_ID)")
        return 0
    msgs = await fetch_rsch_messages()
    if not msgs:
        return 0

    newest_mid = msgs[0].body.mid
    last_mid = _read_state(RSCH_STATE_FILE)

    if not last_mid:
        _save_state(RSCH_STATE_FILE, newest_mid)
        log.info("Канал-источник: инициализация — запомнен mid=%s (старые не шлём)", newest_mid)
        return 0

    fresh = []
    for m in msgs:
        if m.body.mid == last_mid:
            break
        fresh.append(m)
    if force and not fresh:
        fresh = msgs[:1]
    if not fresh:
        log.info("Канал-источник: новых сообщений нет (mid=%s)", newest_mid)
        return 0

    sent = 0
    for m in reversed(fresh):
        text = (m.body.text or "").strip()
        if not text:
            continue
        if len(text) > MAX_TEXT_LEN:
            text = text[: MAX_TEXT_LEN - 3] + "..."
        try:
            await bot.send_message(chat_id=settings.target_chat_id, text=text)
            sent += 1
            log.info("Канал-источник: отправлено mid=%s (%d симв.)", m.body.mid, len(text))
        except Exception as e:
            log.error("Канал-источник: ошибка отправки mid=%s: %s", m.body.mid, e)
        await asyncio.sleep(1)

    _save_state(RSCH_STATE_FILE, newest_mid)
    return sent


async def rsch_loop():
    """Фоновый цикл: проверяет канал каждые rsch_interval секунд."""
    if not settings.rsch_chat_id or not settings.target_chat_id:
        log.info("Репост канала выключен (нужен RSCH_CHAT_ID)")
        return
    await asyncio.sleep(15)  # дать боту стартовать
    while True:
        try:
            await repost_rsch()
        except Exception as e:
            log.error("rsch_loop: %s", e)
        await asyncio.sleep(settings.rsch_interval)


# ─── Команды бота ─────────────────────────────────────────────────────

def _start_text(chat_id) -> str:
    return START_TEXT.format(chat_id=chat_id)


@dp.bot_started()
async def bot_started(event: BotStarted):
    # ВАЖНО: сюда попадает chat_id, из которого запустили бота —
    # это и есть «самообнаружение канала» для владельца.
    await event.bot.send_message(chat_id=event.chat_id, text=_start_text(event.chat_id))


@dp.message_created(Command("start"))
async def start_cmd(event: MessageCreated):
    await event.message.answer(_start_text(event.message.recipient.chat_id))


@dp.message_created(Command("time"))
async def time_cmd(event: MessageCreated):
    now = datetime.now()
    await event.message.answer(f"🕐 Сейчас {now.strftime('%H:%M:%S')}")


@dp.message_created(Command("date"))
async def date_cmd(event: MessageCreated):
    now = datetime.now()
    await event.message.answer(f"📅 Сегодня {now.strftime('%d.%m.%Y')}")


@dp.message_created(Command("echo"))
async def echo_cmd(event: MessageCreated):
    parts = (event.message.body.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await event.message.answer("Напиши так: /echo Привет!")
        return
    await event.message.answer(f"🗣 Ты сказал: {parts[1]}")


@dp.message_created(Command("news"))
async def news_cmd(event: MessageCreated):
    """Прислать последние 3 поста паблика по запросу."""
    items = await fetch_vk_posts()
    if not items:
        await event.message.answer("Не удалось получить новости 😔")
        return
    for p in items[:3]:
        text = (p.get("text") or "").strip()
        if not text and not (p.get("attachments")):
            continue
        msg = text[:MAX_TEXT_LEN] if text else None
        try:
            media, missing_urls = await _post_media(p)
            if missing_urls:
                extra = "\n\n📹 Видео: " + "\n".join(missing_urls)
                msg = (msg + extra) if msg else extra.strip()
            await event.message.answer(msg, attachments=media or None)
        except Exception as e:
            log.error("/news: %s", e)
        await asyncio.sleep(1)


@dp.message_created(Command("news_now"))
async def news_now_cmd(event: MessageCreated):
    sent = await repost_news(force=True)
    if sent == 0:
        await event.message.answer("Свежих новостей нет 🙂")


@dp.message_created(Command("rsch_now"))
async def rsch_now_cmd(event: MessageCreated):
    sent = await repost_rsch(force=True)
    if sent == 0:
        await event.message.answer("Свежих сообщений из канала нет 🙂")


@dp.message_created()
async def any_text(event: MessageCreated):
    # В каналах не отвечаем на произвольный текст — чтобы не мусорить
    chat_type = getattr(event.message.recipient, "chat_type", None)
    if chat_type == ChatType.CHANNEL:
        return
    await event.message.answer("Я пока умею только команды из /start 😅")


# ─── Запуск ───────────────────────────────────────────────────────────

@dp.on_started()
async def on_dp_started() -> None:
    """Вызывается один раз при старте диспетчера (в обоих режимах)."""
    if settings.polling:
        log.info("Режим: Long Polling (webhook не подписываем — MAX запрещает оба сразу)")
    else:
        log.info("Подписываемся на webhook: %s", settings.webhook_url)
        try:
            await bot.subscribe_webhook(
                url=settings.webhook_url,
                update_types=[
                    UpdateType.MESSAGE_CREATED,
                    UpdateType.MESSAGE_CALLBACK,
                    UpdateType.BOT_STARTED,
                ],
                secret=settings.webhook_secret or None,
            )
            log.info("Подписка на webhook зарегистрирована успешно.")
        except Exception as exc:
            log.error("Не удалось зарегистрировать webhook: %s", exc)
    asyncio.create_task(news_loop())
    asyncio.create_task(rsch_loop())


def build_app() -> FastAPI:
    """FastAPI-приложение: /healthz. Webhook-часть — только при POLLING=false."""
    if settings.polling:
        # В polling-режиме MAX-объект webhook не создаём вовсе:
        # он пишет лишние предупреждения в лог и не используется.
        app = FastAPI(title="max-news-bot health")

        @app.get("/healthz")
        async def healthz() -> JSONResponse:
            return JSONResponse({"status": "ok", "polling": True})

        return app

    from maxapi.webhook.fastapi import FastAPIMaxWebhook

    webhook = FastAPIMaxWebhook(dp=dp, bot=bot, secret=settings.webhook_secret or None)
    app = FastAPI(title="max-news-bot webhook", lifespan=webhook.lifespan)
    webhook.setup(app, path=settings.webhook_path)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "polling": False})

    return app


async def main() -> None:
    if settings.polling:
        # healthz в фоне (для docker healthcheck), polling — основной цикл
        app = build_app()
        config = uvicorn.Config(app=app, host=settings.webhook_host,
                                port=settings.webhook_port, log_level="warning")
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())
        log.info("Long Polling стартует (skip_updates=True — старые события не шлём)")
        await dp.start_polling(bot, skip_updates=True)
    else:
        app = build_app()
        config = uvicorn.Config(app=app, host=settings.webhook_host,
                                port=settings.webhook_port, log_level="info")
        server = uvicorn.Server(config)
        log.info("Webhook-сервер на %s:%d%s", settings.webhook_host,
                 settings.webhook_port, settings.webhook_path)
        await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
