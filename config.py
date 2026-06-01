"""
config.py — Конфигурация бота Funding Rate Arbitrage
Стратегия: дельта-нейтральный сбор фандинга (Long Spot + Short Perp)
"""

import os
from dotenv import load_dotenv

load_dotenv(override=True)

# ─────────────────────────────────────────────
#  BYBIT API
# ─────────────────────────────────────────────
API_KEY    = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TESTNET    = os.getenv("TESTNET", "true").lower() == "true"

# ─────────────────────────────────────────────
#  ТОРГОВЫЕ ИНСТРУМЕНТЫ
# ─────────────────────────────────────────────
CATEGORY = "linear"

# Символы для сканирования ставок фандинга
# Пересобрано май 2026: добавлены TON, TRUMP, FARTCOIN, PNUT, VIRTUAL, HYPE, ONDO, RENDER
SYMBOLS = [
    # ── Топ по капитализации (всегда ликвидны) ────────────────────
    "BTCUSDT",   "ETHUSDT",   "SOLUSDT",   "BNBUSDT",   "XRPUSDT",
    "SUIUSDT",   "ADAUSDT",   "AVAXUSDT",  "DOTUSDT",   "LTCUSDT",

    # ── DeFi / L1 / L2 (стабильная ликвидность) ──────────────────
    "LINKUSDT",  "AAVEUSDT",  "UNIUSDT",   "INJUSDT",   "ATOMUSDT",
    "APTUSDT",   "NEARUSDT",  "ARBUSDT",   "OPUSDT",    "SEIUSDT",
    "PENDLEUSDT","LDOUSDT",   "GMXUSDT",

    # ── TON (активен в 2026, высокий интерес лонгистов) ──────────
    "TONUSDT",

    # ── AI / Agent нарратив (пиковые ставки при AI-хайпе) ────────
    # FETUSDT убран — контракт не активен (FET→ASI ребрендинг)
    "TAOUSDT",   "RENDERUSDT","VIRTUALUSDT","ENAUSDT",   "WLDUSDT",

    # ── RWA / Институциональный нарратив ─────────────────────────
    "ONDOUSDT",

    # ── Hyperliquid — самый горячий токен 2025-2026 ───────────────
    "HYPEUSDT",

    # ── Политические мемы (экстремальные ставки в хайп) ──────────
    "TRUMPUSDT",

    # ── Solana-мемы (исторически самые высокие ставки) ───────────
    # Примечание: SHIB/PEPE/BONK/FLOKI торгуются с префиксом 1000 на Bybit
    "DOGEUSDT",      "1000PEPEUSDT", "WIFUSDT",   "1000BONKUSDT",
    "1000FLOKIUSDT", "POPCATUSDT",   "PNUTUSDT",     "FARTCOINUSDT",

    # ── Прочие с историей повышенных ставок ──────────────────────
    "JUPUSDT",   "EIGENUSDT", "TIAUSDT",   "ARKMUSDT",
    "ORDIUSDT",  "GALAUSDT",  "SANDUSDT",  "IMXUSDT",
    "PYTHUSDT",  "MOVEUSDT",
]

# ─────────────────────────────────────────────
#  ПАРАМЕТРЫ СТРАТЕГИИ
# ─────────────────────────────────────────────

# --- Пороги ставки фандинга ---
# Bybit платит фандинг каждые 8 часов (00:00, 08:00, 16:00 UTC)
# Bybit дефолтная ставка = 0.01% / 8h. Входим только если ставка ВЫШЕ дефолта.
# 0.00015 = 0.015% / 8h ≈ 16.4% APY   ← текущий реалистичный рынок
# 0.0003  = 0.030% / 8h ≈ 32.9% APY   ← бычий рынок / хайп
MIN_FUNDING_RATE     = 0.00015  # понижено с 0.0003 — рынок в режиме низких ставок
EXIT_FUNDING_RATE    = 0.00006  # закрыть если ставка упала ниже (0.006% / 8h)
MIN_HOLD_CYCLES      = 2        # держать минимум N циклов фандинга (= N×8 часов)
MAX_HOLD_HOURS       = 96       # принудительно закрыть через N часов (4 дня)

# --- Уровень 1: Отрицательные ставки (long_perp_short_spot) ---
# Когда шортисты доминируют — они платят лонгистам.
# Мы открываем лонг перп + шорт спот → получаем фандинг в обратную сторону.
# Удваивает количество сигналов. Требует маржинальный шорт спота (isLeverage=1).
NEGATIVE_FUNDING_ENABLED  = True
MIN_NEGATIVE_FUNDING_RATE = -0.00015  # войти когда rate ≤ -0.015%/8h
EXIT_NEGATIVE_FUNDING_RATE = -0.00006  # выйти когда rate ≥ -0.006%/8h

