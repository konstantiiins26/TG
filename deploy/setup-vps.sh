#!/usr/bin/env bash
# Установка снайпера на чистый Ubuntu/Debian VPS.
#
#   sudo bash deploy/setup-vps.sh
#
# Что делает: заводит отдельного пользователя без прав root, ставит код в
# /opt/gift-sniper, собирает venv, кладёт systemd-юнит и создаёт заготовку
# файла с настройками. Бота НЕ запускает: сначала вы впишете адреса
# коллекций, иначе стартовать всё равно нечему.
#
# Скрипт идемпотентен — повторный запуск обновляет код и юнит, не трогая
# ваши настройки и накопленную запись рынка.

set -euo pipefail

REPO="${REPO:-https://github.com/konstantiiins26/TG.git}"
BRANCH="${BRANCH:-claude/telegram-gifts-nft-sniper-lhui4z}"
APP_DIR=/opt/gift-sniper
ENV_DIR=/etc/gift-sniper
ENV_FILE="$ENV_DIR/env"
SVC_USER=sniper

if [[ $EUID -ne 0 ]]; then
    echo "Запускайте через sudo: sudo bash deploy/setup-vps.sh" >&2
    exit 1
fi

echo "==> Пакеты"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git

echo "==> Пользователь $SVC_USER (без shell и без прав root)"
id -u "$SVC_USER" &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin "$SVC_USER"

echo "==> Код в $APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" checkout --quiet "$BRANCH"
    git -C "$APP_DIR" reset --hard --quiet "origin/$BRANCH"
else
    git clone --quiet --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

echo "==> venv и зависимости"
[[ -d "$APP_DIR/.venv" ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> Тесты (сломанная сборка не должна дойти до торговли)"
"$APP_DIR/.venv/bin/python" "$APP_DIR/test_gift_sniper.py" >/dev/null

chown -R "$SVC_USER:$SVC_USER" "$APP_DIR"

echo "==> Настройки в $ENV_FILE"
mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<'ENVEOF'
# Режим: --record копит запись рынка и НЕ торгует. Это то, с чего начинают.
SNIPER_ARGS=--record

# Адреса коллекций через запятую. Без них бот не стартует.
TARGET_COLLECTIONS=

# Доверенные адреса коллекций. Пусто = защита от скам-клонов ОТКЛЮЧЕНА.
COLLECTION_WHITELIST=

# Выборка для floor. 15 страниц x 100 лотов; вместе с интервалом 60с это
# 15 запросов в минуту — без ключа TonAPI это безопасно.
FLOOR_SAMPLE_PAGES=15
POLL_INTERVAL_SEC=60

# Необязательно: с ключом TonAPI лимиты выше.
TONAPI_KEY=

# Нужен только для торгового режима, для --record не требуется.
ANTHROPIC_API_KEY=

# Запись рынка и БД — рядом с кодом, переживают перезапуск.
RECORD_PATH=/opt/gift-sniper/market_history.jsonl
DB_PATH=/opt/gift-sniper/sniper_state.db

# Покупка только симулируется. Не менять, пока подпись транзакций не написана.
DRY_RUN=1
ENVEOF
    echo "    создан (впишите TARGET_COLLECTIONS)"
else
    echo "    уже есть — не трогаю"
fi
# Файл с настройками читает только systemd и служебный пользователь.
chown root:"$SVC_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "==> systemd"
cp "$APP_DIR/deploy/gift-sniper.service" /etc/systemd/system/
systemctl daemon-reload

cat <<'DONE'

Установлено. Осталось два шага:

  1) Впишите адреса коллекций:
       sudo nano /etc/gift-sniper/env      # строка TARGET_COLLECTIONS=

  2) Запустите и посмотрите лог:
       sudo systemctl enable --now gift-sniper
       journalctl -u gift-sniper -f

Дальше бот работает сам: переживает перезагрузку VPS и падения сети.

Через несколько дней прогоните бэктест по накопленной записи:
       sudo -u sniper /opt/gift-sniper/.venv/bin/python \
            /opt/gift-sniper/gift_sniper.py --backtest \
            /opt/gift-sniper/market_history.jsonl
DONE
