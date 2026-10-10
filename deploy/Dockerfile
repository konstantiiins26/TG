# Альтернатива systemd, если предпочитаете контейнеры.
#
#   docker build -f deploy/Dockerfile -t gift-sniper .
#   docker run -d --name sniper --restart=unless-stopped \
#     --env-file ./.env \
#     -v $HOME/.gift_sniper:/data:ro \
#     -v sniper-state:/state \
#     gift-sniper
#
# Ключ монтируется томом только на чтение и НЕ попадает в образ.

FROM python:3.12-slim

# Работаем не от root: компрометация процесса не должна давать root в контейнере.
RUN useradd --create-home --uid 10001 sniper

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gift_sniper.py test_gift_sniper.py ./

# Состояние (SQLite, запись рынка) — в томе, чтобы переживало пересоздание.
ENV DB_PATH=/state/sniper_state.db \
    RECORD_PATH=/state/market_history.jsonl \
    PYTHONUNBUFFERED=1
RUN mkdir -p /state && chown sniper:sniper /state
VOLUME /state

USER sniper

# Проверяем логику на старте: сломанная сборка не должна дойти до торговли.
RUN python test_gift_sniper.py > /dev/null

ENTRYPOINT ["python", "gift_sniper.py"]
