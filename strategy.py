"""
strategy.py — Funding Rate Arbitrage Strategy
Дельта-нейтральная позиция: Long Spot + Short Perp (положительный фандинг)
                             Short Spot + Long Perp  (отрицательный фандинг)

Условия реализованы по стандарту open-source ботов:
  github.com/IrakliXYZ/ARBOT
  github.com/aoki-h-jp/funding-rate-arbitrage
  coincryptorank.com/blog/funding-rate-arbitrage
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from loguru import logger


FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000  # 8 часов в миллисекундах


# ─────────────────────────────────────────────────────
#  Активная позиция
# ─────────────────────────────────────────────────────

@dataclass
class HedgedPosition:
    """
    Дельта-нейтральная хеджированная позиция на одном символе.

    Два типа:
      'long_spot_short_perp'  → покупаем спот + шортим перп (положит. фандинг)
      'short_spot_long_perp'  → шортим спот + лонгуем перп (отрицат. фандинг) [TODO]
    """
    symbol:              str
    direction:           str        # 'long_spot_short_perp'

    # Нога 1 — Спот
    spot_qty:            float = 0.0
    entry_spot_price:    float = 0.0
    spot_order_id:       str   = ""

    # Нога 2 — Перп
    perp_qty:            float = 0.0
    entry_perp_price:    float = 0.0
    perp_order_id:       str   = ""

    # Метаданные входа
    entry_funding_rate:  float = 0.0
    opened_at:           datetime = field(default_factory=datetime.now)
    balance_before:      float = 0.0

    # Отслеживание цикла фандинга
    funding_cycles_held:    int   = 0
    estimated_funding:      float = 0.0
    last_next_funding_ms:   int   = 0   # последнее известное время следующего фандинга

    # Мониторинг базиса (спред между спот и перп ценами)
    entry_basis_pct:     float = 0.0    # базис при входе (perp_price/spot_price - 1)
    max_basis_pct:       float = 0.0    # максимальный базис за время позиции

    def age_hours(self) -> float:
        return (datetime.now() - self.opened_at).total_seconds() / 3600

    def notional_usd(self) -> float:
        return self.perp_qty * self.entry_perp_price

    def check_new_funding_cycle(self, new_next_funding_ms: int) -> bool:
        """
        Возвращает True если прошёл новый цикл фандинга.
        Определяется по увеличению nextFundingTime на ~8 часов.
        """
        if self.last_next_funding_ms == 0:
            self.last_next_funding_ms = new_next_funding_ms
            return False
        if new_next_funding_ms > self.last_next_funding_ms + FUNDING_INTERVAL_MS // 2:
            self.last_next_funding_ms = new_next_funding_ms
            return True
        return False


# ─────────────────────────────────────────────────────
#  Основная стратегия
# ─────────────────────────────────────────────────────

class FundingRateArbStrategy:

    def __init__(self, config):
        self.cfg = config

    # ──────────────────────────────────────────
    #  Анализ ставок и поиск возможностей
    # ──────────────────────────────────────────
    def scan_opportunities(
        self,
        funding_data:    dict,    # {symbol: {"rate", "next_time_ms", "price"}}
        rate_history:    dict,    # {symbol: [{"rate", "timestamp_ms"}, ...]}
        volumes_24h:     dict,    # {symbol: float (USD)}
        active_symbols:  set,
        max_slots:       int,
    ) -> list:
        """
        Находит символы подходящие для входа.
        Фильтры (стандарт open-source реализаций):
          1. Ставка > MIN_FUNDING_RATE
          2. Ставка стабильна (N последних периодов тоже положительны)
          3. 24h объём > MIN_VOLUME_24H
          4. Есть время до следующего фандинга (не входим прямо перед)
          5. Ожидаемая прибыль покрывает комиссии (break-even)
          6. Не занят в активной позиции
        """
        candidates = []

        for sym, data in funding_data.items():
            if sym in active_symbols:
                continue

            rate  = data.get("rate", 0.0)
            price = data.get("price", 0.0)
            next_ms = data.get("next_time_ms", 0)

            if price <= 0:
                continue

            # ── Фильтр 1: направление по ставке ──────────
            neg_enabled  = getattr(self.cfg, "NEGATIVE_FUNDING_ENABLED", False)
            min_neg_rate = getattr(self.cfg, "MIN_NEGATIVE_FUNDING_RATE", -0.00015)

            if rate >= self.cfg.MIN_FUNDING_RATE:
                direction = "long_spot_short_perp"      # шортисты платят лонгистам
            elif neg_enabled and rate <= min_neg_rate:
                direction = "long_perp_short_spot"      # лонгисты платят шортистам
            else:
                continue

            # ── Фильтр 2: стабильность ставки ────────────
            history = rate_history.get(sym, [])
            if not self._rate_is_stable(rate, history):
                logger.debug(f"{sym}: нестабильная ставка — пропуск")
                continue

            # ── Фильтр 3: объём торгов ────────────────────
            min_vol = getattr(self.cfg, "MIN_VOLUME_24H_USD", 10_000_000)
            vol = volumes_24h.get(sym, 0)
            if vol < min_vol:
                logger.debug(f"{sym}: объём ${vol/1e6:.1f}M < ${min_vol/1e6:.0f}M — пропуск")
                continue

            # ── Фильтр 4: тайминг ─────────────────────────
            min_to_fund = minutes_until_funding(next_ms)
            # Не входим если до фандинга меньше N минут (нет смысла)
            min_entry_window = getattr(self.cfg, "MIN_MINUTES_BEFORE_FUNDING_FOR_ENTRY", 60)
            if 0 < min_to_fund < min_entry_window:
                logger.debug(
                    f"{sym}: до фандинга {min_to_fund:.0f} мин < {min_entry_window} — пропуск"
                )
                continue

            # ── Фильтр 5: окупаемость ─────────────────────
            be_cycles = estimate_break_even_cycles(
                self.cfg.POSITION_USD, abs(rate), self.cfg.BYBIT_TAKER_FEE
            )
            max_affordable_be = getattr(self.cfg, "MAX_BREAK_EVEN_CYCLES", 8)
            if be_cycles > max_affordable_be:
                logger.debug(
                    f"{sym}: break-even {be_cycles:.1f} циклов > {max_affordable_be} — пропуск"
                )
                continue

            candidates.append({
                "symbol":       sym,
                "rate":         rate,
                "direction":    direction,
                "price":        price,
                "next_ms":      next_ms,
                "volume":       vol,
                "be_cycles":    be_cycles,
                "stability":    self._rate_stability_score(history),
            })

        if not candidates:
            return []

        # Сортируем: сначала по ставке, затем по стабильности
        candidates.sort(key=lambda x: (x["rate"], x["stability"]), reverse=True)

        # ── Фильтр категорий: защита от корреляции ───────────
        # Не берём более MAX_POSITIONS_PER_CATEGORY позиций в одной категории
        # (мемкоины коррелируют — при дампе падают все вместе)
        categories = getattr(self.cfg, "SYMBOL_CATEGORIES", {})
        max_per_cat = getattr(self.cfg, "MAX_POSITIONS_PER_CATEGORY", 999)

        if categories and max_per_cat < 999:
            # Считаем уже занятые слоты по категориям (активные позиции)
            cat_counts: dict = {}
            for active_sym in active_symbols:
                for cat, syms in categories.items():
                    if active_sym in syms:
                        cat_counts[cat] = cat_counts.get(cat, 0) + 1
                        break

            # Жадный выбор кандидатов с учётом лимита по категории
            selected = []
            for cand in candidates:
                if len(selected) >= max_slots:
                    break
                sym_cat = None
                for cat, syms in categories.items():
                    if cand["symbol"] in syms:
                        sym_cat = cat
                        break
                if sym_cat and cat_counts.get(sym_cat, 0) >= max_per_cat:
                    logger.debug(
                        f"{cand['symbol']}: категория '{sym_cat}' заполнена "
                        f"({cat_counts[sym_cat]}/{max_per_cat}) — пропуск"
                    )
                    continue
                selected.append(cand)
                if sym_cat:
                    cat_counts[sym_cat] = cat_counts.get(sym_cat, 0) + 1
            return selected

        return candidates[:max_slots]

    # ──────────────────────────────────────────
    #  Уровень 2: Basis Mean Reversion
    # ──────────────────────────────────────────
    def scan_basis_opportunities(
        self,
        funding_data:   dict,   # {symbol: {"rate", "price", "next_time_ms"}}
        spot_prices:    dict,   # {symbol: spot_price}
        active_symbols: set,
        max_slots:      int,
    ) -> list:
        """
        Находит аномальный базис (perp/spot - 1) для basis mean reversion.

        Когда perp > spot на BASIS_ENTRY_PCT — открываем short_perp + long_spot.
        Когда perp < spot на BASIS_ENTRY_PCT — открываем long_perp + short_spot.

        Работает независимо от ставки фандинга — деплоит капитал
        даже когда ставки низкие и funding arb не даёт сигналов.
        """
        if not getattr(self.cfg, "BASIS_ARB_ENABLED", False):
            return []

        entry_pct = getattr(self.cfg, "BASIS_ENTRY_PCT", 0.003)
        min_vol   = getattr(self.cfg, "MIN_VOLUME_24H_USD", 3_000_000)
        candidates = []

        for sym, data in funding_data.items():
            if sym in active_symbols:
                continue

            perp_price = data.get("price", 0.0)
            spot_price = spot_prices.get(sym, 0.0)

            if perp_price <= 0 or spot_price <= 0:
                continue

            basis = (perp_price / spot_price) - 1.0

            if abs(basis) < entry_pct:
                continue

            # Направление: куда идёт схождение
            if basis > 0:
                # perp дороже спота → перп упадёт или спот вырастет
                direction = "basis_short_perp"   # short perp + long spot
            else:
                # perp дешевле спота → перп вырастет или спот упадёт
                direction = "basis_long_perp"    # long perp + short spot

            candidates.append({
                "symbol":    sym,
                "rate":      data.get("rate", 0.0),
                "direction": direction,
                "price":     perp_price,
                "next_ms":   data.get("next_time_ms", 0),
                "basis":     basis,
                "be_cycles": 0,
                "stability": abs(basis),
            })

        # Сортируем по величине базиса (аномальнее = выгоднее)
        candidates.sort(key=lambda x: abs(x["basis"]), reverse=True)
        return candidates[:max_slots]

    def _rate_is_stable(self, current_rate: float, history: list) -> bool:
        """
        Проверяет стабильность ставки.
        Требование: из последних N периодов минимум M должны быть того же знака.
        """
        min_periods = getattr(self.cfg, "STABILITY_MIN_PERIODS", 3)
        if len(history) < min_periods:
            # Недостаточно истории — разрешаем вход (консервативно)
            return True

        recent = history[:min_periods]
        same_sign = sum(1 for h in recent if (h["rate"] > 0) == (current_rate > 0))
        return same_sign >= min_periods - 1  # допускаем 1 период другого знака

    def _rate_stability_score(self, history: list) -> float:
        """Оценка стабильности: среднее из последних 5 периодов (для сортировки)."""
        if not history:
            return 0.0
        rates = [h["rate"] for h in history[:5]]
        return sum(rates) / len(rates)

    # ──────────────────────────────────────────
    #  Условия выхода
    # ──────────────────────────────────────────
    def check_exit(
        self,
        pos:                   HedgedPosition,
        current_rate:          float,
        current_perp_price:    float,
        current_spot_price:    float,
        minutes_to_funding:    float,
        dynamic_max_basis_pct: float = None,   # если передан — переопределяет config
    ) -> tuple:
        """
        Проверяет все условия выхода.
        Возвращает (should_exit: bool, reason: str).

        Условия (по стандарту open-source ботов):
          1. Стоп-лосс: цена ушла против нас (только без хеджа)
          2. Базис-риск: спред spot/perp вырос аномально (динамический ATR-порог)
          3. Максимальное время удержания
          4. Ставка упала ниже порога выхода (после min_cycles)
          5. Ставка изменила знак (после min_cycles)
          6. Ставка слишком низкая перед следующим фандингом
        """
        is_basis = pos.direction.startswith("basis_")

        # ── BASIS ARBTRAGE: отдельная логика выхода ───────────
        if is_basis:
            basis_exit  = getattr(self.cfg, "BASIS_EXIT_PCT", 0.0005)
            basis_max_h = getattr(self.cfg, "BASIS_MAX_HOLD_HOURS", 24)

            if current_spot_price > 0 and current_perp_price > 0:
                current_basis = (current_perp_price / current_spot_price) - 1.0

                if pos.direction == "basis_short_perp" and current_basis <= basis_exit:
                    return True, "basis_converged"
                if pos.direction == "basis_long_perp" and current_basis >= -basis_exit:
                    return True, "basis_converged"

                # Базис инвертировался против нас — выходим
                if pos.direction == "basis_short_perp" and current_basis < -basis_exit * 3:
                    return True, "basis_inverted"
                if pos.direction == "basis_long_perp" and current_basis > basis_exit * 3:
                    return True, "basis_inverted"

            if pos.age_hours() >= basis_max_h:
                return True, "basis_max_hold"

            return False, "hold"

        # ── FUNDING ARBITRAGE: стандартная логика выхода ──────

        # 1. Стоп-лосс (только при отсутствии спот-хеджа)
        if not getattr(self.cfg, "HEDGE_WITH_SPOT", True):
            if pos.entry_perp_price > 0:
                price_chg = (current_perp_price - pos.entry_perp_price) / pos.entry_perp_price
                # long_spot_short_perp: цена идёт вверх — перп теряет
                if pos.direction == "long_spot_short_perp" and price_chg > self.cfg.STOP_LOSS_PCT:
                    return True, "stop_loss"
                # long_perp_short_spot: цена идёт вниз — перп теряет
                if pos.direction == "long_perp_short_spot" and price_chg < -self.cfg.STOP_LOSS_PCT:
                    return True, "stop_loss"

        # 2. Базис-риск: если спред спот/перп > порога — ликвидность нарушена
        if dynamic_max_basis_pct is not None:
            max_basis = dynamic_max_basis_pct
        else:
            max_basis = getattr(self.cfg, "MAX_BASIS_PCT", 0.02)
        if current_spot_price > 0 and current_perp_price > 0:
            basis = abs(current_perp_price / current_spot_price - 1.0)
            pos.max_basis_pct = max(pos.max_basis_pct, basis)
            if basis > max_basis:
                logger.warning(
                    f"[{pos.symbol}] Базис {basis*100:.2f}% > {max_basis*100:.2f}%"
                    f"{'(ATR)' if dynamic_max_basis_pct is not None else ''} — выход"
                )
                return True, "basis_risk"

        # 3. Максимальное время удержания
        if pos.age_hours() >= self.cfg.MAX_HOLD_HOURS:
            return True, "max_hold_time"

        # 4 & 5. Ставка: выход после min hold cycles
        if pos.funding_cycles_held >= self.cfg.MIN_HOLD_CYCLES:
            exit_neg = getattr(self.cfg, "EXIT_NEGATIVE_FUNDING_RATE", -0.00006)

            if pos.direction == "long_spot_short_perp":
                if current_rate < self.cfg.EXIT_FUNDING_RATE:
                    return True, "rate_dropped"
                if current_rate < -0.0001:
                    return True, "rate_flipped"

            elif pos.direction == "long_perp_short_spot":
                if current_rate > exit_neg:     # ставка стала менее отрицательной
                    return True, "rate_recovered"
                if current_rate > 0.0001:       # ставка ушла в плюс
                    return True, "rate_flipped"

        # 6. Ставка низкая перед следующим фандингом
        close_before = getattr(self.cfg, "CLOSE_BEFORE_FUNDING_MIN", 10)
        if minutes_to_funding <= close_before:
            min_for_worth = self.cfg.MIN_FUNDING_RATE * 0.4
            if abs(current_rate) < min_for_worth and pos.funding_cycles_held >= self.cfg.MIN_HOLD_CYCLES:
                return True, "low_rate_before_funding"

        return False, "hold"

    # ──────────────────────────────────────────
    #  Обнаружение и учёт выплаты фандинга
    # ──────────────────────────────────────────
    def update_funding_cycle(self, pos: HedgedPosition, current_rate: float) -> float:
        """
        Вызывается при обнаружении нового цикла фандинга (nextFundingTime обновился).
        Возвращает оценочную выплату в USD.
        """
        payment = pos.perp_qty * pos.entry_perp_price * abs(current_rate)
        pos.funding_cycles_held += 1
        pos.estimated_funding   += payment
        logger.info(
            f"[{pos.symbol}] 💰 Фандинг #{pos.funding_cycles_held}: "
            f"+${payment:.4f} (ставка={current_rate*100:.4f}% | "
            f"накоплено=${pos.estimated_funding:.4f})"
        )
        return payment

    # ──────────────────────────────────────────
    #  Расчёт размеров позиций
    # ──────────────────────────────────────────
    def calc_position_usd(self, balance: float) -> float:
        """
        Kelly-критерий: размер одной ноги позиции (USD).

        Если KELLY_ENABLED=True: position = KELLY_FRACTION * balance,
        зажатое между POSITION_USD_MIN и POSITION_USD_MAX.
        Иначе возвращает фиксированный POSITION_USD из конфига.
        """
        if not getattr(self.cfg, "KELLY_ENABLED", False):
            return self.cfg.POSITION_USD

        kelly_fraction = getattr(self.cfg, "KELLY_FRACTION", 0.015)
        pos_min        = getattr(self.cfg, "POSITION_USD_MIN", 10.0)
        pos_max        = getattr(self.cfg, "POSITION_USD_MAX", 150.0)

        raw = kelly_fraction * balance
        clamped = max(pos_min, min(pos_max, raw))
        logger.debug(
            f"Kelly position: {kelly_fraction*100:.1f}% × ${balance:.0f} "
            f"= ${raw:.1f} → clamp[{pos_min},{pos_max}] = ${clamped:.1f}"
        )
        return clamped

    def calc_perp_qty(self, price: float, position_usd: float = None) -> float:
        """Количество монет для перп-ноги."""
        if price <= 0:
            return 0.0
        usd = position_usd if position_usd is not None else self.cfg.POSITION_USD
        return usd / price

    # ──────────────────────────────────────────
    #  Анализ истории ставок
    # ──────────────────────────────────────────
    @staticmethod
    def analyze_funding_history(history: list) -> dict:
        if not history:
            return {"avg": 0.0, "min": 0.0, "max": 0.0, "stable": False, "count": 0}
        rates  = [h["rate"] for h in history]
        avg    = sum(rates) / len(rates)
        stable = (all(r > 0 for r in rates) or all(r < 0 for r in rates)) and abs(avg) > 0.0002
        return {
            "avg":    avg,
            "min":    min(rates),
            "max":    max(rates),
            "stable": stable,
            "count":  len(rates),
        }


# ─────────────────────────────────────────────────────
#  Вспомогательные функции
# ─────────────────────────────────────────────────────

def minutes_until_funding(next_funding_ms: int) -> float:
    """Минут до следующего фандинга."""
    if next_funding_ms <= 0:
        return 999.0
    now_ms  = int(datetime.now().timestamp() * 1000)
    diff_ms = next_funding_ms - now_ms
    return max(0.0, diff_ms / 60_000)


def annualized_rate(rate_per_8h: float) -> float:
    """Годовая доходность из ставки за 8 часов (3 выплаты/день × 365 дней)."""
    return rate_per_8h * 3 * 365


def estimate_break_even_cycles(position_usd: float, rate: float,
                                taker_fee: float) -> float:
    """
    Минимальное кол-во циклов фандинга для покрытия комиссий.
    4 ордера × position_usd × taker_fee (вход + выход обеих ног).
    """
    total_fees = 4 * position_usd * taker_fee
    income_per_cycle = position_usd * rate
    if income_per_cycle <= 0:
        return float("inf")
    return total_fees / income_per_cycle
