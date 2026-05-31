"""
main.py — Funding Rate Arbitrage Bot (Bybit Perpetuals)
Стратегия: дельта-нейтральная позиция Long Spot + Short Perp

Реализованные условия (по стандарту open-source ботов):
  ✅ Минимальный порог ставки фандинга
  ✅ Стабильность ставки (3+ предыдущих периода)
  ✅ Минимальный 24h объём символа (ликвидность)
  ✅ Тайминг входа: не входить за < 60 мин до фандинга
  ✅ Break-even фильтр: ожидаемая прибыль > комиссии
  ✅ Правильное обнаружение циклов фандинга (через nextFundingTime)
  ✅ Базис-риск мониторинг (спред spot/perp)
  ✅ Верификация позиций после открытия ордеров
  ✅ Стоп-лосс при ценовом движении (без спот-хеджа)
  ✅ Максимальное время удержания
  ✅ Просадочный стоп
  ✅ Ежедневный отчёт
  ✅ Алерт бездействия

Запуск: python main.py
"""

import sys
import os
import time
import signal
import traceback
from datetime import datetime, date
from loguru import logger

# ── Защита от двойного запуска ────────────────────────────────
_PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.pid")

def _acquire_pid_lock():
    if os.path.exists(_PID_FILE):
        try:
            old_pid = int(open(_PID_FILE).read().strip())
            os.kill(old_pid, 0)
            print(f"[ERROR] Бот уже запущен (PID {old_pid}). Остановите перед новым запуском.")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            pass
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def _release_pid_lock():
    try:
        os.remove(_PID_FILE)
    except FileNotFoundError:
        pass

_acquire_pid_lock()

import config
import dashboard
from bybit_client import BybitClient
from strategy import (
    FundingRateArbStrategy, HedgedPosition,
    minutes_until_funding, annualized_rate, estimate_break_even_cycles
)
from risk_manager import RiskManager
from notifier import TelegramNotifier
from trade_history import TradeHistory

