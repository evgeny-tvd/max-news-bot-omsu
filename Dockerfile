FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

# tzdata — чтобы TZ из .env работал (Asia/Yekaterinburg и т.п.)
# su-exec — для понижения привилегий после chown /data (см. entrypoint.sh)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata su-exec \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src/ /app/src/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Не root: безопаснее и «правильный тон» для раздачи коллегам
RUN useradd -m botuser

USER root
ENTRYPOINT ["/entrypoint.sh"]

ENV STATE_DIR=/data
VOLUME ["/data"]

EXPOSE 8080
CMD ["python", "-m", "src.bot"]
