"""Bot <-> Moore Platform sync (Path A — read-only data out, config in).

PUSH: a read-only snapshot (portfolio + strategies + pending proposals) to the
hosted dashboard so the client can see it. PULL: config (approvals + allocation
changes + natural-language assist requests) and apply it LOCALLY. No keys ever leave.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

import requests

import config
import guardrails
import proposals
import strategies

log = logging.getLogger("sync")


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


def build_snapshot(client):
    snap = {"as_of": datetime.now(timezone.utc).isoformat()}
    if client is not None and config.DEFAULT_ACCOUNT_NUMBER:
        try:
            pf = client.get_portfolio(account_id=config.DEFAULT_ACCOUNT_NUMBER)
            snap["equity"] = _num(getattr(pf, "equity", None))
            snap["buying_power"] = _num(getattr(pf, "buying_power", None))
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
    snap["day_trades_used"] = st.get("day_trades", 0)
    snap["strategies"] = [{
        "id": s.id, "name": s.name, "description": s.description,
        "allocation_usd": float(s.allocation_usd), "enabled": s.enabled,
        "deployed": float(deployed.get(s.id, 0) or 0),
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

    for pid in cfg.get("approvals", []):
        p = proposals.get(pid)
        if p and p["status"] == "pending":
            proposals.set_status(pid, "approved")
            log.info("approved via platform: %s", pid)

    changes = cfg.get("strategies", {})
    if changes:
        strats = strategies.load_strategies()
        dirty = False
        for s in strats:
            if s.id in changes and "allocation_usd" in changes[s.id]:
                new_alloc = Decimal(str(changes[s.id]["allocation_usd"]))
                if new_alloc != s.allocation_usd:
                    s.allocation_usd = new_alloc
                    dirty = True
        if dirty:
            strategies.save_strategies(strats)
            log.info("applied allocation changes from platform")

    return cfg.get("assist_requests", [])


def post_assist_result(result: dict):
    """Send the client-LLM's suggestion back to the platform for display."""
    if not enabled():
        return
    try:
        r = requests.post(_url("/api/assist_result"), json=result, headers=_headers(), timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.warning("assist result post failed: %s", e)
