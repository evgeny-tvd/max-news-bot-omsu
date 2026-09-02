#!/bin/sh
# entrypoint: отдаёт /data (state) пользователю бота, затем запускает бота
# от непривилегированного пользователя. Работает при любом способе создания
# volume/bind-mount (даже если хост-папка создана от root).
#
# Понижение привилегий — через setpriv (util-linux, есть в любом slim-образе):
# su-exec/gosu в репозитории Debian bookworm отсутствуют.
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R botuser:botuser /data
    exec setpriv --reuid=botuser --regid=botuser --clear-groups "$@"
fi

exec "$@"