# Шорт спота требует включения collateral в Bybit UTA:
# Assets → Unified Trading → Collateral Settings → включить нужные монеты
# True безопасно даже до настройки UI: main.py проверяет collateral через API
# (get_collateral_info) и пропускает монеты без collateral + нерентабельный borrow,
# вместо падения с ErrCode 170037.
ALLOW_SPOT_SHORT = True

# --- Уровень 2: Basis Mean Reversion ---
# Торгуем схождение спреда между перпом и спотом независимо от ставки фандинга.
# Когда perp > spot на X% → это аномалия, обычно схлопывается за часы.
# Даёт сигналы ДАЖЕ когда ставки низкие — деплоит простаивающий капитал.
BASIS_ARB_ENABLED    = True
# Порог входа должен покрывать round-trip комиссии (4×0.055%=0.22%) с запасом,
# иначе вход на шуме → закрытие за 0.0ч в минус на фиях. 0.5%: капчим
# (0.5−0.05)=0.45% gross − 0.22% fee = +0.23% net.  (было 0.3% — слишком тонко)
BASIS_ENTRY_PCT      = 0.005   # войти если |perp/spot - 1| > 0.5%
BASIS_EXIT_PCT       = 0.0005  # выйти когда базис < 0.05% (схлопнулся)
BASIS_MAX_HOLD_HOURS = 24      # принудительное закрытие через 24ч
# Подтверждать базис по mid-ценам ордербука перед входом (last-цены шумят на
# тиках → ложный базис). Нет данных ордербука — не входим.
BASIS_CONFIRM_WITH_ORDERBOOK = True

# --- Позиция ---
POSITION_USD         = 25.0     # USD на каждую ногу (fallback если Kelly отключён)
MAX_POSITIONS        = 5        # макс. одновременных позиций (было 2 → утилизация 6%→30%)

# --- Kelly-критерий (динамический размер позиции) ---
# Размер ноги = KELLY_FRACTION * баланс, ограничен [MIN, MAX]
# Пример: баланс $500  → 500 * 0.03 = $15   → используем $15
#          баланс $2000 → 2000 * 0.03 = $60  → используем $60
#          баланс $10000 → 10000 * 0.03 = $300 → clamp → $300 (MAX)
KELLY_ENABLED        = True
KELLY_FRACTION       = 0.03     # 3% баланса на одну ногу (было 1.5% — удваиваем размер позиции)
POSITION_USD_MIN     = 10.0     # минимальный размер ноги, USD
POSITION_USD_MAX     = 300.0    # максимальный размер ноги, USD (было 150)

# --- Диверсификация по категориям (защита от корреляции) ---
# Мемкоины коррелируют друг с другом — при крипто-дампе падают все вместе.
# Ограничиваем кол-во позиций в одной категории.
MAX_POSITIONS_PER_CATEGORY = 2   # макс. позиций в одной категории одновременно

SYMBOL_CATEGORIES = {
    "meme": [
        "DOGEUSDT", "1000PEPEUSDT", "WIFUSDT", "1000BONKUSDT",
        "1000FLOKIUSDT", "POPCATUSDT", "PNUTUSDT", "FARTCOINUSDT", "TRUMPUSDT",
    ],
    "ai": [
        "TAOUSDT", "RENDERUSDT", "VIRTUALUSDT", "ENAUSDT", "WLDUSDT",
    ],
    "defi": [
        "LINKUSDT", "AAVEUSDT", "UNIUSDT", "INJUSDT", "PENDLEUSDT",
        "LDOUSDT", "GMXUSDT", "JUPUSDT",
    ],
    "l1_l2": [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "SUIUSDT",
        "ADAUSDT", "AVAXUSDT", "TONUSDT", "NEARUSDT", "ARBUSDT", "OPUSDT",
        "APTUSDT", "ATOMUSDT", "DOTUSDT", "LTCUSDT", "SEIUSDT",
    ],
    "other": [
        "EIGENUSDT", "TIAUSDT", "ARKMUSDT", "ORDIUSDT", "GALAUSDT",
        "SANDUSDT", "IMXUSDT", "PYTHUSDT", "MOVEUSDT", "ONDOUSDT", "HYPEUSDT",
    ],
}

# --- Режим хеджирования ---
# True  → Лонг спот + Шорт перп (дельта-нейтральный, рекомендуется)
# False → Только шорт перп (проще, но есть ценовой риск)
HEDGE_WITH_SPOT      = True

# --- Стоп-лосс (только при HEDGE_WITH_SPOT=False или сбое хеджа) ---
STOP_LOSS_PCT        = 0.03     # 3% движение цены → принудительное закрытие

