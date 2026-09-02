"""Конфигурация max-news-bot-omsu: всё читается из переменных окружения (.env).

Секреты НЕ хранятся в git — только в .env на стороне пользователя
(файл .env автоматически игнорируется .gitignore).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _read_secret(env_name: str, path: str = "") -> str:
    """Значение из env или файла (для совместимости со схемой /opt/bot-max/*)."""
    val = os.environ.get(env_name, "").strip()
    if val:
        return val
    if path:
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except OSError:
            return ""
    return ""


def _as_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _as_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """Все настройки бота. Значения по умолчанию — безопасные/выключенные."""

    # ── Бот MAX ────────────────────────────────────────────────
    token: str                      # токен бота MAX (обязателен)
    polling: bool = True            # True: Long Polling (ноль инфраструктуры);
                                    # False: webhook (нужен домен + HTTPS)
    target_chat_id: int = 0         # канал, куда репостим (обязателен)

    # ── Источник 1: VK-паблик (опционально) ────────────────────
    vk_domain: str = ""             # напр. gorodtavda (без vk.com/)
    vk_token: str = ""              # сервисный ключ dev.vk.ru
    news_interval: int = 60         # сек, опрос стены

    # ── Источник 2: канал MAX (РСЧС-стиль, опционально) ────────
    # Читается через get_messages БЕЗ членства бота, дедуп по mid.
    rsch_chat_id: int = 0           # публичный канал-источник (напр. РСЧС региона)
    rsch_interval: int = 60         # сек, опрос канала

    # ── Хранилище state (дедупликация) ─────────────────────────
    state_dir: str = "./state"

    # ── Webhook (только при polling=false) ─────────────────────
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080
    webhook_path: str = "/webhook"

    # ── Прочее ─────────────────────────────────────────────────
    yt_dlp_path: str = "yt-dlp"     # в Docker — из pip; можно переопределить
    tz: str = "UTC"


def load_settings(env: dict | None = None) -> Settings:
    """Собирает Settings из os.environ (или переданного dict — для тестов)."""
    old = dict(os.environ)
    try:
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
        return Settings(
            token=_read_secret("MAX_BOT_TOKEN", "/run/secrets/max_bot_token"),
            polling=_as_bool("POLLING", True),
            target_chat_id=_as_int("TARGET_CHAT_ID", 0),
            vk_domain=os.environ.get("VK_DOMAIN", "").strip(),
            vk_token=_read_secret("VK_TOKEN", "/run/secrets/vk_token"),
            news_interval=_as_int("NEWS_INTERVAL", 60),
            rsch_chat_id=_as_int("RSCH_CHAT_ID", 0),
            rsch_interval=_as_int("RSCH_INTERVAL", 60),
            state_dir=os.environ.get("STATE_DIR", "./state").strip(),
            webhook_url=os.environ.get("WEBHOOK_URL", "").strip().rstrip("/"),
            webhook_secret=_read_secret("WEBHOOK_SECRET", "/run/secrets/webhook_secret"),
            webhook_host=os.environ.get("WEBHOOK_HOST", "0.0.0.0").strip(),
            webhook_port=_as_int("WEBHOOK_PORT", 8080),
            webhook_path=os.environ.get("WEBHOOK_PATH", "/webhook").strip(),
            yt_dlp_path=os.environ.get("YT_DLP_PATH", "yt-dlp").strip(),
            tz=os.environ.get("TZ", "UTC").strip(),
        )
    finally:
        os.environ.clear()
        os.environ.update(old)


def validate(s: Settings) -> list[str]:
    """Проверяет обязательные настройки, возвращает список ошибок."""
    errors: list[str] = []
    if not s.token:
        errors.append("MAX_BOT_TOKEN не задан (токен бота MAX)")
    if not s.target_chat_id:
        errors.append("TARGET_CHAT_ID не задан (куда репостить)")
    if not s.polling and not s.webhook_url:
        errors.append("WEBHOOK_URL не задан (нужен при POLLING=false)")
    return errors
