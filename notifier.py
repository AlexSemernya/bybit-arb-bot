"""
notifier.py — Telegram уведомления для Funding Rate Arbitrage бота
"""

import requests
from datetime import datetime
from loguru import logger

from strategy import annualized_rate


class TelegramNotifier:

    API_URL = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: str, enabled: bool = True):
        self.token    = token
        self.chat_ids = [c.strip() for c in chat_id.split(",") if c.strip()]
        self.enabled  = enabled and bool(token) and bool(self.chat_ids)
        if self.enabled:
            logger.info(f"Telegram ✅ включён → {len(self.chat_ids)} получателей: {self.chat_ids}")
        else:
            logger.warning("Telegram ВЫКЛЮЧЕН (нет TG_BOT_TOKEN / TG_CHAT_ID)")

    # ──────────────────────────────────────────
    #  Открытие позиции
    # ──────────────────────────────────────────
    def notify_position_opened(self, symbol: str, direction: str,
                                funding_rate: float, perp_qty: float,
                                spot_qty: float, perp_price: float,
                                spot_price: float, balance: float,
                                break_even_cycles: float):
        apy = annualized_rate(funding_rate) * 100
        rate_pct = funding_rate * 100
        spot_line = (
            f"<b>Спот:</b> BUY {spot_qty:.6f} {symbol.replace('USDT','')} "
            f"@ <code>{spot_price:,.4f}</code>\n"
            if spot_qty > 0 else "⚠️ <i>Без спот-хеджа</i>\n"
        )
        self._send(
            f"🟢 <b>Открыта позиция (Funding Arb)</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<b>Символ:</b> {symbol}\n"
            f"<b>Тип:</b> Long Spot + Short Perp\n\n"
            f"{spot_line}"
            f"<b>Перп:</b> SHORT {perp_qty:.6f} {symbol.replace('USDT','')} "
            f"@ <code>{perp_price:,.4f}</code>\n\n"
            f"<b>Ставка фандинга:</b> <code>{rate_pct:.4f}%</code> / 8ч\n"
            f"<b>APY (оценка):</b> <code>{apy:.1f}%</code>\n"
            f"<b>Окупаемость:</b> ~{break_even_cycles:.1f} цикл(а)\n\n"
            f"<b>Баланс:</b> <code>{balance:.2f}</code> USDT\n"
            f"<b>Время:</b> {self._now()}"
        )

    # ──────────────────────────────────────────
    #  Закрытие позиции
    # ──────────────────────────────────────────
    def notify_position_closed(self, symbol: str, funding_cycles: int,
                                estimated_funding: float, total_pnl: float,
                                balance: float, reason: str,
                                hold_hours: float):
        emoji   = "✅" if total_pnl >= 0 else "🔴"
        pnl_str = f"+{total_pnl:.4f}" if total_pnl >= 0 else f"{total_pnl:.4f}"
        reasons = {
            "rate_dropped":          "📉 Ставка упала",
            "rate_flipped":          "🔄 Ставка изменила знак",
            "stop_loss":             "🛑 Стоп-лосс",
            "max_hold_time":         "⏰ Максимальное время удержания",
            "low_rate_before_funding": "⚠️ Низкая ставка перед фандингом",
            "shutdown":              "⛔ Остановка бота",
            "manual":                "👤 Ручное закрытие",
        }
        self._send(
            f"{emoji} <b>Закрыта позиция</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<b>Символ:</b> {symbol}\n"
            f"<b>Причина:</b> {reasons.get(reason, reason)}\n\n"
            f"<b>Циклов фандинга:</b> <code>{funding_cycles}</code> "
            f"({hold_hours:.1f}ч)\n"
            f"<b>Фандинг (оценка):</b> <code>+{estimated_funding:.4f}</code> USDT\n"
            f"<b>PnL итого:</b> <code>{pnl_str}</code> USDT\n\n"
            f"<b>Баланс:</b> <code>{balance:.2f}</code> USDT\n"
            f"<b>Время:</b> {self._now()}"
        )

    # ──────────────────────────────────────────
    #  Сбор фандинга (раз в 8 часов)
    # ──────────────────────────────────────────
    def notify_funding_collected(self, symbol: str, payment: float,
                                  cycle_num: int, total_so_far: float):
        self._send(
            f"💰 <b>Фандинг получен</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<b>Символ:</b> {symbol}\n"
            f"<b>Выплата #{cycle_num}:</b> <code>+{payment:.4f}</code> USDT\n"
            f"<b>Накоплено всего:</b> <code>+{total_so_far:.4f}</code> USDT\n"
            f"<b>Время:</b> {self._now()}"
        )

    # ──────────────────────────────────────────
    #  Старт / Стоп
    # ──────────────────────────────────────────
    def notify_bot_started(self, n_symbols: int, balance: float, testnet: bool):
        mode = "🟡 TESTNET" if testnet else "🔴 MAINNET"
        self._send(
            f"🤖 <b>Funding Rate Arb Bot запущен</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<b>Режим:</b> {mode}\n"
            f"<b>Сканирует:</b> {n_symbols} символов\n"
            f"<b>Баланс:</b> <code>{balance:.2f}</code> USDT\n"
            f"<b>Время:</b> {self._now()}"
        )

    def notify_bot_stopped(self, trades: int, win_rate: float,
                            total_pnl: float, total_funding: float):
        pnl_str = f"+{total_pnl:.4f}" if total_pnl >= 0 else f"{total_pnl:.4f}"
        self._send(
            f"⛔ <b>Бот остановлен</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<b>Сделок:</b> <code>{trades}</code>\n"
            f"<b>Win Rate:</b> <code>{win_rate:.1f}%</code>\n"
            f"<b>Фандинг собрано:</b> <code>+{total_funding:.4f}</code> USDT\n"
            f"<b>PnL итого:</b> <code>{pnl_str}</code> USDT\n"
            f"<b>Время:</b> {self._now()}"
        )

    # ──────────────────────────────────────────
    #  Ежедневный отчёт
    # ──────────────────────────────────────────
    def notify_daily_report(self, today: dict, all_time: dict,
                             balance: float, per_symbol: list):
        pnl_today = today.get("pnl_net", 0)
        fund_today = today.get("funding_earned", 0)
        emoji = "✅" if pnl_today >= 0 else "🔴"
        pnl_str = f"+{pnl_today:.4f}" if pnl_today >= 0 else f"{pnl_today:.4f}"

        top = sorted(per_symbol, key=lambda x: x["funding"], reverse=True)[:3]
        sym_lines = "".join(
            f"  {s['symbol']}: <code>+{s['funding']:.4f}</code> "
            f"({s['trades']}сд, {s['win_rate']}%WR)\n"
            for s in top
        ) or "  Нет данных\n"

        self._send(
            f"📊 <b>Ежедневный отчёт</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"{emoji} <b>PnL сегодня:</b> <code>{pnl_str}</code> USDT\n"
            f"<b>Фандинг сегодня:</b> <code>+{fund_today:.4f}</code> USDT\n"
            f"<b>Сделок:</b> <code>{today.get('trades', 0)}</code> | "
            f"WR: <code>{today.get('win_rate', 0)}%</code>\n\n"
            f"<b>За всё время:</b>\n"
            f"  Фандинг: <code>+{all_time.get('funding_earned', 0):.4f}</code> USDT\n"
            f"  PnL net: <code>{all_time.get('pnl_net', 0):+.4f}</code> USDT\n"
            f"  Сделок: <code>{all_time.get('trades', 0)}</code>\n\n"
            f"<b>Топ символы:</b>\n{sym_lines}"
            f"<b>Баланс:</b> <code>{balance:.2f}</code> USDT\n"
            f"<b>Дата:</b> {self._now()}"
        )

    # ──────────────────────────────────────────
    #  Алерты
    # ──────────────────────────────────────────
    def notify_drawdown_halt(self, drawdown_pct: float, balance: float):
        self._send(
            f"🛑 <b>ТОРГОВЛЯ ОСТАНОВЛЕНА</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<b>Просадка:</b> <code>{drawdown_pct:.1f}%</code>\n"
            f"<b>Баланс:</b> <code>{balance:.2f}</code> USDT\n"
            f"<b>Время:</b> {self._now()}"
        )

    def notify_inactivity(self, hours: float, balance: float,
                           active_positions: int, top_rates: list):
        rates_str = "".join(
            f"  {sym}: <code>{rate*100:.4f}%</code>/8ч\n"
            for sym, rate in top_rates[:3]
        ) or "  Нет высоких ставок\n"
        self._send(
            f"💤 <b>Нет новых позиций {hours:.0f}ч</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<b>Открытых позиций:</b> <code>{active_positions}</code>\n"
            f"<b>Лучшие ставки сейчас:</b>\n{rates_str}"
            f"<b>Баланс:</b> <code>{balance:.2f}</code> USDT\n"
            f"<b>Время:</b> {self._now()}"
        )

    def notify_error(self, error_msg: str):
        self._send(
            f"❌ <b>Ошибка бота</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"<code>{error_msg[:400]}</code>\n"
            f"<b>Время:</b> {self._now()}"
        )

    # ──────────────────────────────────────────
    #  Внутренняя отправка
    # ──────────────────────────────────────────
    def _send(self, text: str) -> bool:
        if not self.enabled:
            return False
        url     = self.API_URL.format(token=self.token, method="sendMessage")
        success = False
        for chat_id in self.chat_ids:
            try:
                resp = requests.post(url, json={
                    "chat_id": chat_id, "text": text,
                    "parse_mode": "HTML", "disable_web_page_preview": True,
                }, timeout=10)
                data = resp.json()
                if resp.status_code == 200 and data.get("ok"):
                    logger.info(f"TG ✅ отправлено (chat_id={chat_id})")
                    success = True
                else:
                    code = data.get("error_code")
                    desc = data.get("description", "")
                    logger.error(f"TG ❌ chat_id={chat_id} | {code}: {desc}")
                    if code == 401:
                        logger.error("TG → Неверный TG_BOT_TOKEN")
                    elif code == 403:
                        logger.error(f"TG → Напишите /start боту (chat_id={chat_id})")
            except requests.exceptions.ConnectionError:
                logger.error(f"TG ❌ нет доступа к api.telegram.org")
            except Exception as e:
                logger.error(f"TG ❌ {e}")
        return success

    def _now(self) -> str:
        return datetime.now().strftime("%d.%m.%Y %H:%M:%S")
