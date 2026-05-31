"""
bybit_client.py — Обёртка над Bybit V5 API
Поддерживает: линейные перпы (linear), спот (spot), ставки фандинга
"""

import math
import time
from typing import Optional
from loguru import logger
from pybit.unified_trading import HTTP


class BybitClient:

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.session = HTTP(testnet=testnet, api_key=api_key, api_secret=api_secret)
        self.testnet = testnet
        self._instrument_cache: dict = {}   # кэш параметров инструментов
        logger.info(f"Bybit клиент | {'TESTNET' if testnet else '🔴 MAINNET'}")

    # ──────────────────────────────────────────
    #  КОТИРОВКИ
    # ──────────────────────────────────────────
    def get_last_price(self, symbol, category="linear") -> Optional[float]:
        try:
            resp = self.session.get_tickers(category=category, symbol=symbol)
            return float(resp["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.error(f"get_last_price({symbol}): {e}")
            return None

    def get_orderbook(self, symbol, category="linear", limit=5) -> Optional[dict]:
        try:
            resp = self.session.get_orderbook(category=category, symbol=symbol, limit=limit)
            bids = resp["result"]["b"]
            asks = resp["result"]["a"]
            if not bids or not asks:
                return None
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid      = (best_bid + best_ask) / 2.0
            bid_vol  = sum(float(b[1]) for b in bids)
            ask_vol  = sum(float(a[1]) for a in asks)
            return {"bid": best_bid, "ask": best_ask, "mid": mid,
                    "spread": best_ask - best_bid,
                    "bid_vol": bid_vol, "ask_vol": ask_vol}
        except Exception as e:
            logger.error(f"get_orderbook({symbol}): {e}")
            return None

    def get_klines(self, symbol, interval, limit=200, category="linear"):
        try:
            resp = self.session.get_kline(
                category=category, symbol=symbol, interval=interval, limit=limit)
            raw = list(reversed(resp["result"]["list"]))
            return [{"timestamp": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                     "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
                    for c in raw]
        except Exception as e:
            logger.error(f"get_klines({symbol}): {e}")
            return []

    def get_atr(self, symbol: str, interval: str = "60",
                period: int = 14, category: str = "linear") -> Optional[float]:
        """
        Average True Range — оценка текущей волатильности символа.
        Используется для динамического расширения порога базис-риска.

        Возвращает ATR в абсолютных USD (не в процентах).
        Для перевода в %: atr_pct = get_atr(...) / current_price
        """
        candles = self.get_klines(symbol, interval, limit=period + 1, category=category)
        if len(candles) < period + 1:
            logger.debug(f"get_atr({symbol}): недостаточно свечей ({len(candles)})")
            return None
        try:
            true_ranges = []
            for i in range(1, len(candles)):
                high       = candles[i]["high"]
                low        = candles[i]["low"]
                prev_close = candles[i - 1]["close"]
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low  - prev_close),
                )
                true_ranges.append(tr)
            # Простое скользящее среднее последних `period` значений
            atr = sum(true_ranges[-period:]) / min(period, len(true_ranges))
            return atr
        except Exception as e:
            logger.error(f"get_atr({symbol}): {e}")
            return None

    # ──────────────────────────────────────────
    #  СТАВКИ ФАНДИНГА
    # ──────────────────────────────────────────
    def get_funding_rates(self, symbols: list) -> dict:
        """
        Возвращает {symbol: {"rate": float, "next_time_ms": int, "price": float}}
        для всех символов из списка.
        Один запрос к /v5/market/tickers (category=linear) — без лишних вызовов.
        """
        try:
            resp = self.session.get_tickers(category="linear")
            result = {}
            for t in resp["result"]["list"]:
                if t["symbol"] in symbols:
                    result[t["symbol"]] = {
                        "rate":         float(t.get("fundingRate", 0)),
                        "next_time_ms": int(t.get("nextFundingTime", 0)),
                        "price":        float(t.get("lastPrice", 0)),
                    }
            return result
        except Exception as e:
            logger.error(f"get_funding_rates: {e}")
            return {}

    def get_funding_rate(self, symbol: str) -> Optional[dict]:
        """Ставка фандинга для одного символа."""
        rates = self.get_funding_rates([symbol])
        return rates.get(symbol)

    def get_funding_history(self, symbol: str, limit: int = 10) -> list:
        """История выплат фандинга (для анализа стабильности ставки)."""
        try:
            resp = self.session.get_funding_rate_history(
                category="linear", symbol=symbol, limit=limit)
            return [
                {
                    "symbol":       r["symbol"],
                    "rate":         float(r["fundingRate"]),
                    "timestamp_ms": int(r["fundingRateTimestamp"]),
                }
                for r in resp["result"]["list"]
            ]
        except Exception as e:
            logger.error(f"get_funding_history({symbol}): {e}")
            return []

    # ──────────────────────────────────────────
    #  ИНСТРУМЕНТЫ
    # ──────────────────────────────────────────
    def get_instrument_info(self, symbol, category="linear") -> Optional[dict]:
        key = f"{category}:{symbol}"
        if key in self._instrument_cache:
            return self._instrument_cache[key]
        try:
            resp = self.session.get_instruments_info(category=category, symbol=symbol)
            info = resp["result"]["list"][0]
            self._instrument_cache[key] = info
            return info
        except Exception as e:
            logger.error(f"get_instrument_info({symbol}): {e}")
            return None

    def round_qty(self, qty: float, symbol: str, category: str = "linear") -> float:
        """Округляет количество до допустимого шага инструмента."""
        info = self.get_instrument_info(symbol, category)
        try:
            if category == "spot":
                step = float(info["lotSizeFilter"]["basePrecision"])
            else:
                step = float(info["lotSizeFilter"]["qtyStep"])
            precision = max(0, -int(math.floor(math.log10(step))))
            return round(math.floor(qty / step) * step, precision)
        except Exception:
            return round(qty, 4)

    def get_min_order_qty(self, symbol: str, category: str = "linear") -> float:
        """Минимальный размер ордера."""
        info = self.get_instrument_info(symbol, category)
        try:
            if category == "spot":
                return float(info["lotSizeFilter"]["minOrderQty"])
            else:
                return float(info["lotSizeFilter"]["minOrderQty"])
        except Exception:
            return 0.0

    # ──────────────────────────────────────────
    #  ОРДЕРА — ПЕРПЕТУАЛ (LINEAR)
    # ──────────────────────────────────────────
    def place_market_order(self, symbol: str, side: str, qty: float,
                           category: str = "linear",
                           reduce_only: bool = False) -> Optional[str]:
        try:
            resp = self.session.place_order(
                category=category, symbol=symbol, side=side,
                orderType="Market", qty=str(qty),
                reduceOnly=reduce_only, timeInForce="GTC")
            order_id = resp["result"]["orderId"]
            logger.info(f"Ордер [{category}]: {side} {qty} {symbol} | id={order_id}")
            return order_id
        except Exception as e:
            logger.error(f"place_market_order({symbol} {side} {qty}): {e}")
            return None

    def close_position(self, symbol: str, category: str = "linear") -> bool:
        """Закрыть открытую позицию по рынку."""
        pos = self.get_position(symbol, category)
        if pos is None:
            return False
        close_side = "Sell" if pos["side"] == "Buy" else "Buy"
        return self.place_market_order(
            symbol, close_side, pos["size"], category, reduce_only=True) is not None

    def get_position(self, symbol: str, category: str = "linear") -> Optional[dict]:
        try:
            resp = self.session.get_positions(category=category, symbol=symbol)
            for pos in resp["result"]["list"]:
                if float(pos.get("size", 0)) != 0:
                    return {
                        "symbol":      pos["symbol"],
                        "side":        pos["side"],
                        "size":        float(pos["size"]),
                        "entry_price": float(pos["avgPrice"]),
                        "unreal_pnl":  float(pos["unrealisedPnl"]),
                    }
            return None
        except Exception as e:
            logger.error(f"get_position({symbol}): {e}")
            return None

    def set_leverage(self, symbol: str, leverage: int, category: str = "linear") -> bool:
        try:
            self.session.set_leverage(
                category=category, symbol=symbol,
                buyLeverage=str(leverage), sellLeverage=str(leverage))
            return True
        except Exception as e:
            if "leverage not modified" in str(e).lower():
                return True
            logger.warning(f"set_leverage({symbol}): {e}")
            return False

    # ──────────────────────────────────────────
    #  ОРДЕРА — СПОТ
    # ──────────────────────────────────────────
    def buy_spot(self, symbol: str, usdt_amount: float) -> Optional[str]:
        """
        Купить спот на сумму usdt_amount USDT (рыночный ордер).
        Bybit V5: qty = сумма в USDT, marketUnit = quoteCoinQty.
        """
        try:
            # Округляем до 2 знаков (минимальный шаг для USDT)
            qty_str = f"{usdt_amount:.2f}"
            resp = self.session.place_order(
                category="spot",
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=qty_str,
                marketUnit="quoteCoin",
            )
            order_id = resp["result"]["orderId"]
            logger.info(f"Ордер [spot BUY]: {qty_str} USDT → {symbol} | id={order_id}")
            return order_id
        except Exception as e:
            logger.error(f"buy_spot({symbol}, {usdt_amount}): {e}")
            return None

    def sell_spot(self, symbol: str, base_qty: float) -> Optional[str]:
        """
        Продать base_qty единиц базовой монеты на споте.
        Например: sell_spot("ETHUSDT", 0.0071) → продать 0.0071 ETH.
        """
        try:
            qty = self.round_qty(base_qty, symbol, category="spot")
            if qty <= 0:
                logger.warning(f"sell_spot({symbol}): qty={qty} <= 0, пропуск")
                return None
            resp = self.session.place_order(
                category="spot",
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=str(qty),
                marketUnit="baseCoin",
            )
            order_id = resp["result"]["orderId"]
            logger.info(f"Ордер [spot SELL]: {qty} {symbol} | id={order_id}")
            return order_id
        except Exception as e:
            logger.error(f"sell_spot({symbol}, {base_qty}): {e}")
            return None

    def short_spot(self, symbol: str, base_qty: float) -> Optional[str]:
        """
        Открыть маржинальный шорт спота (isLeverage=1).
        Используется при стратегии long_perp_short_spot (отрицательный фандинг).
        Bybit UTA автоматически занимает токены и продаёт их.
        """
        try:
            qty = self.round_qty(base_qty, symbol, category="spot")
            if qty <= 0:
                logger.warning(f"short_spot({symbol}): qty={qty} <= 0, пропуск")
                return None
            resp = self.session.place_order(
                category="spot",
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=str(qty),
                marketUnit="baseCoin",
                isLeverage=1,
            )
            order_id = resp["result"]["orderId"]
            logger.info(f"Ордер [spot SHORT]: {qty} {symbol} | id={order_id}")
            return order_id
        except Exception as e:
            logger.error(f"short_spot({symbol}, {base_qty}): {e}")
            return None

    def cover_short_spot(self, symbol: str, usdt_amount: float) -> Optional[str]:
        """
        Закрыть маржинальный шорт спота (купить для покрытия долга).
        Используется при закрытии long_perp_short_spot позиции.
        """
        try:
            qty_str = f"{usdt_amount:.2f}"
            resp = self.session.place_order(
                category="spot",
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=qty_str,
                marketUnit="quoteCoin",
            )
            order_id = resp["result"]["orderId"]
            logger.info(f"Ордер [spot COVER]: {qty_str} USDT → {symbol} | id={order_id}")
            return order_id
        except Exception as e:
            logger.error(f"cover_short_spot({symbol}, {usdt_amount}): {e}")
            return None

    def get_all_funding_intervals(self, symbols: list) -> dict:
        """
        {symbol: interval_hours} одним запросом по всем linear-перпам.
        Bybit отдаёт fundingInterval в минутах (480=8ч, 240=4ч, 60=1ч).
        Критично для ранжирования: 0.01%/1ч ≈ 88% APY, а 0.02%/8ч ≈ 22% APY —
        при сортировке по сырой ставке бот выбрал бы худшую монету.
        Интервалы не меняются → кэшировать на стороне вызова.
        """
        try:
            resp = self.session.get_instruments_info(category="linear")
            out = {}
            for it in resp["result"]["list"]:
                try:
                    out[it["symbol"]] = float(it.get("fundingInterval", 480)) / 60.0
                except (TypeError, ValueError):
                    out[it["symbol"]] = 8.0
            return {s: out.get(s, 8.0) for s in symbols}
        except Exception as e:
            logger.error(f"get_all_funding_intervals: {e}")
            return {s: 8.0 for s in symbols}

    def get_collateral_info(self, coin: str) -> Optional[dict]:
        """
        Collateral-настройки монеты в UTA. Нужно для шорта спота
        (long_perp_short_spot / basis_long_perp) — Bybit занимает токен,
        чтобы его продать, и берёт почасовой процент за заём.

        Возвращает:
          {"enabled": bool,            # включена как collateral (collateralSwitch=ON)
           "borrowable": bool,         # вообще можно занять
           "hourly_borrow_rate": float}  # ставка займа за 1 час (доля, не %)
        Или None при ошибке API.
        """
        try:
            resp = self.session.get_collateral_info(currency=coin)
            for c in resp["result"]["list"]:
                if c["currency"] == coin:
                    return {
                        "enabled":    c.get("collateralSwitch") == "ON",
                        "borrowable": bool(c.get("borrowable", False)),
                        "hourly_borrow_rate": float(c.get("hourlyBorrowRate") or 0.0),
                    }
            return None
        except Exception as e:
            logger.error(f"get_collateral_info({coin}): {e}")
            return None

    def get_all_spot_prices(self, symbols: list) -> dict:
        """Возвращает {symbol: last_price} для всех символов за один запрос."""
        try:
            resp = self.session.get_tickers(category="spot")
            price_map = {t["symbol"]: float(t.get("lastPrice", 0))
                         for t in resp["result"]["list"]}
            return {s: price_map.get(s, 0.0) for s in symbols}
        except Exception as e:
            logger.error(f"get_all_spot_prices: {e}")
            return {s: 0.0 for s in symbols}

    def get_spot_coin_balance(self, coin: str) -> float:
        """
        Возвращает баланс монеты в спот-кошельке UTA.
        Использует walletBalance (не availableToWithdraw) — в UTA только что купленные
        токены могут быть заблокированы как маржа и availableToWithdraw = 0,
        хотя фактически токены есть и их можно продать.
        """
        try:
            resp = self.session.get_wallet_balance(accountType="UNIFIED")
            for c in resp["result"]["list"][0]["coin"]:
                if c["coin"] == coin:
                    # walletBalance — фактическое кол-во токенов в аккаунте
                    # availableToWithdraw может быть 0 если токены используются как маржа
                    wallet_bal = float(c.get("walletBalance", 0))
                    avail_bal  = float(c.get("availableToWithdraw", 0))
                    # Берём максимум: если walletBalance > 0, токены есть
                    return max(wallet_bal, avail_bal)
            return 0.0
        except Exception as e:
            logger.error(f"get_spot_coin_balance({coin}): {e}")
            return 0.0

    # ──────────────────────────────────────────
    #  КОШЕЛЁК
    # ──────────────────────────────────────────
    def get_wallet_balance(self, coin: str = "USDT") -> Optional[float]:
        try:
            resp = self.session.get_wallet_balance(accountType="UNIFIED")
            for c in resp["result"]["list"][0]["coin"]:
                if c["coin"] == coin:
                    return float(c["walletBalance"])
            return 0.0
        except Exception as e:
            logger.error(f"get_wallet_balance: {e}")
            return None

    def get_total_equity(self) -> Optional[float]:
        """
        Полная mark-to-market стоимость UTA-аккаунта (USDT + все монеты + unreal PnL).
        Правильная база для просадки: незахеджированный спот, спот-холдинги и PnL
        перпов учтены автоматически → ложная просадка от «крипты на споте» исключена.
        """
        try:
            resp = self.session.get_wallet_balance(accountType="UNIFIED")
            te = resp["result"]["list"][0].get("totalEquity")
            return float(te) if te not in (None, "") else None
        except Exception as e:
            logger.error(f"get_total_equity: {e}")
            return None

    def get_volumes_24h(self, symbols: list, category: str = "linear") -> dict:
        """Возвращает {symbol: turnover_24h_usd}."""
        try:
            resp = self.session.get_tickers(category=category)
            vol_map = {t["symbol"]: float(t.get("turnover24h", 0))
                       for t in resp["result"]["list"]}
            return {s: vol_map.get(s, 0.0) for s in symbols}
        except Exception as e:
            logger.error(f"get_volumes_24h: {e}")
            return {s: float("inf") for s in symbols}
