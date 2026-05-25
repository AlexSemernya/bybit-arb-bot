#!/bin/bash
# deploy.sh — Деплой бота на сервер + перезапуск
# Использование: ./deploy.sh

set -e

# ─── Настройки сервера ────────────────────────────────────────
SERVER_USER="root"
SERVER_HOST="bybit-vps"            # алиас из ~/.ssh/config (IPv6 нужен алиас)
REMOTE_DIR="/root/bybit_arb_bot"   # путь к боту на сервере
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# ─── Цвета для вывода ─────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}🚀 Деплой на ${SERVER_USER}@${SERVER_HOST}${NC}"

# ─── 1. Синхронизация файлов ──────────────────────────────────
echo -e "\n${YELLOW}📦 Синхронизируем файлы...${NC}"
rsync -avz --progress \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'trades.db' \
    --exclude 'bot.pid' \
    --exclude 'bot.log' \
    --exclude 'dashboard.html' \
    --exclude '.env' \
    --exclude 'deploy.sh' \
    "${LOCAL_DIR}/" \
    "${SERVER_USER}@${SERVER_HOST}:${REMOTE_DIR}/"

echo -e "${GREEN}✅ Файлы синхронизированы${NC}"

# ─── 2. Перезапуск бота на сервере ───────────────────────────
echo -e "\n${YELLOW}🔄 Перезапускаем бота...${NC}"
ssh "${SERVER_HOST}" bash << EOF
    set -e

    # Останавливаем текущий процесс
    if [ -f "${REMOTE_DIR}/bot.pid" ]; then
        PID=\$(cat "${REMOTE_DIR}/bot.pid")
        if kill -0 \$PID 2>/dev/null; then
            echo "Останавливаем бота (PID=\$PID)..."
            kill \$PID
            sleep 3
        fi
        rm -f "${REMOTE_DIR}/bot.pid"
    fi

    # Также ищем по имени процесса на всякий случай
    pkill -f "python.*main.py" 2>/dev/null || true
    sleep 1

    # Запускаем в фоне через nohup
    cd "${REMOTE_DIR}"
    nohup .venv/bin/python main.py > /dev/null 2>&1 &
    NEW_PID=\$!
    sleep 2

    # Проверяем что запустился
    if kill -0 \$NEW_PID 2>/dev/null; then
        echo "✅ Бот запущен (PID=\$NEW_PID)"
    else
        echo "❌ Бот не запустился — проверь логи на сервере"
        exit 1
    fi
EOF

echo -e "\n${GREEN}✅ Деплой завершён!${NC}"
echo -e "Логи на сервере: ssh ${SERVER_USER}@${SERVER_HOST} 'tail -f ${REMOTE_DIR}/bot.log'"
