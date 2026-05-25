"""
trade_history.py — SQLite история сделок для Funding Rate Arbitrage бота
"""

import sqlite3
from datetime import datetime, date
from loguru import logger


class TradeHistory:

    def __init__(self, db_path: str = "trades.db"):
        self.db_path = db_path
        self.conn    = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        logger.info(f"TradeHistory | БД: {db_path}")

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol              TEXT,
                direction           TEXT,
                entry_funding_rate  REAL,
                exit_funding_rate   REAL,
                entry_perp_price    REAL,
                exit_perp_price     REAL,
                entry_spot_price    REAL,
                exit_spot_price     REAL,
                perp_qty            REAL,
                spot_qty            REAL,
                funding_cycles      INTEGER,
                estimated_funding   REAL,
                spot_pnl            REAL,
                total_pnl           REAL,
                commission          REAL,
                balance_after       REAL,
                reason              TEXT,
                hold_hours          REAL,
                opened_at           TEXT,
                closed_at           TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                date            TEXT PRIMARY KEY,
                trades          INTEGER DEFAULT 0,
                funding_earned  REAL    DEFAULT 0.0,
                total_pnl       REAL    DEFAULT 0.0,
                commission      REAL    DEFAULT 0.0,
                balance_end     REAL    DEFAULT 0.0
            );
        """)
        self.conn.commit()

    # ──────────────────────────────────────────
    #  Запись сделки
    # ──────────────────────────────────────────
    def record(
        self,
        symbol:             str,
        direction:          str,
        entry_funding_rate: float,
        exit_funding_rate:  float,
        entry_perp_price:   float,
        exit_perp_price:    float,
        entry_spot_price:   float,
        exit_spot_price:    float,
        perp_qty:           float,
        spot_qty:           float,
        funding_cycles:     int,
        estimated_funding:  float,
        spot_pnl:           float,
        total_pnl:          float,
        commission:         float,
        balance_after:      float,
        reason:             str,
        hold_hours:         float,
        opened_at:          datetime,
    ):
        now   = datetime.now()
        today = date.today().isoformat()
        try:
            self.conn.execute("""
                INSERT INTO trades (
                    symbol, direction,
                    entry_funding_rate, exit_funding_rate,
                    entry_perp_price, exit_perp_price,
                    entry_spot_price, exit_spot_price,
                    perp_qty, spot_qty,
                    funding_cycles, estimated_funding,
                    spot_pnl, total_pnl, commission,
                    balance_after, reason, hold_hours,
                    opened_at, closed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                symbol, direction,
                entry_funding_rate, exit_funding_rate,
                entry_perp_price, exit_perp_price,
                entry_spot_price, exit_spot_price,
                perp_qty, spot_qty,
                funding_cycles, estimated_funding,
                spot_pnl, total_pnl, commission,
                balance_after, reason, hold_hours,
                opened_at.strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d %H:%M:%S"),
            ))

            self.conn.execute("""
                INSERT INTO daily_stats (date, trades, funding_earned, total_pnl, commission, balance_end)
                VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    trades         = trades + 1,
                    funding_earned = funding_earned + excluded.funding_earned,
                    total_pnl      = total_pnl + excluded.total_pnl,
                    commission     = commission + excluded.commission,
                    balance_end    = excluded.balance_end
            """, (today, estimated_funding, total_pnl, commission, balance_after))

            self.conn.commit()
            logger.info(f"TradeHistory: {symbol} | PnL={total_pnl:+.4f} | "
                        f"фандинг={estimated_funding:+.4f} | cycles={funding_cycles}")
        except Exception as e:
            logger.error(f"TradeHistory.record: {e}")

    # ──────────────────────────────────────────
    #  Статистика
    # ──────────────────────────────────────────
    def get_today(self) -> dict:
        today = date.today().isoformat()
        row   = self.conn.execute("""
            SELECT trades, funding_earned, total_pnl, commission, balance_end
            FROM daily_stats WHERE date = ?
        """, (today,)).fetchone()
        if not row:
            return {"trades": 0, "funding_earned": 0.0, "pnl_net": 0.0,
                    "commission": 0.0, "balance_end": 0.0, "win_rate": 0.0}
        t, fund, pnl, comm, bal = row
        wins = self.conn.execute("""
            SELECT COUNT(*) FROM trades
            WHERE date(closed_at) = ? AND total_pnl > 0
        """, (today,)).fetchone()[0]
        return {
            "trades":         t,
            "funding_earned": round(fund or 0, 4),
            "pnl_net":        round(pnl or 0, 4),
            "commission":     round(comm or 0, 4),
            "balance_end":    round(bal or 0, 2),
            "win_rate":       round(wins / t * 100, 1) if t else 0.0,
        }

    def get_all_time(self) -> dict:
        row = self.conn.execute("""
            SELECT COUNT(*),
                   SUM(total_pnl),
                   SUM(estimated_funding),
                   SUM(commission),
                   SUM(CASE WHEN total_pnl > 0 THEN 1 ELSE 0 END),
                   AVG(hold_hours),
                   AVG(entry_funding_rate)
            FROM trades
        """).fetchone()
        if not row or not row[0]:
            return {"trades": 0, "pnl_net": 0.0, "funding_earned": 0.0,
                    "commission": 0.0, "win_rate": 0.0,
                    "avg_hold_hours": 0.0, "avg_funding_rate_pct": 0.0}
        n, pnl, fund, comm, wins, avg_hold, avg_rate = row
        return {
            "trades":              n,
            "pnl_net":             round(pnl or 0, 4),
            "funding_earned":      round(fund or 0, 4),
            "commission":          round(comm or 0, 4),
            "win_rate":            round((wins or 0) / n * 100, 1),
            "avg_hold_hours":      round(avg_hold or 0, 1),
            "avg_funding_rate_pct": round((avg_rate or 0) * 100, 4),
        }

    def get_recent(self, limit: int = 25) -> list:
        rows = self.conn.execute("""
            SELECT symbol, direction, entry_funding_rate, exit_funding_rate,
                   funding_cycles, estimated_funding, total_pnl, reason,
                   hold_hours, closed_at
            FROM trades ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [
            {
                "symbol":       r[0],
                "direction":    r[1],
                "entry_rate":   r[2],
                "exit_rate":    r[3],
                "cycles":       r[4],
                "funding":      r[5],
                "pnl":          r[6],
                "reason":       r[7],
                "hold_hours":   r[8],
                "closed_at":    r[9],
            }
            for r in rows
        ]

    def get_per_symbol(self) -> list:
        rows = self.conn.execute("""
            SELECT symbol,
                   COUNT(*) as trades,
                   SUM(total_pnl) as pnl,
                   SUM(estimated_funding) as funding,
                   AVG(entry_funding_rate) as avg_rate,
                   SUM(CASE WHEN total_pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM trades
            GROUP BY symbol
            ORDER BY pnl DESC
        """).fetchall()
        return [
            {
                "symbol":   r[0],
                "trades":   r[1],
                "pnl_net":  round(r[2] or 0, 4),
                "funding":  round(r[3] or 0, 4),
                "avg_rate": round((r[4] or 0) * 100, 4),
                "win_rate": round((r[5] or 0) / r[1] * 100, 1) if r[1] else 0,
            }
            for r in rows
        ]

    def close(self):
        self.conn.close()
