#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  start_bot.sh — Запуск Bybit Arbitrage Bot
#  Использование: bash start_bot.sh
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
MAIN="$SCRIPT_DIR/main.py"
LOG="$SCRIPT_DIR/bot.log"

# Если venv не найден — пробуем системный python3
if [ ! -f "$PYTHON" ]; then
    PYTHON=$(which python3)
fi

echo "======================================"
echo "  Bybit Arbitrage Bot"
echo "  Python: $PYTHON"
echo "  Script: $MAIN"
echo "  Log:    $LOG"
echo "======================================"

cd "$SCRIPT_DIR"
"$PYTHON" "$MAIN"
