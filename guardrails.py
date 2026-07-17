"""Risk guardrails. Every order must pass authorize() before placement.

Dollar caps, PDT, and loss limits fail CLOSED (any uncertainty => BLOCK). Symbol
allowlists are OPT-IN: empty = every symbol allowed; list some = restrict to those.
Daily counters persist per-day under ./state/.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import config

STATE_DIR = Path(__file__).parent / "state"
HALT_FILE = Path(__file__).parent / "HALT"


@dataclass
class OrderIntent:
    symbol: str
    side: str                       # "BUY" | "SELL"
    notional: Decimal = None        # estimated $ value; refined by preflight
    is_day_trade: bool = False
    asset_class: str = "equity"     # "equity" | "option" | "crypto"
    strategy_id: str = "default"
    allocation: Decimal = None      # the strategy's capital budget ($), if any
    allowed_symbols: list = None    # this bot's ticker restriction; empty/None = any


def _today_state_path() -> Path:
    STATE_DIR.mkdir(exist_ok=True)
    return STATE_DIR / f"{date.today().isoformat()}.json"


def _load_state() -> dict:
    p = _today_state_path()
    s = json.loads(p.read_text()) if p.exists() else {}
    s.setdefault("notional", "0")
    s.setdefault("day_trades", 0)
    s.setdefault("realized_pl", "0")
    s.setdefault("strategy_notional", {})
    return s


def _save_state(s: dict) -> None:
    _today_state_path().write_text(json.dumps(s))


def kill_switch_active() -> bool:
    return HALT_FILE.exists()


def snapshot_state() -> dict:
    """Read-only view of today's counters (notional, day_trades, per-strategy deployed)."""
    return _load_state()


def authorize(intent: OrderIntent):
    """Return (ok: bool, reason: str). Fails CLOSED on any doubt."""
    if kill_switch_active():
        return False, "HALT file present — kill switch engaged"

    sym = (intent.symbol or "").upper()
    # Allowlists are OPT-IN restrictions — empty means "no restriction" (all symbols
    # allowed). An order must satisfy this bot's own ticker list (if the client set
    # one) AND the fleet-wide list (if the operator set one). Dollar caps, PDT, and
    # loss limits below still fail closed.
    bot_allow = [s.upper() for s in (intent.allowed_symbols or [])]
    if bot_allow and sym not in bot_allow:
        return False, f"{sym} not in this bot's allowed tickers {bot_allow}"
    if config.SYMBOL_ALLOWLIST and sym not in config.SYMBOL_ALLOWLIST:
        return False, f"{sym} not in the account allowlist {config.SYMBOL_ALLOWLIST}"

    if intent.notional is None or Decimal(intent.notional) <= 0:
        return False, "notional not estimated — run preflight before authorizing"
    if Decimal(intent.notional) < config.MIN_ORDER_NOTIONAL:
        return False, f"order notional {intent.notional} < MIN_ORDER_NOTIONAL {config.MIN_ORDER_NOTIONAL} (dust/typo guard)"
    if Decimal(intent.notional) > config.MAX_ORDER_NOTIONAL:
        return False, f"order notional {intent.notional} > MAX_ORDER_NOTIONAL {config.MAX_ORDER_NOTIONAL}"

    state = _load_state()
    day_notional = Decimal(state["notional"]) + Decimal(intent.notional)
    if day_notional > config.MAX_DAILY_NOTIONAL:
        return False, f"daily notional {day_notional} > MAX_DAILY_NOTIONAL {config.MAX_DAILY_NOTIONAL}"

    # PDT guard — only stocks/options in a margin account count; crypto is exempt.
    if intent.is_day_trade and intent.asset_class in ("equity", "option"):
        if state["day_trades"] + 1 > config.MAX_DAY_TRADES:
            return False, f"would exceed MAX_DAY_TRADES {config.MAX_DAY_TRADES} (PDT guard)"

    if Decimal(state["realized_pl"]) <= -config.DAILY_LOSS_LIMIT:
        return False, f"daily loss limit reached ({state['realized_pl']} <= -{config.DAILY_LOSS_LIMIT})"

    # Per-strategy allocation: a strategy can't deploy more than its budget.
    if intent.allocation is not None:
        sid = intent.strategy_id or "default"
        deployed = Decimal(state["strategy_notional"].get(sid, "0")) + Decimal(intent.notional)
        if deployed > Decimal(intent.allocation):
            return False, (f"strategy '{sid}' would exceed its ${intent.allocation} "
                           f"allocation (deployed would be ${deployed})")

    return True, "ok"


def record_fill(intent: OrderIntent, realized_pl_delta: Decimal = Decimal("0")) -> None:
    """Update day counters after a CONFIRMED fill."""
    state = _load_state()
    state["notional"] = str(Decimal(state["notional"]) + Decimal(intent.notional or 0))
    if intent.is_day_trade and intent.asset_class in ("equity", "option"):
        state["day_trades"] += 1
    state["realized_pl"] = str(Decimal(state["realized_pl"]) + Decimal(realized_pl_delta))
    sid = intent.strategy_id or "default"
    sn = state["strategy_notional"]
    sn[sid] = str(Decimal(sn.get(sid, "0")) + Decimal(intent.notional or 0))
    _save_state(state)