# --- Тайминг ---
# Не входить если до следующего фандинга меньше N минут (недостаточно окна)
MIN_MINUTES_BEFORE_FUNDING_FOR_ENTRY = 60  # мин. 1 час до фандинга для нового входа
# Закрыть позицию если до фандинга осталось < N минут И ставка низкая
CLOSE_BEFORE_FUNDING_MIN = 10

# --- Стабильность ставки ---
# Из последних N периодов минимум N-1 должны быть того же знака
STABILITY_MIN_PERIODS    = 2      # проверяем 2 последних периода (было 3)

# --- Окупаемость ---
# При ставке 0.015%: break-even = 4 × fee / rate = 4 × 0.055% / 0.015% = 14.7 цикла
# Ставим 18 чтобы был запас (минимальный Kelly $10: break-even не зависит от size!)
MAX_BREAK_EVEN_CYCLES    = 18     # повышено с 12 (рынок низких ставок — дольше окупаем)
# Окупаемость во ВРЕМЕНИ, а не в циклах: 1ч-фандинг окупается в 8× быстрее, чем 8ч.
# Фильтр меряет be_cycles × interval_h ≤ этого порога. 144ч = 18 циклов × 8ч (старое
# поведение для 8ч-монет), но 1ч/4ч-монеты с тем же be_cycles проходят легко.
MAX_BREAK_EVEN_HOURS     = 144

# Защита от фиксации убытка: НЕ выходить по «затуханию» ставки, пока накопленный
# фандинг не покрыл round-trip комиссии. Адверсный флип знака всё равно режется сразу.
# Без этого бот ловит 2 цикла, выходит при нормализации ставки и фиксирует −fee.
EXIT_ONLY_AFTER_BREAKEVEN = True

# --- Базис-риск (спред спот vs перп) ---
# Выйти если базис (|perp/spot - 1|) вырос выше этого порога
MAX_BASIS_PCT            = 0.02   # 2.0% базовый порог (было 1.5% — слишком строго)

# --- Динамический базис-риск через ATR ---
# dynamic_basis = MAX_BASIS_PCT + ATR_BASIS_MULTIPLIER * (ATR / price)
# В периоды высокой волатильности порог расширяется — меньше ложных выходов
# Ограничен сверху MAX_BASIS_PCT * ATR_BASIS_CAP_MULTIPLIER
ATR_ENABLED              = True
ATR_INTERVAL             = "60"   # таймфрейм свечей для ATR (60 мин = 1h)
ATR_PERIOD               = 14     # период ATR (стандарт)
ATR_BASIS_MULTIPLIER     = 1.5    # коэффициент влияния ATR на порог
ATR_BASIS_CAP_MULTIPLIER = 2.5    # максимум = MAX_BASIS_PCT * 2.5 (не даём уйти слишком далеко)
ATR_CACHE_TTL_SEC        = 300    # обновлять ATR-кэш каждые 5 минут

# --- Объём ---
MIN_VOLUME_24H_USD       = 3_000_000   # $3M мин. 24ч оборот (было $10M)

# ─────────────────────────────────────────────
#  РИСК-МЕНЕДЖМЕНТ
# ─────────────────────────────────────────────
MAX_DRAWDOWN_PCT     = 0.20     # 20% максимальная просадка (было 10% — слишком чувствительно при spot purchases)
BYBIT_TAKER_FEE      = 0.00055  # 0.055% taker fee
# Брейкер «убытков подряд» считает только МАТЕРИАЛЬНЫЕ потери (≥ этой доли баланса).
# Копеечные basis-минусы на комиссиях не должны глушить торговлю до рестарта.
LOSS_STREAK_MIN_LOSS_PCT = 0.005   # 0.5% от entry-баланса

# ─────────────────────────────────────────────
#  НАСТРОЙКИ БОТА
# ─────────────────────────────────────────────
LOOP_INTERVAL        = 30       # секунд между итерациями
LOG_LEVEL            = "INFO"
LOG_FILE             = "bot.log"

# ─────────────────────────────────────────────
#  ДАШБОРД
# ─────────────────────────────────────────────
DASHBOARD_FILE          = "dashboard.html"
DASHBOARD_UPDATE_EVERY  = 10    # обновлять каждые N итераций

# ─────────────────────────────────────────────
#  ЕЖЕДНЕВНЫЙ ОТЧЁТ
# ─────────────────────────────────────────────
DAILY_REPORT_HOUR       = 9     # отправлять отчёт в 09:00
INACTIVITY_HOURS        = 12    # алерт если нет новых сделок N часов

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID   = os.getenv("TG_CHAT_ID", "")
TG_ENABLED   = os.getenv("TG_ENABLED", "true").lower() == "true"
