"""
dashboard.py — HTML-дашборд для Funding Rate Arbitrage бота
Открыть: двойной клик на dashboard.html в папке проекта
"""

from datetime import datetime
from strategy import HedgedPosition, annualized_rate


def generate(
    balance:          float,
    active_positions: dict,   # {symbol: HedgedPosition}
    trade_history,            # TradeHistory instance
    funding_data:     dict,   # {symbol: {"rate": float, ...}}
) -> str:

    now      = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    today    = trade_history.get_today()
    all_time = trade_history.get_all_time()
    recent   = trade_history.get_recent(25)
    per_sym  = trade_history.get_per_symbol()

    def pnl_color(v):
        return "#2ecc71" if v >= 0 else "#e74c3c"

    # ── Активные позиции ────────────────────────────────────────────
    pos_rows = ""
    for sym, pos in active_positions.items():
        rate     = pos.entry_funding_rate
        apy      = annualized_rate(rate) * 100
        age_h    = pos.age_hours()
        est_fund = pos.estimated_funding
        cycles   = pos.funding_cycles_held
        pos_rows += f"""
        <tr>
          <td><b>{sym}</b></td>
          <td><code>{rate*100:.4f}%</code></td>
          <td><code>{apy:.1f}%</code></td>
          <td>{cycles}</td>
          <td>{age_h:.1f}h</td>
          <td style="color:#2ecc71">+{est_fund:.4f}</td>
          <td><code>{pos.perp_qty:.6f}</code></td>
          <td><code>{pos.entry_perp_price:,.2f}</code></td>
        </tr>"""

    # ── Топ ставки фандинга (рынок) ─────────────────────────────────
    top_rates = sorted(
        [(s, d["rate"]) for s, d in funding_data.items() if d.get("rate", 0) > 0],
        key=lambda x: x[1], reverse=True
    )[:10]
    rates_rows = ""
    for sym, rate in top_rates:
        apy = annualized_rate(rate) * 100
        in_pos = "🟢 Открыта" if sym in active_positions else ""
        color  = "#f39c12" if rate >= 0.001 else ("#e67e22" if rate >= 0.0005 else "#e0e0e0")
        rates_rows += f"""
        <tr>
          <td>{sym}</td>
          <td style="color:{color}"><code>{rate*100:.4f}%</code></td>
          <td><code>{apy:.1f}%</code></td>
          <td>{in_pos}</td>
        </tr>"""

    # ── История сделок ──────────────────────────────────────────────
    trades_rows = ""
    for t in recent:
        color   = "#2ecc71" if (t.get("pnl") or 0) >= 0 else "#e74c3c"
        pnl_str = f"+{t['pnl']:.4f}" if (t.get("pnl") or 0) >= 0 else f"{t['pnl']:.4f}"
        ts      = (t.get("closed_at") or "")[:16]
        rate_str = f"{(t.get('entry_rate') or 0)*100:.4f}%"
        trades_rows += f"""
        <tr>
          <td>{ts}</td>
          <td><b>{t.get('symbol','')}</b></td>
          <td>{rate_str}</td>
          <td>{t.get('cycles', 0)}</td>
          <td>{t.get('hold_hours', 0):.1f}h</td>
          <td style="color:#2ecc71">+{t.get('funding', 0):.4f}</td>
          <td style="color:{color};font-weight:bold">{pnl_str}</td>
          <td>{t.get('reason','')}</td>
        </tr>"""

    # ── Статистика по символам ──────────────────────────────────────
    sym_rows = ""
    for s in per_sym:
        color = "#2ecc71" if s["pnl_net"] >= 0 else "#e74c3c"
        sym_rows += f"""
        <tr>
          <td>{s['symbol']}</td>
          <td>{s['trades']}</td>
          <td>{s['win_rate']}%</td>
          <td style="color:#2ecc71">+{s['funding']:.4f}</td>
          <td style="color:{color}">{s['pnl_net']:+.4f}</td>
          <td>{s['avg_rate']:.4f}%</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="30">
  <title>Funding Rate Arb Bot</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0d0d1a;
      color: #e0e0e0;
      font-family: 'Courier New', monospace;
      font-size: 13px;
      padding: 20px;
    }}
    h1 {{ color: #f39c12; margin-bottom: 4px; font-size: 22px; }}
    h2 {{ color: #3498db; margin: 22px 0 8px; font-size: 14px;
          border-bottom: 1px solid #1e1e30; padding-bottom: 5px; text-transform: uppercase; }}
    .sub {{ color: #7f8c8d; font-size: 11px; margin-bottom: 20px; }}
    .cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 22px; }}
    .card {{
      background: #151525; border: 1px solid #1e1e35;
      border-radius: 8px; padding: 14px 18px; min-width: 155px;
    }}
    .card .label {{ color: #7f8c8d; font-size: 11px; margin-bottom: 5px; }}
    .card .value {{ font-size: 22px; font-weight: bold; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 18px; }}
    th {{
      background: #151525; color: #3498db;
      padding: 7px 10px; text-align: left;
      font-size: 11px; text-transform: uppercase;
    }}
    td {{ padding: 6px 10px; border-bottom: 1px solid #151525; }}
    tr:hover td {{ background: #151525; }}
    code {{ background: #1a1a2e; padding: 1px 5px; border-radius: 3px; font-size: 12px; }}
  </style>
</head>
<body>

<h1>💰 Funding Rate Arbitrage Bot</h1>
<div class="sub">Обновлено: {now} &nbsp;·&nbsp; Автообновление каждые 30 сек</div>

<!-- КАРТОЧКИ МЕТРИК -->
<div class="cards">
  <div class="card">
    <div class="label">Баланс USDT</div>
    <div class="value">{balance:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Открытых позиций</div>
    <div class="value" style="color:#f39c12">{len(active_positions)}</div>
  </div>
  <div class="card">
    <div class="label">Фандинг сегодня</div>
    <div class="value" style="color:#2ecc71">+{today.get('funding_earned',0):.4f}</div>
  </div>
  <div class="card">
    <div class="label">PnL сегодня</div>
    <div class="value" style="color:{pnl_color(today.get('pnl_net',0))}">{today.get('pnl_net',0):+.4f}</div>
  </div>
  <div class="card">
    <div class="label">Сделок сегодня</div>
    <div class="value">{today.get('trades',0)}</div>
  </div>
  <div class="card">
    <div class="label">Фандинг всего</div>
    <div class="value" style="color:#2ecc71">+{all_time.get('funding_earned',0):.4f}</div>
  </div>
  <div class="card">
    <div class="label">PnL всего</div>
    <div class="value" style="color:{pnl_color(all_time.get('pnl_net',0))}">{all_time.get('pnl_net',0):+.4f}</div>
  </div>
  <div class="card">
    <div class="label">Win Rate</div>
    <div class="value">{all_time.get('win_rate',0)}%</div>
  </div>
  <div class="card">
    <div class="label">Сред. удержание</div>
    <div class="value">{all_time.get('avg_hold_hours',0):.1f}h</div>
  </div>
</div>

<!-- АКТИВНЫЕ ПОЗИЦИИ -->
<h2>🟢 Активные позиции ({len(active_positions)})</h2>
<table>
  <tr>
    <th>Символ</th><th>Ставка/8ч</th><th>APY</th><th>Циклов</th>
    <th>Возраст</th><th>Фандинг $</th><th>Qty</th><th>Цена входа</th>
  </tr>
  {pos_rows if pos_rows else '<tr><td colspan="8" style="color:#7f8c8d">Нет открытых позиций</td></tr>'}
</table>

<!-- СТАВКИ ФАНДИНГА РЫНКА -->
<h2>📡 Топ ставки фандинга (рынок)</h2>
<table>
  <tr><th>Символ</th><th>Ставка / 8ч</th><th>APY (оценка)</th><th>Статус</th></tr>
  {rates_rows if rates_rows else '<tr><td colspan="4" style="color:#7f8c8d">Загрузка...</td></tr>'}
</table>

<!-- СТАТИСТИКА ПО СИМВОЛАМ -->
<h2>📊 Статистика по символам</h2>
<table>
  <tr>
    <th>Символ</th><th>Сделок</th><th>Win Rate</th>
    <th>Фандинг $</th><th>PnL net</th><th>Сред. ставка</th>
  </tr>
  {sym_rows if sym_rows else '<tr><td colspan="6" style="color:#7f8c8d">Нет данных</td></tr>'}
</table>

<!-- ИСТОРИЯ СДЕЛОК -->
<h2>🕐 Последние 25 сделок</h2>
<table>
  <tr>
    <th>Закрыто</th><th>Символ</th><th>Ставка вх.</th>
    <th>Циклов</th><th>Удержание</th><th>Фандинг $</th><th>PnL</th><th>Причина</th>
  </tr>
  {trades_rows if trades_rows else '<tr><td colspan="8" style="color:#7f8c8d">Нет сделок</td></tr>'}
</table>

</body>
</html>"""
    return html


def save(filepath: str, html: str):
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
