#!/bin/sh
# entrypoint: отдаёт /data (state) пользователю бота, затем запускает бота
# от непривилегированного пользователя. Работает при любом способе создания
# volume/bind-mount (даже если хост-папка создана от root).
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R botuser:botuser /data
    exec su-exec botuser "$@"
fi

exec "$@"
