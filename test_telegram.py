"""
test_telegram.py — Диагностика Telegram уведомлений
Запуск: python test_telegram.py
"""

import requests
from dotenv import load_dotenv
import os

load_dotenv(override=True)

TOKEN   = os.getenv("TG_BOT_TOKEN", "")
CHAT_ID = os.getenv("TG_CHAT_ID", "")
ENABLED = os.getenv("TG_ENABLED", "true")

print("=" * 50)
print("  Диагностика Telegram")
print("=" * 50)
print(f"TG_ENABLED:  {ENABLED}")
print(f"TG_BOT_TOKEN: {TOKEN[:20]}..." if TOKEN else "TG_BOT_TOKEN: ❌ НЕ НАЙДЕН!")
print(f"TG_CHAT_ID:  {CHAT_ID}" if CHAT_ID else "TG_CHAT_ID:  ❌ НЕ НАЙДЕН!")
print()

if not TOKEN or not CHAT_ID:
    print("❌ Заполните TG_BOT_TOKEN и TG_CHAT_ID в .env")
    exit(1)

# Шаг 1: Проверка доступности api.telegram.org
print("Шаг 1: Проверка соединения с api.telegram.org...")
try:
    r = requests.get("https://api.telegram.org", timeout=5)
    print(f"  ✅ Соединение OK (статус {r.status_code})")
except Exception as e:
    print(f"  ❌ Нет доступа: {e}")
    print("  → Включите VPN и запустите снова")
    exit(1)

# Шаг 2: Проверка токена через getMe
print("\nШаг 2: Проверка токена бота (getMe)...")
try:
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=10)
    data = r.json()
    if data.get("ok"):
        bot = data["result"]
        print(f"  ✅ Бот найден: @{bot.get('username')} ({bot.get('first_name')})")
    else:
        print(f"  ❌ Неверный токен: {data.get('description')}")
        print("  → Проверьте TG_BOT_TOKEN в .env")
        exit(1)
except Exception as e:
    print(f"  ❌ Ошибка: {e}")
    exit(1)

# Шаг 3: Отправка тестового сообщения
print(f"\nШаг 3: Отправка сообщения в chat_id={CHAT_ID}...")
try:
    url  = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id":    CHAT_ID,
        "text":       "✅ <b>Тест успешен!</b>\nБот подключён и уведомления работают 🚀",
        "parse_mode": "HTML",
    }, timeout=10)

    data = resp.json()
    print(f"  HTTP статус: {resp.status_code}")
    print(f"  Ответ API:   {data}")

    if data.get("ok"):
        print("\n✅ УСПЕХ! Проверьте сообщение в Telegram.")
    else:
        code = data.get("error_code")
        desc = data.get("description", "")
        print(f"\n❌ Ошибка {code}: {desc}")
        if code == 400 and "chat not found" in desc.lower():
            print("  → Напишите боту /start в Telegram и попробуйте снова")
        elif code == 403:
            print("  → Бот заблокирован. Напишите боту /start в Telegram")
        elif code == 400:
            print("  → Неверный CHAT_ID. Запустите get_chat_id.py")

except requests.exceptions.ConnectionError as e:
    print(f"  ❌ Нет соединения: {e}")
    print("  → Включите VPN")
except requests.exceptions.Timeout:
    print("  ❌ Таймаут — сервер не отвечает. Попробуйте другой VPN сервер")
except Exception as e:
    print(f"  ❌ Неизвестная ошибка: {e}")
