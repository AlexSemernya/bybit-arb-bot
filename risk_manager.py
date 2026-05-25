"""
risk_manager.py — Риск-менеджмент для Funding Rate Arbitrage бота
"""

import math
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class RiskState:
    initial_balance:    float = 0.0
    peak_balance:       float = 0.0
    current_balance:    float = 0.0
    trade_returns:      list  = field(default_factory=list)
    is_trading_halted:  bool  = False
    halt_reason:        str   = ""
    consecutive_losses: int   = 0


class RiskManager:

    MAX_CONSECUTIVE_LOSSES = 5

    def __init__(self, config, initial_balance: float):
        self.cfg   = config
        self.state = RiskState(
            initial_balance=initial_balance,
            peak_balance=initial_balance,
            current_balance=initial_balance,
        )
        logger.info(f"RiskManager | баланс={initial_balance:.2f} | "
                    f"max_dd={config.MAX_DRAWDOWN_PCT*100:.0f}%")

    def update_balance(self, new_balance: float):
        self.state.current_balance = new_balance
        if new_balance > self.state.peak_balance:
            self.state.peak_balance = new_balance

    def current_drawdown(self) -> float:
        if self.state.peak_balance <= 0:
            return 0.0
        return max(0.0, (self.state.peak_balance - self.state.current_balance)
                   / self.state.peak_balance)

    def can_open_position(self, current_balance: float, required_margin: float) -> tuple:
        """
        Проверяет, можно ли открыть новую позицию.
        required_margin = POSITION_USD × 2 (спот + перп ноги)
        """
        if self.state.is_trading_halted:
            return False, f"Торговля остановлена: {self.state.halt_reason}"

        dd = self.current_drawdown()
        if dd >= self.cfg.MAX_DRAWDOWN_PCT:
            self._halt(f"Просадка {dd*100:.1f}%")
            return False, f"Превышена просадка {dd*100:.1f}%"

        # Требуем 20% запас сверх margin (комиссии + буфер)
        if current_balance < required_margin * 1.2:
            return False, (f"Мало средств: {current_balance:.2f} < "
                           f"{required_margin*1.2:.2f} USDT")

        if self.state.consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
            return False, f"Много убытков подряд ({self.state.consecutive_losses})"

        return True, "OK"

    def record_trade(self, pnl_usdt: float, entry_balance: float):
        ret = pnl_usdt / entry_balance if entry_balance > 0 else 0.0
        self.state.trade_returns.append(ret)

        if pnl_usdt >= 0:
            self.state.consecutive_losses = 0
            logger.info(f"Сделка: +{pnl_usdt:.4f} USDT ({ret*100:+.2f}%)")
        else:
            self.state.consecutive_losses += 1
            logger.warning(
                f"Сделка: {pnl_usdt:.4f} USDT | "
                f"убытков подряд: {self.state.consecutive_losses}"
            )

    def sharpe_ratio(self) -> float:
        r = self.state.trade_returns
        if len(r) < 2:
            return 0.0
        import statistics
        std = statistics.stdev(r)
        return round(statistics.mean(r) / std * math.sqrt(365), 3) if std else 0.0

    def get_summary(self) -> dict:
        r    = self.state.trade_returns
        n    = len(r)
        wins = sum(1 for x in r if x > 0)
        return {
            "total_trades":         n,
            "win_rate_pct":         round(wins / n * 100, 1) if n else 0,
            "sharpe_ratio":         self.sharpe_ratio(),
            "current_drawdown_pct": round(self.current_drawdown() * 100, 2),
            "total_pnl_usdt":       round(self.state.current_balance - self.state.initial_balance, 4),
            "peak_balance":         round(self.state.peak_balance, 2),
            "current_balance":      round(self.state.current_balance, 2),
            "is_halted":            self.state.is_trading_halted,
            "halt_reason":          self.state.halt_reason,
            "consecutive_losses":   self.state.consecutive_losses,
        }

    def _halt(self, reason: str):
        if not self.state.is_trading_halted:
            self.state.is_trading_halted = True
            self.state.halt_reason       = reason
            logger.critical(f"🛑 ТОРГОВЛЯ ОСТАНОВЛЕНА: {reason}")