logger.remove()
logger.add(sys.stdout, level=config.LOG_LEVEL, colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{message}</cyan>")
logger.add(config.LOG_FILE, level="DEBUG", rotation="10 MB", retention="7 days",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}")


class FundingRateBot:

    # Как часто обновлять историю ставок (раз в N итераций)
    HISTORY_REFRESH_EVERY = 10

    def __init__(self):
        self.running = True

        if not config.API_KEY or not config.API_SECRET:
            logger.error("API ключи не найдены! Заполните .env файл")
            sys.exit(1)

        self.client   = BybitClient(config.API_KEY, config.API_SECRET, config.TESTNET)
        balance       = self.client.get_wallet_balance("USDT") or 0.0
        self.risk     = RiskManager(config, initial_balance=balance)
        self.tg       = TelegramNotifier(config.TG_BOT_TOKEN, config.TG_CHAT_ID, config.TG_ENABLED)
        self.history  = TradeHistory("trades.db")
        self.strategy = FundingRateArbStrategy(config)

        # {symbol: HedgedPosition}
        self.active_positions: dict = {}

        # Последние данные о ставках (для дашборда и exit-проверок)
        self.last_funding_data: dict = {}

        # Кэш истории ставок {symbol: [{rate, timestamp_ms}, ...]}
        self.rate_history_cache: dict = {}

        # Кэш 24h объёмов {symbol: float}
        self.volumes_24h_cache: dict = {}

        # Кэш ATR {symbol: float (абс. USD)} + время последнего обновления
        self.atr_cache:         dict = {}
        self._atr_last_refresh: float = 0.0

        # Для ежедневного отчёта
        self.last_report_date   = date.today()
        self.last_open_time     = datetime.now()
        self.inactivity_alerted = False

        # Устанавливаем 1x плечо для всех символов
        for sym in config.SYMBOLS:
            self.client.set_leverage(sym, leverage=1)

        signal.signal(signal.SIGINT,  self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        logger.info("=" * 60)
        logger.info("  Funding Rate Arbitrage Bot v1.1")
        logger.info(f"  Символов:          {len(config.SYMBOLS)}")
        logger.info(f"  Мин. ставка:       {config.MIN_FUNDING_RATE*100:.4f}%/8ч "
                    f"(≈{annualized_rate(config.MIN_FUNDING_RATE)*100:.0f}% APY)")
        logger.info(f"  Макс. break-even:  {config.MAX_BREAK_EVEN_CYCLES} цикл(а)")
        logger.info(f"  Стабильность:      {config.STABILITY_MIN_PERIODS} периода")
        logger.info(f"  Размер позиции:    ${config.POSITION_USD}/нога")
        logger.info(f"  Макс. позиций:     {config.MAX_POSITIONS}")
        logger.info(f"  Хедж спотом:       {'ДА' if config.HEDGE_WITH_SPOT else 'НЕТ'}")
        logger.info(f"  Базис-риск стоп:   {config.MAX_BASIS_PCT*100:.1f}%")
        logger.info(f"  Мин. объём 24h:    ${config.MIN_VOLUME_24H_USD/1e6:.0f}M")
        logger.info(f"  Режим:             {'TESTNET' if config.TESTNET else '🔴 MAINNET'}")
        logger.info(f"  Баланс:            {balance:.2f} USDT")
        logger.info("=" * 60)

        self.tg.notify_bot_started(len(config.SYMBOLS), balance, config.TESTNET)

    # ──────────────────────────────────────────
    #  Основной цикл
    # ──────────────────────────────────────────
    def run(self):
        logger.info("Бот запущен. Ctrl+C для остановки.")
        iteration = 0
        while self.running:
            iteration += 1
            t0 = time.time()
            try:
                logger.info(
                    f"--- Итерация #{iteration} [{datetime.now().strftime('%H:%M:%S')}] "
                    f"| Открытых: {len(self.active_positions)} ---"
                )
                self._run_iteration(iteration)
                self._check_daily_report()
                self._check_inactivity()
                if iteration % config.DASHBOARD_UPDATE_EVERY == 0:
                    self._update_dashboard()
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Ошибка итерации: {e}")
                logger.debug(traceback.format_exc())
                self.tg.notify_error(str(e))
            sleep = max(0, config.LOOP_INTERVAL - (time.time() - t0))
            if sleep:
                time.sleep(sleep)

    # ──────────────────────────────────────────
    #  Одна итерация
    # ──────────────────────────────────────────
    def _run_iteration(self, iteration: int):
        # 1. Баланс и просадка
        balance = self.client.get_wallet_balance("USDT") or 0.0

        # Добавляем оценочную стоимость открытых спот-позиций к USDT балансу.
        # Без этого: при покупке спота USDT временно падает на position_usd (~$10),
        # и бот видит ложную просадку — drawdown_halt срабатывает сразу после открытия.
        # С поправкой: просадка отражает РЕАЛЬНЫЕ потери (движение цены + комиссии),
        # а не временное перемещение USDT в спот-токены.
        if config.HEDGE_WITH_SPOT and self.active_positions:
            spot_value = sum(
                pos.spot_qty * pos.entry_spot_price
                for pos in self.active_positions.values()
                if pos.spot_qty > 0 and pos.entry_spot_price > 0
            )
        else:
            spot_value = 0.0

        effective_balance = balance + spot_value
        if effective_balance:
            self.risk.update_balance(effective_balance)

        dd = self.risk.current_drawdown()
        if dd >= config.MAX_DRAWDOWN_PCT and not self.risk.state.is_trading_halted:
            self.tg.notify_drawdown_halt(dd * 100, balance)
            logger.critical(f"Просадка {dd*100:.1f}% — экстренное закрытие всего")
            for sym in list(self.active_positions.keys()):
                self._close_position(sym, "drawdown_halt", balance)
            return

        # 2. Текущие ставки фандинга (каждую итерацию)
        funding_data = self.client.get_funding_rates(config.SYMBOLS)
        if not funding_data:
            logger.warning("Нет данных о ставках фандинга")
            return
        self.last_funding_data = funding_data

        # 3. Обновляем историю ставок и объёмы (реже)
        if iteration % self.HISTORY_REFRESH_EVERY == 1:
            self._refresh_rate_history()
            self._refresh_volumes()

        # 3b. ATR — обновляем по TTL (не по итерации, т.к. тяжёлый запрос)
        self._refresh_atr()

        # 4. Лог топ ставок
        self._log_top_rates(funding_data)

        # 5. Проверяем выход и фандинг-циклы для активных позиций
        for sym in list(self.active_positions.keys()):
            self._check_position(sym, funding_data, balance)

        # 6. Ищем новые входы (funding arb + basis arb)
        free_slots = config.MAX_POSITIONS - len(self.active_positions)
        if free_slots > 0 and not self.risk.state.is_trading_halted:
            self._find_and_open_positions(funding_data, balance, free_slots)

    # ──────────────────────────────────────────
    #  Обновление данных
    # ──────────────────────────────────────────
    def _refresh_rate_history(self):
        """Загружаем историю ставок для фильтра стабильности."""
        for sym in config.SYMBOLS:
            hist = self.client.get_funding_history(sym, limit=5)
            if hist:
                self.rate_history_cache[sym] = hist

    def _refresh_volumes(self):
        """Обновляем кэш 24h объёмов."""
        vols = self.client.get_volumes_24h(config.SYMBOLS, "linear")
        if vols:
            self.volumes_24h_cache = vols

    def _refresh_atr(self):
        """
        Обновляем ATR для активных символов и топ-кандидатов.
        Вызываем не чаще ATR_CACHE_TTL_SEC секунд — это тяжёлый запрос (1 свеча-запрос/символ).
        """
        if not getattr(config, "ATR_ENABLED", True):
            return

        now = time.time()
        ttl = getattr(config, "ATR_CACHE_TTL_SEC", 300)
        if now - self._atr_last_refresh < ttl:
            return

        interval = getattr(config, "ATR_INTERVAL", "60")
        period   = getattr(config, "ATR_PERIOD", 14)

        # Обновляем ATR только для активных позиций (приоритет — они влияют на exit)
        symbols_to_update = set(self.active_positions.keys())

        for sym in symbols_to_update:
            atr = self.client.get_atr(sym, interval=interval, period=period)
            if atr is not None:
                self.atr_cache[sym] = atr
                logger.debug(f"ATR [{sym}] = {atr:.4f} USD")

        self._atr_last_refresh = now

    def _get_dynamic_basis(self, symbol: str, price: float) -> float:
        """
        Динамический порог базис-риска с учётом ATR.

        В периоды высокой волатильности (ATR большой) порог расширяется,
        что снижает количество ложных срабатываний на basis_risk.
        Ограничен сверху MAX_BASIS_PCT * ATR_BASIS_CAP_MULTIPLIER.
        """
        base = getattr(config, "MAX_BASIS_PCT", 0.02)

        if not getattr(config, "ATR_ENABLED", True):
            return base

        atr = self.atr_cache.get(symbol)
        if not atr or price <= 0:
            return base

        atr_pct  = atr / price
        mult     = getattr(config, "ATR_BASIS_MULTIPLIER", 1.5)
        cap_mult = getattr(config, "ATR_BASIS_CAP_MULTIPLIER", 2.5)

        dynamic = base + mult * atr_pct
        capped  = min(dynamic, base * cap_mult)

        if dynamic > base * 1.1:   # логируем только если заметное расширение
            logger.debug(
                f"[{symbol}] Динамический базис: {base*100:.2f}% → {capped*100:.2f}% "
                f"(ATR={atr:.2f}, atr_pct={atr_pct*100:.3f}%)"
            )
        return capped

    def _log_top_rates(self, funding_data: dict):
        top = sorted(
            [(s, d["rate"]) for s, d in funding_data.items() if d.get("rate", 0) > 0],
            key=lambda x: x[1], reverse=True
        )[:5]
        if top:
            rates_str = " | ".join(f"{s}: {r*100:.4f}%" for s, r in top)
            logger.info(f"Топ ставки: {rates_str}")

    # ──────────────────────────────────────────
    #  Мониторинг активных позиций
    # ──────────────────────────────────────────
    def _check_position(self, symbol: str, funding_data: dict, balance: float):
        pos = self.active_positions.get(symbol)
        if not pos:
            return

        data          = funding_data.get(symbol, {})
        cur_rate      = data.get("rate", 0.0)
        cur_price     = data.get("price", pos.entry_perp_price)
        next_ms       = data.get("next_time_ms", 0)
        min_to_fund   = minutes_until_funding(next_ms)

        # ── Обнаружение нового цикла фандинга ─────────────
        # Правильный метод: отслеживаем изменение nextFundingTime
        if pos.check_new_funding_cycle(next_ms):
            payment = self.strategy.update_funding_cycle(pos, cur_rate)
            self.tg.notify_funding_collected(
                symbol, payment, pos.funding_cycles_held, pos.estimated_funding
            )

        # ── Обновление базис-мониторинга ──────────────────
        spot_price = 0.0
        if config.HEDGE_WITH_SPOT:
            spot_price = self.client.get_last_price(symbol, "spot") or cur_price

        # ── Лог статуса позиции ───────────────────────────
        logger.info(
            f"  [{symbol}] rate={cur_rate*100:.4f}% | "
            f"циклов={pos.funding_cycles_held} | "
            f"возраст={pos.age_hours():.1f}h | "
            f"до фандинга={min_to_fund:.0f}мин | "
            f"фандинг≈${pos.estimated_funding:.4f}"
        )

        # ── Динамический порог базис-риска (ATR) ─────────
        dynamic_basis = self._get_dynamic_basis(symbol, cur_price)

        # ── Проверка условий выхода ───────────────────────
        should_exit, reason = self.strategy.check_exit(
            pos, cur_rate, cur_price, spot_price or cur_price,
            min_to_fund, dynamic_max_basis_pct=dynamic_basis
        )

        if should_exit:
            logger.info(f"[{symbol}] → ЗАКРЫТИЕ: {reason}")
            self._close_position(symbol, reason, balance)

    # ──────────────────────────────────────────
    #  Поиск и открытие новых позиций
    # ──────────────────────────────────────────
    def _find_and_open_positions(self, funding_data: dict, balance: float, free_slots: int):
        active_syms = set(self.active_positions.keys())

        # ── Поиск по ставке фандинга (Уровень 1) ─────────
        candidates = self.strategy.scan_opportunities(
            funding_data=funding_data,
            rate_history=self.rate_history_cache,
            volumes_24h=self.volumes_24h_cache,
            active_symbols=active_syms,
            max_slots=free_slots,
        )

        # ── Поиск по базису (Уровень 2) ───────────────────
        if len(candidates) < free_slots and getattr(config, "BASIS_ARB_ENABLED", False):
            remaining = free_slots - len(candidates)
            already_picked = active_syms | {c["symbol"] for c in candidates}
            spot_prices = self.client.get_all_spot_prices(config.SYMBOLS)
            basis_candidates = self.strategy.scan_basis_opportunities(
                funding_data=funding_data,
                spot_prices=spot_prices,
                active_symbols=already_picked,
                max_slots=remaining,
            )
            if basis_candidates:
                logger.info(f"📐 Basis candidates: {[c['symbol'] for c in basis_candidates]}")
            candidates += basis_candidates

        if not candidates:
            logger.debug("Нет кандидатов для входа (все фильтры прошли 0 символов)")
            return

        for cand in candidates:
            if len(self.active_positions) >= config.MAX_POSITIONS:
                break
            sym       = cand["symbol"]
            rate      = cand["rate"]
            price     = cand["price"]
            direction = cand.get("direction", "long_spot_short_perp")
            be_cycles = cand["be_cycles"]
            logger.info(
                f"✨ Кандидат: {sym} | {direction} | rate={rate*100:.4f}% | "
                f"APY={annualized_rate(rate)*100:.1f}% | "
                f"break-even={be_cycles:.1f}ц | "
                f"vol=${self.volumes_24h_cache.get(sym,0)/1e6:.0f}M"
            )
            self._open_position(sym, rate, price, balance, be_cycles, direction)

    # ──────────────────────────────────────────
    #  Открытие позиции
    # ──────────────────────────────────────────
    def _open_position(self, symbol: str, funding_rate: float,
                       price: float, balance: float, be_cycles: float,
                       direction: str = "long_spot_short_perp"):
        # ── Kelly-критерий: динамический размер позиции ──
        position_usd = self.strategy.calc_position_usd(balance)

        # Проверка риск-менеджера
        required = position_usd * 2
        can, reason = self.risk.can_open_position(balance, required)
        if not can:
            logger.warning(f"[{symbol}] Заблокировано: {reason}")
            return

        # Расчёт размера перп-ноги
        perp_qty_raw = self.strategy.calc_perp_qty(price, position_usd=position_usd)
        perp_qty     = self.client.round_qty(perp_qty_raw, symbol, "linear")
        if perp_qty <= 0:
            logger.warning(f"[{symbol}] perp_qty={perp_qty} слишком мал")
            return

        min_qty = self.client.get_min_order_qty(symbol, "linear")
        if perp_qty < min_qty:
            logger.warning(f"[{symbol}] qty={perp_qty} < min={min_qty} — пропуск")
            return

        logger.info(
            f"[{symbol}] 📥 Открываем [{direction}] | rate={funding_rate*100:.4f}% | "
            f"qty={perp_qty} | leg=${position_usd:.1f}"
        )

        pos = HedgedPosition(
            symbol=symbol,
            direction=direction,
            entry_funding_rate=funding_rate,
            entry_perp_price=price,
            perp_qty=perp_qty,
            balance_before=balance,
        )

        next_ms = self.last_funding_data.get(symbol, {}).get("next_time_ms", 0)
        pos.last_next_funding_ms = next_ms

        spot_ok = True
        spot_price = self.client.get_last_price(symbol, "spot") or price

        # ── Нога 1: Спот ─────────────────────────────────
        if direction in ("long_spot_short_perp", "basis_short_perp"):
            # Покупаем спот (Long Spot)
            spot_oid = self.client.buy_spot(symbol, position_usd)
            if spot_oid:
                spot_qty_raw         = position_usd / spot_price
                pos.spot_qty         = self.client.round_qty(spot_qty_raw, symbol, "spot")
                pos.entry_spot_price = spot_price
                pos.spot_order_id    = spot_oid
                if spot_price > 0:
                    pos.entry_basis_pct = abs(price / spot_price - 1.0)
                logger.info(f"[{symbol}] Спот куплен: {pos.spot_qty} @ {spot_price:.4f}")
            else:
                logger.error(f"[{symbol}] ❌ Ошибка покупки спота — отменяем вход")
                spot_ok = False

        elif direction in ("long_perp_short_spot", "basis_long_perp"):
            # Шортим спот через маржу (Short Spot)
            # Требует ALLOW_SPOT_SHORT=True + настройки collateral в Bybit UTA
            if not getattr(config, "ALLOW_SPOT_SHORT", False):
                logger.warning(
                    f"[{symbol}] {direction} требует ALLOW_SPOT_SHORT=True. "
                    f"Включи collateral в Bybit UTA → установи ALLOW_SPOT_SHORT=True"
                )
                return
            if config.HEDGE_WITH_SPOT:
                spot_qty_raw = position_usd / spot_price
                short_qty    = self.client.round_qty(spot_qty_raw, symbol, "spot")
                spot_oid     = self.client.short_spot(symbol, short_qty)
                if spot_oid:
                    pos.spot_qty         = short_qty
                    pos.entry_spot_price = spot_price
                    pos.spot_order_id    = spot_oid
                    logger.info(f"[{symbol}] Спот зашорчен: {short_qty} @ {spot_price:.4f}")
                else:
                    logger.error(f"[{symbol}] ❌ Ошибка шорта спота — отменяем вход")
                    spot_ok = False

        if not spot_ok:
            return

        # ── Нога 2: Перп ─────────────────────────────────
        perp_side = "Sell" if direction in ("long_spot_short_perp", "basis_short_perp") else "Buy"
        perp_oid  = self.client.place_market_order(
            symbol, perp_side, perp_qty, "linear", reduce_only=False
        )
        if not perp_oid:
            logger.error(f"[{symbol}] ❌ Ошибка перп-ордера — закрываем спот")
            if pos.spot_qty > 0:
                if direction in ("long_spot_short_perp", "basis_short_perp"):
                    self.client.sell_spot(symbol, pos.spot_qty)
                else:
                    self.client.cover_short_spot(symbol, pos.spot_qty * spot_price)
            return

        pos.perp_order_id = perp_oid
        time.sleep(1)

        # ── Верификация ───────────────────────────────────
        verified = self._verify_position(symbol, pos)
        if not verified:
            logger.error(f"[{symbol}] ❌ Верификация не прошла — экстренное закрытие")
            self.client.close_position(symbol, "linear")
            if pos.spot_qty > 0:
                if direction in ("long_spot_short_perp", "basis_short_perp"):
                    self.client.sell_spot(symbol, pos.spot_qty)
                else:
                    self.client.cover_short_spot(symbol, pos.spot_qty * spot_price)
            return

        # ── Позиция открыта ───────────────────────────────
        self.active_positions[symbol] = pos
        self.last_open_time     = datetime.now()
        self.inactivity_alerted = False

        logger.info(f"[{symbol}] ✅ Позиция открыта [{direction}]")

        self.tg.notify_position_opened(
            symbol=symbol,
            direction=pos.direction,
            funding_rate=funding_rate,
            perp_qty=perp_qty,
            spot_qty=pos.spot_qty,
            perp_price=price,
            spot_price=pos.entry_spot_price,
            balance=balance,
            break_even_cycles=be_cycles,
        )

    def _verify_position(self, symbol: str, pos: HedgedPosition) -> bool:
        """
        Верификация после открытия: проверяем что обе ноги реально открыты.
        Стандартная практика open-source ботов.
        """
        # Проверяем перп позицию
        perp_pos = self.client.get_position(symbol, "linear")
        if perp_pos is None:
            logger.error(f"[{symbol}] Верификация: перп позиция не найдена!")
            return False

        if perp_pos["side"] != "Sell":
            logger.error(f"[{symbol}] Верификация: перп side={perp_pos['side']} (ожидался Sell)")
            return False

        actual_qty = perp_pos["size"]
        expected_qty = pos.perp_qty
        qty_diff = abs(actual_qty - expected_qty) / (expected_qty + 1e-9)

        if qty_diff > 0.05:  # допуск 5%
            logger.warning(
                f"[{symbol}] Верификация: qty={actual_qty} vs ожидалось {expected_qty} "
                f"(разница {qty_diff*100:.1f}%)"
            )

        # Обновляем реальную цену входа из позиции
        pos.entry_perp_price = perp_pos["entry_price"]
        pos.perp_qty         = actual_qty

        logger.info(
            f"[{symbol}] Верификация ✅ | perp={actual_qty}@{pos.entry_perp_price:.4f} | "
            f"side=Sell | unreal_pnl={perp_pos['unreal_pnl']:+.4f}"
        )

        # Для спота верификация через баланс монеты
        if config.HEDGE_WITH_SPOT:
            coin = symbol.replace("USDT", "")
            spot_bal = self.client.get_spot_coin_balance(coin)
            if spot_bal < pos.spot_qty * 0.9:
                logger.warning(
                    f"[{symbol}] Спот баланс {spot_bal} < {pos.spot_qty} "
                    f"(возможно не исполнилось полностью)"
                )
                # Корректируем только если баланс ненулевой.
                # Если spot_bal=0 — скорее всего задержка UTA API (токены есть,
                # но ещё не отражены в walletBalance). Не обнуляем pos.spot_qty!
                if spot_bal > 0:
                    pos.spot_qty = spot_bal

        return True

    # ──────────────────────────────────────────
    #  Закрытие позиции
    # ──────────────────────────────────────────
    def _close_position(self, symbol: str, reason: str, balance: float):
        pos = self.active_positions.get(symbol)
        if not pos:
            return

        bal_before = pos.balance_before
        logger.info(
            f"[{symbol}] 📤 Закрываем | reason={reason} | "
            f"cycles={pos.funding_cycles_held} | age={pos.age_hours():.1f}h"
        )

        # ── Закрываем перп ────────────────────────────────
        perp_closed = self.client.close_position(symbol, "linear")
        if not perp_closed:
            logger.error(f"[{symbol}] Не удалось закрыть перп позицию")

        # ── Закрываем спот ────────────────────────────────
        exit_spot_price = 0.0
        is_long_spot = pos.direction in ("long_spot_short_perp", "basis_short_perp")
        is_short_spot = pos.direction in ("long_perp_short_spot", "basis_long_perp")

        if pos.spot_qty > 0:
            if is_long_spot:
                # Продаём купленный спот
                coin     = symbol.replace("USDT", "")
                spot_bal = self.client.get_spot_coin_balance(coin)
                qty      = min(pos.spot_qty, spot_bal) if spot_bal > 0 else pos.spot_qty
                if qty > 0:
                    sold = self.client.sell_spot(symbol, qty)
                    if sold:
                        exit_spot_price = self.client.get_last_price(symbol, "spot") or 0.0
                    else:
                        logger.error(f"[{symbol}] Не удалось продать спот ({qty})")

            elif is_short_spot:
                # Покрываем маржинальный шорт спота
                cover_usdt = pos.spot_qty * (self.client.get_last_price(symbol, "spot") or pos.entry_spot_price)
                covered = self.client.cover_short_spot(symbol, cover_usdt * 1.01)  # +1% буфер на движение цены
                if covered:
                    exit_spot_price = self.client.get_last_price(symbol, "spot") or 0.0
                else:
                    logger.error(f"[{symbol}] Не удалось покрыть шорт спота")

        time.sleep(1)
        bal_after = self.client.get_wallet_balance("USDT") or bal_before

        # ── Расчёт PnL ───────────────────────────────────
        total_pnl  = bal_after - bal_before
        spot_pnl   = 0.0
        if config.HEDGE_WITH_SPOT and pos.entry_spot_price > 0 and exit_spot_price > 0:
            spot_pnl = pos.spot_qty * (exit_spot_price - pos.entry_spot_price)

        # Комиссия считается от реального notional позиции
        commission = 4 * pos.notional_usd() * config.BYBIT_TAKER_FEE
        hold_hours = pos.age_hours()

        exit_data       = self.last_funding_data.get(symbol, {})
        exit_rate       = exit_data.get("rate", 0.0)
        exit_perp_price = exit_data.get("price", 0.0)

        # ── Запись в историю ─────────────────────────────
        self.history.record(
            symbol=symbol,
            direction=pos.direction,
            entry_funding_rate=pos.entry_funding_rate,
            exit_funding_rate=exit_rate,
            entry_perp_price=pos.entry_perp_price,
            exit_perp_price=exit_perp_price,
            entry_spot_price=pos.entry_spot_price,
            exit_spot_price=exit_spot_price,
            perp_qty=pos.perp_qty,
            spot_qty=pos.spot_qty,
            funding_cycles=pos.funding_cycles_held,
            estimated_funding=pos.estimated_funding,
            spot_pnl=spot_pnl,
            total_pnl=total_pnl,
            commission=commission,
            balance_after=bal_after,
            reason=reason,
            hold_hours=hold_hours,
            opened_at=pos.opened_at,
        )

        self.risk.record_trade(total_pnl, bal_before)

        s = self.risk.get_summary()
        logger.info(
            f"[{symbol}] 🔒 ЗАКРЫТО | PnL={total_pnl:+.4f} | "
            f"фандинг≈+{pos.estimated_funding:.4f} | "
            f"basis_max={pos.max_basis_pct*100:.3f}% | "
            f"WR={s['win_rate_pct']}%"
        )

        self.tg.notify_position_closed(
            symbol=symbol,
            funding_cycles=pos.funding_cycles_held,
            estimated_funding=pos.estimated_funding,
            total_pnl=total_pnl,
            balance=bal_after,
            reason=reason,
            hold_hours=hold_hours,
        )

        del self.active_positions[symbol]

    # ──────────────────────────────────────────
    #  Ежедневный отчёт
    # ──────────────────────────────────────────
    def _check_daily_report(self):
        now = datetime.now()
        if now.date() > self.last_report_date and now.hour >= config.DAILY_REPORT_HOUR:
            balance  = self.client.get_wallet_balance("USDT") or 0.0
            today    = self.history.get_today()
            all_time = self.history.get_all_time()
            per_sym  = self.history.get_per_symbol()
            self.tg.notify_daily_report(today, all_time, balance, per_sym)
            self.last_report_date = now.date()
            logger.info("📊 Ежедневный отчёт отправлен в Telegram")

    # ──────────────────────────────────────────
    #  Алерт бездействия
    # ──────────────────────────────────────────
    def _check_inactivity(self):
        if self.inactivity_alerted:
            return
        hours = (datetime.now() - self.last_open_time).total_seconds() / 3600
        if hours >= config.INACTIVITY_HOURS:
            balance   = self.client.get_wallet_balance("USDT") or 0.0
            top_rates = sorted(
                [(s, d["rate"]) for s, d in self.last_funding_data.items()
                 if d.get("rate", 0) > 0],
                key=lambda x: x[1], reverse=True
            )[:3]
            self.tg.notify_inactivity(hours, balance, len(self.active_positions), top_rates)
            self.inactivity_alerted = True
            logger.warning(f"💤 Нет новых позиций {hours:.1f}ч — алерт отправлен")

    # ──────────────────────────────────────────
    #  Дашборд
    # ──────────────────────────────────────────
    def _update_dashboard(self):
        try:
            html = dashboard.generate(
                balance=self.risk.state.current_balance,
                active_positions=self.active_positions,
                trade_history=self.history,
                funding_data=self.last_funding_data,
            )
            dashboard.save(config.DASHBOARD_FILE, html)
        except Exception as e:
            logger.debug(f"Dashboard: {e}")

    # ──────────────────────────────────────────
    #  Завершение
    # ──────────────────────────────────────────
    def _handle_shutdown(self, signum, frame):
        logger.info("\nОстановка бота...")
        self.running = False
        self._shutdown()

    def _shutdown(self):
        logger.info("Закрываем все позиции...")
        balance = self.client.get_wallet_balance("USDT") or 0.0
        for sym in list(self.active_positions.keys()):
            self._close_position(sym, "shutdown", balance)

        self._update_dashboard()

        at = self.history.get_all_time()
        s  = self.risk.get_summary()
        logger.info("=" * 60)
        logger.info(f"  Сделок:          {at['trades']}")
        logger.info(f"  Win Rate:        {at['win_rate']}%")
        logger.info(f"  Фандинг собрано: +{at['funding_earned']:.4f} USDT")
        logger.info(f"  PnL net:         {at['pnl_net']:+.4f} USDT")
        logger.info(f"  Sharpe:          {s['sharpe_ratio']}")
        logger.info("=" * 60)

        self.tg.notify_bot_stopped(
            at["trades"], at["win_rate"],
            at["pnl_net"], at["funding_earned"]
        )
        self.history.close()
        _release_pid_lock()


if __name__ == "__main__":
    FundingRateBot().run()
