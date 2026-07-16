"""Bot <-> Moore Platform sync (Path A — read-only data out, config in).

PUSH: a read-only snapshot (portfolio + strategies + pending proposals) to the
hosted dashboard so the client can see it. PULL: config (approvals + allocation
changes + natural-language assist requests) and apply it LOCALLY. No keys ever leave.
"""
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

import config
import executor
import guardrails
import pnl
import proposals
import strategies

log = logging.getLogger("sync")

RUNTIME = Path(__file__).parent / "state" / "runtime.json"


def enabled():
    return bool(config.PLATFORM_URL and config.BOT_TOKEN)


def _url(path):
    return config.PLATFORM_URL.rstrip("/") + path


def _headers():
    return {"Authorization": f"Bearer {config.BOT_TOKEN}"}


def _num(v):
    try:
        return float(v)
    except Exception:
        return None


def _first_num(obj, names):
    """First present, numeric attribute value among `names` (SDK field-name drift)."""
    for n in names:
        v = _num(getattr(obj, n, None))
        if v is not None:
            return v
    return None


def build_snapshot(client):
    snap = {"as_of": datetime.now(timezone.utc).isoformat()}
    if client is not None and config.DEFAULT_ACCOUNT_NUMBER:
        try:
            pf = client.get_portfolio(account_id=config.DEFAULT_ACCOUNT_NUMBER)
            snap["equity"] = _first_num(pf, ("equity", "total_equity", "account_value", "equity_value", "total_value"))
            snap["buying_power"] = _first_num(pf, ("buying_power", "cash", "cash_balance", "available_cash"))
            positions = []
            for pos in getattr(pf, "positions", []) or []:
                inst = getattr(pos, "instrument", None)
                positions.append({
                    "symbol": getattr(inst, "symbol", str(inst)),
                    "quantity": str(getattr(pos, "quantity", "")),
                    "value": _num(getattr(pos, "current_value", 0)),
                    "pct": _num(getattr(pos, "percent_of_portfolio", 0)),
                    "cost_basis": _num(getattr(pos, "cost_basis", None)),
                    "daily_gain": _num(getattr(pos, "position_daily_gain", None)),
                })
            snap["positions"] = positions
        except Exception as e:
            log.warning("portfolio fetch failed (snapshot still sent): %s", e)

    st = guardrails.snapshot_state()
    deployed = st.get("strategy_notional", {})
    ledger = pnl.snapshot()
    sym_strat = ledger.get("symbol_strategy", {})
    strat_realized = ledger.get("strategy_realized", {})

    # Attribute each open position's unrealized P&L to the strategy that traded it.
    unreal = {}
    for pos in snap.get("positions", []):
        sid = sym_strat.get(str(pos.get("symbol", "")).upper())
        cost = pos.get("cost_basis")
        if sid and cost is not None:
            unreal[sid] = unreal.get(sid, 0.0) + (float(pos.get("value") or 0) - float(cost))

    snap["day_trades_used"] = st.get("day_trades", 0)
    snap["realized_pl"] = float(ledger.get("realized_total", 0) or 0)
    snap["live"] = not executor.effective_dry_run()
    snap["autonomous"] = executor.effective_autonomous()
    snap["stopped"] = executor.runtime_stopped()
    snap["strategies"] = [{
        "id": s.id, "name": s.name, "description": s.description,
        "allocation_usd": float(s.allocation_usd), "enabled": s.enabled,
        "deployed": float(deployed.get(s.id, 0) or 0),
        "realized_pl": float(strat_realized.get(s.id, 0) or 0),
        "unrealized_pl": round(unreal.get(s.id, 0.0), 2),
    } for s in strategies.load_strategies()]
    snap["pending_proposals"] = [{
        "id": p["id"], "strategy_id": p["strategy_id"], "symbol": p["symbol"],
        "side": p["side"], "amount": p.get("amount"), "rationale": p.get("rationale", ""),
    } for p in proposals.list_all("pending")]
    return snap


def push_snapshot(client):
    if not enabled():
        return
    try:
        r = requests.post(_url("/api/snapshot"), json=build_snapshot(client), headers=_headers(), timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.warning("snapshot push failed: %s", e)


def pull_and_apply_config():
    """Apply approvals + allocation changes from the platform. Return assist_requests."""
    if not enabled():
        return []
    try:
        r = requests.get(_url("/api/config"), headers=_headers(), timeout=20)
        r.raise_for_status()
        cfg = r.json()
    except Exception as e:
        log.warning("config pull failed: %s", e)
        return []

    # Live + autonomous toggles from the dashboard (the bot's FORCE_DRY_RUN and
    # FORCE_MANUAL_APPROVAL hard-locks can still veto each locally).
    RUNTIME.parent.mkdir(exist_ok=True)
    RUNTIME.write_text(json.dumps({
        "live": bool(cfg.get("live", False)),
        "autonomous": bool(cfg.get("autonomous", False)),
        "stopped": bool(cfg.get("stopped", False)),
    }))

    for pid in cfg.get("approvals", []):
        p = proposals.get(pid)
        if p and p["status"] == "pending":
            proposals.set_status(pid, "approved")
            log.info("approved via platform: %s", pid)

    changes = cfg.get("strategies", {})
    if changes:
        strats = strategies.load_strategies()
        by_id = {s.id: s for s in strats}
        dirty = False
        for sid, ch in changes.items():
            if sid in by_id:
                s = by_id[sid]
                if "allocation_usd" in ch:
                    na = Decimal(str(ch["allocation_usd"]))
                    if na != s.allocation_usd:
                        s.allocation_usd = na
                        dirty = True
                if "enabled" in ch and bool(ch["enabled"]) != s.enabled:
                    s.enabled = bool(ch["enabled"])
                    dirty = True
            elif ch.get("name"):  # a new strategy the client created on the dashboard
                strats.append(strategies.Strategy(
                    id=sid, name=ch.get("name", sid), description=ch.get("description", ""),
                    allocation_usd=Decimal(str(ch.get("allocation_usd", 0))),
                    enabled=bool(ch.get("enabled", True)), asset_class=ch.get("asset_class", "equity"),
                ))
                dirty = True
                log.info("created strategy from platform: %s", sid)
        if dirty:
            strategies.save_strategies(strats)
            log.info("applied strategy changes from platform")

    return cfg.get("assist_requests", [])


def post_assist_result(result: dict):
    """Send the client-LLM's suggestion back to the platform for display."""
    if not enabled():
        return
    try:
        r = requests.post(_url("/api/assist-result"), json=result, headers=_headers(), timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.warning("assist result post failed: %s", e)
