#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  max-news-bot-omsu — установка одной командой
#
#  curl -fsSL https://raw.githubusercontent.com/evgeny-tvd/max-news-bot-omsu/main/install.sh | bash
#
#  Мастер задаёт несколько вопросов, пишет .env и docker-compose.yml,
#  запускает бота. Секреты остаются только в .env на этом сервере.
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

IMAGE="ghcr.io/evgeny-tvd/max-news-bot-omsu:latest"
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"
BOLD="\033[1m"; DIM="\033[2m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; RESET="\033[0m"

say()  { echo -e "${GREEN}==>${RESET} $*"; }
warn() { echo -e "${YELLOW}!!${RESET} $*"; }
fail() { echo -e "${RED}!!${RESET} $*"; exit 1; }

ask() { # ask "Вопрос" "подсказка" "значение_по_умолчанию"
  local q="$1" hint="${2:-}" def="${3:-}" ans
  echo -e "${BOLD}${q}${RESET}"
  [ -n "$hint" ] && echo -e "${DIM}  ${hint}${RESET}"
  [ -n "$def" ] && echo -e "${DIM}  [по умолчанию: ${def}]${RESET}"
  read -r -p "> " ans
  ans="${ans:-$def}"
  # Убираем CR (вставка из буфера Windows) и лишние пробелы по краям
  ans="${ans//$'\r'/}"
  ans="$(printf '%s' "$ans" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  echo "$ans"
}

# Валидаторы
valid_token() { [[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]; }        # буквы/цифры/-/_
valid_chat_id() { [[ "$1" =~ ^-?[0-9]+$ ]]; }

# ─── 1. Проверка docker ───────────────────────────────────────────────
say "Проверяю Docker…"
if ! command -v docker >/dev/null 2>&1; then
  warn "Docker не установлен."
  echo -e "  Установить можно одной командой (нужен root):"
  echo -e "  ${BOLD}curl -fsSL https://get.docker.com | sh${RESET}"
  echo -e "  Затем добавьте пользователя в группу docker: ${BOLD}sudo usermod -aG docker \$USER${RESET}"
  echo -e "  и перезайдите в SSH, после чего запустите установку снова."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  fail "Нужен docker с поддержкой 'docker compose' (плагин). Обновите Docker."
fi
if ! docker info >/dev/null 2>&1; then
  warn "Docker установлен, но нет доступа (нужна группа docker или sudo)."
  echo -e "  Сделайте: ${BOLD}sudo usermod -aG docker \$USER${RESET}, перезайдите и повторите."
  exit 1
fi

# ─── 2. Вопросы ───────────────────────────────────────────────────────
echo
say "Отвечайте на вопросы. Пустой ответ = пропустить (если поле необязательно)."

while :; do
  TOKEN=$(ask "Токен бота MAX (обязательно)" \
    "Как получить: создайте бота в MAX — токен выдаётся при создании (см. DEPLOY.md)")
  [ -z "$TOKEN" ] && fail "Без токена бота нельзя. Запустите установку ещё раз и вставьте токен."
  valid_token "$TOKEN" && break
  warn "Токен содержит недопустимые символы (только буквы, цифры, -, _). Попробуйте ещё раз."
done

while :; do
  CHAT_ID=$(ask "ID канала, куда репостить новости (обязательно)" \
    "Не знаете? Добавьте бота в свой канал и напишите ему /start — он ответит «📢 Этот чат: …»")
  [ -z "$CHAT_ID" ] && fail "Без ID канала нельзя. Как узнать — в подсказке выше."
  valid_chat_id "$CHAT_ID" && break
  warn "ID канала — это число (возможно, со знаком минус). Попробуйте ещё раз."
done

VK_DOMAIN=$(ask "VK-паблик-источник (обязательно)" \
  "Например: gorodtavda (без vk.com/). Ваши новости будут браться отсюда." \
  "gorodtavda")

while :; do
  VK_TOKEN=$(ask "Сервисный ключ VK (обязательно)" \
    "dev.vk.ru → мини-приложение → Разработка → Ключи доступа → Сервисный ключ.")
  [ -z "$VK_TOKEN" ] && fail "VK — основной источник репостера, без сервисного ключа нельзя."
  valid_token "$VK_TOKEN" && break
  warn "Ключ содержит недопустимые символы (только буквы, цифры, -, _). Попробуйте ещё раз."
done

RSCH_CHAT_ID=$(ask "Канал MAX-источник для предупреждений (РСЧС)" \
  "Встроен по умолчанию: РСЧС Свердловской области (-69712963313704). Если вы из другого региона — укажите свой." \
  "-69712963313704")
valid_chat_id "$RSCH_CHAT_ID" || RSCH_CHAT_ID="-69712963313704"

NEWS_INTERVAL=$(ask "Как часто опрашивать источники, секунд (минимум 30)" "" "60")
valid_chat_id "$NEWS_INTERVAL" && [ "$NEWS_INTERVAL" -ge 30 ] 2>/dev/null || NEWS_INTERVAL=60

TZ_DEF=$(timedatectl show -p Timezone --value 2>/dev/null || echo "Asia/Yekaterinburg")
TZ_VAL=$(ask "Часовой пояс" "" "$TZ_DEF")

# ─── 3. Пишем .env (с бэкапом, если файл уже есть) ───────────────────
if [ -f "$ENV_FILE" ]; then
  cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d_%H%M%S)"
  warn "Старый $ENV_FILE сохранён в $ENV_FILE.bak.*"
fi
cat > "$ENV_FILE" <<EOF
# Сгенерировано install.sh $(date '+%d.%m.%Y %H:%M')
MAX_BOT_TOKEN=$TOKEN
TARGET_CHAT_ID=$CHAT_ID
VK_DOMAIN=$VK_DOMAIN
VK_TOKEN=$VK_TOKEN
RSCH_CHAT_ID=$RSCH_CHAT_ID
NEWS_INTERVAL=$NEWS_INTERVAL
RSCH_INTERVAL=$NEWS_INTERVAL
POLLING=true
TZ=$TZ_VAL
# ADMIN_CHAT_IDS=    # необязательно: личные чаты, откуда можно запускать
#                     # /news_now и /rsch_now (через запятую). По умолчанию —
#                     # команды работают только из самого канала TARGET_CHAT_ID.
EOF
chmod 600 "$ENV_FILE"
say "Записан $ENV_FILE (права 600 — секреты только для этого пользователя)."

# ─── 4. Пишем docker-compose.yml (с бэкапом) ─────────────────────────
if [ -f "$COMPOSE_FILE" ]; then
  cp "$COMPOSE_FILE" "$COMPOSE_FILE.bak.$(date +%Y%m%d_%H%M%S)"
  warn "Старый $COMPOSE_FILE сохранён в $COMPOSE_FILE.bak.*"
fi
cat > "$COMPOSE_FILE" <<EOF
services:
  bot:
    image: $IMAGE
    container_name: max-news-bot
    restart: unless-stopped
    env_file: .env
    environment:
      - TZ=\${TZ:-Asia/Yekaterinburg}
    volumes:
      - ./state:/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz')"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s
EOF
say "Записан $COMPOSE_FILE."

# ─── 5. Запуск ────────────────────────────────────────────────────────
say "Скачиваю образ и запускаю…"
docker compose up -d

echo
say "Бот запущен! Проверьте:"
echo -e "  ${BOLD}docker compose logs -f${RESET}   — логи (Ctrl+C для выхода)"
echo -e "  ${BOLD}docker compose ps${RESET}       — статус (health: healthy)"
echo
say "Важно: при первом запуске бот запоминает последний пост/сообщение источника"
echo -e "и ${BOLD}не шлёт старые${RESET} — новые начнут приходить после публикации."
