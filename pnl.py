"""Cumulative P&L ledger (persists across days, unlike the daily guardrail state).

- realized_total / strategy_realized: realized P&L booked when a strategy SELLS.
- symbol_strategy: which strategy last traded a symbol — used to attribute open
  positions (and their unrealized P&L) back to a strategy on the dashboard.

Attribution is best-effort (avg-cost; a symbol maps to its most recent strategy).
Realized figures populate once the bot is live and actually fills sells.
"""
import json
from decimal import Decimal
from pathlib import Path

PNL = Path(__file__).parent / "state" / "pnl.json"


def _load():
    if PNL.exists():
        return json.loads(PNL.read_text())
    return {"realized_total": "0", "strategy_realized": {}, "symbol_strategy": {}}


def _save(d):
    PNL.parent.mkdir(exist_ok=True)
    PNL.write_text(json.dumps(d))


def record_buy(strategy_id, symbol):
    d = _load()
    d["symbol_strategy"][symbol.upper()] = strategy_id
    _save(d)


def record_sell(strategy_id, symbol, realized_delta):
    d = _load()
    d["realized_total"] = str(Decimal(d["realized_total"]) + Decimal(str(realized_delta)))
    sr = d["strategy_realized"]
    sr[strategy_id] = str(Decimal(sr.get(strategy_id, "0")) + Decimal(str(realized_delta)))
    d["symbol_strategy"][symbol.upper()] = strategy_id
    _save(d)


def snapshot():
    return _load()
