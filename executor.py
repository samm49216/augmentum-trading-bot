"""Execute an APPROVED proposal: preflight -> guardrails -> (dry-run | place).

Placement is GATED by config.DRY_RUN (default on). Nothing is auto-generated here;
this only acts on a proposal the client already approved. Order/preflight objects
are built from the installed publicdotcom-py models.
"""
import json
import logging
import uuid
from decimal import Decimal
from pathlib import Path

import config
import guardrails
import pnl
import proposals
from guardrails import OrderIntent

log = logging.getLogger("executor")

RUNTIME = Path(__file__).parent / "state" / "runtime.json"


def _runtime_live():
    """True/False from the platform (dashboard toggle), or None if not connected."""
    try:
        if RUNTIME.exists():
            return bool(json.loads(RUNTIME.read_text()).get("live"))
    except Exception:
        pass
    return None


def effective_dry_run(strategy=None):
    """Resolve dry-run. Fail-safe order: the local hard-lock (FORCE_DRY_RUN) and a
    dashboard STOP always force dry-run. Otherwise a specific bot trades live ONLY if
    that bot is explicitly set live; with no bot context, fall back to the
    account-level flag / local default."""
    if config.FORCE_DRY_RUN:
        return True
    if runtime_stopped():
        return True
    if strategy is not None:
        return not bool(getattr(strategy, "live", False))
    live = _runtime_live()
    return (not live) if live is not None else config.DRY_RUN


def _runtime_autonomous():
    """True/False from the platform (dashboard toggle), or None if not connected."""
    try:
        if RUNTIME.exists():
            return bool(json.loads(RUNTIME.read_text()).get("autonomous"))
    except Exception:
        pass
    return None


def effective_autonomous():
    """Resolve autonomous mode: the local hard-lock (FORCE_MANUAL_APPROVAL) always
    wins and forces manual approval; else the dashboard's autonomous flag if
    connected; else the local AUTONOMOUS default."""
    if config.FORCE_MANUAL_APPROVAL:
        return False
    auto = _runtime_autonomous()
    return auto if auto is not None else config.AUTONOMOUS


def runtime_stopped():
    """Dashboard emergency-STOP flag (hard halt) from runtime.json."""
    try:
        if RUNTIME.exists():
            return bool(json.loads(RUNTIME.read_text()).get("stopped"))
    except Exception:
        pass
    return False


def _position(client, symbol, account_id):
    """(cost_basis, current_value) for a held symbol, for realized-P&L math."""
    try:
        pf = client.get_portfolio(account_id=account_id)
        for pos in getattr(pf, "positions", []) or []:
            inst = getattr(pos, "instrument", None)
            if getattr(inst, "symbol", None) == symbol:
                cb, cv = getattr(pos, "cost_basis", None), getattr(pos, "current_value", None)
                if cb is not None and cv:
                    return (Decimal(str(cb)), Decimal(str(cv)))
    except Exception as e:
        log.warning("cost-basis lookup failed for %s: %s", symbol, e)
    return None


def _instrument_type(asset_class):
    from public_api_sdk import InstrumentType
    return {
        "crypto": InstrumentType.CRYPTO,
        "equity": InstrumentType.EQUITY,
        "option": InstrumentType.OPTION,
        "index": InstrumentType.INDEX,
    }.get((asset_class or "equity").lower(), InstrumentType.EQUITY)


def _common_kwargs(p):
    """Fields shared by OrderRequest and PreflightRequest."""
    from public_api_sdk import OrderInstrument, OrderExpirationRequest, OrderSide, OrderType, TimeInForce
    k = dict(
        instrument=OrderInstrument(symbol=p["symbol"], type=_instrument_type(p["asset_class"])),
        order_side=OrderSide[p["side"].upper()],
        order_type=OrderType[p["order_type"].upper()],
        expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY, expiration_time=None),
    )
    if p.get("quantity") is not None:
        k["quantity"] = Decimal(str(p["quantity"]))
    if p.get("amount") is not None:
        k["amount"] = Decimal(str(p["amount"]))
    if p.get("limit_price") is not None:
        k["limit_price"] = Decimal(str(p["limit_price"]))
    return k


def build_order_request(p):
    from public_api_sdk import OrderRequest
    return OrderRequest(order_id=uuid.uuid4().hex, **_common_kwargs(p))


def build_preflight_request(p):
    from public_api_sdk import PreflightRequest
    return PreflightRequest(validate_order=False, **_common_kwargs(p))


def _estimate_notional(client, p, account_id):
    """What-if cost estimate via preflight (no market impact). Returns Decimal or None."""
    try:
        resp = client.perform_preflight_calculation(build_preflight_request(p), account_id=account_id)
        val = getattr(resp, "order_value", None)
        if val is None:
            val = getattr(resp, "estimated_cost", None)
        return Decimal(str(val)) if val is not None else None
    except Exception as e:
        log.error("preflight failed for %s: %s", p["symbol"], e)
        return None


def execute_proposal(client, p, strategy, account_id=None):
    """Run one approved proposal through the safe flow, updating its status."""
    if guardrails.kill_switch_active():
        log.warning("HALT engaged — skipping proposal %s", p["id"])
        return
    proposals.set_status(p["id"], "executing")

    intent = OrderIntent(
        symbol=p["symbol"], side=p["side"], asset_class=p["asset_class"],
        strategy_id=p["strategy_id"],
        allocation=(strategy.allocation_usd if strategy else None),
        is_day_trade=bool(p.get("is_day_trade", False)),
    )

    est = _estimate_notional(client, p, account_id)
    if est is None and p.get("amount") is not None:
        est = Decimal(str(p["amount"]))
    intent.notional = est

    ok, reason = guardrails.authorize(intent)
    if not ok:
        log.warning("BLOCKED %s %s: %s", p["side"], p["symbol"], reason)
        proposals.set_status(p["id"], "failed", {"blocked": reason})
        return

    if effective_dry_run(strategy):
        log.info("[DRY-RUN] would place %s %s (~$%s). Nothing sent.", p["side"], p["symbol"], intent.notional)
        proposals.set_status(p["id"], "executed", {"dry_run": True, "est_notional": str(intent.notional)})
        return

    side = p["side"].upper()
    presell = _position(client, p["symbol"], account_id) if side == "SELL" else None
    try:
        order = client.place_order(build_order_request(p), account_id=account_id)
        result = order.wait_for_terminal_status(timeout=120)
        guardrails.record_fill(intent)
        if side == "BUY":
            pnl.record_buy(p["strategy_id"], p["symbol"])
        elif side == "SELL":
            proceeds = Decimal(str(intent.notional or 0))
            realized = Decimal("0")
            if presell and presell[1]:
                realized = proceeds * (Decimal("1") - (presell[0] / presell[1]))
            pnl.record_sell(p["strategy_id"], p["symbol"], realized)
        status = str(getattr(result, "status", result))
        log.info("order %s terminal status: %s", p["id"], status)
        proposals.set_status(p["id"], "executed", {"status": status})
    except Exception as e:
        log.error("place failed for %s: %s", p["symbol"], e)
        proposals.set_status(p["id"], "failed", {"error": str(e)})
