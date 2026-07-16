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

import assist
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
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        pass
    # Public may wrap amounts in a nested Money-like object or dict — dig one level.
    for attr in ("value", "amount", "total", "raw"):
        inner = getattr(v, attr, None) if not isinstance(v, dict) else v.get(attr)
        if inner is not None:
            try:
                return float(inner)
            except Exception:
                pass
    cents = getattr(v, "cents", None) if not isinstance(v, dict) else v.get("cents")
    try:
        return float(cents) / 100 if cents is not None else None
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
            # Public's portfolio.equity is a LIST of holdings (each {type, value}); the
            # account balance = sum of their values. buying_power is a nested object.
            eq = getattr(pf, "equity", None)
            cash = invested = None
            if isinstance(eq, (list, tuple)):
                total = 0.0; found = False; cashv = 0.0; invv = 0.0
                for it in eq:
                    v = _num(getattr(it, "value", None) if not isinstance(it, dict) else it.get("value"))
                    if v is None:
                        continue
                    found = True; total += v
                    ty = str((getattr(it, "type", "") if not isinstance(it, dict) else it.get("type", "")) or "").upper()
                    if "CASH" in ty:
                        cashv += v
                    else:
                        invv += v
                total = total if found else None
                cash, invested = (cashv, invv) if found else (None, None)
            else:  # flat number formats (fallback)
                invested = _first_num(pf, ("equity", "market_value", "positions_value", "equity_value"))
                cash = _first_num(pf, ("cash", "cash_balance", "available_cash", "settled_cash"))
                total = _first_num(pf, ("account_value", "total_value", "total_equity", "net_liquidation_value"))

            bpobj = getattr(pf, "buying_power", None)
            bp = _num(bpobj)
            if bp is None and bpobj is not None:
                bp = _first_num(bpobj, ("buying_power", "cash_only_buying_power", "options_buying_power"))

            if total is None:
                parts = [x for x in (invested, cash) if x is not None]
                total = sum(parts) if parts else bp

            snap["equity"] = invested
            snap["cash"] = cash
            snap["buying_power"] = bp
            snap["account_value"] = total
            if total is None:  # still unparsed — surface the raw structure to fix later
                try:
                    snap["_portfolio_debug"] = str(pf.model_dump() if hasattr(pf, "model_dump") else vars(pf))[:900]
                except Exception:
                    pass
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
    snap["stopped"] = executor.runtime_stopped()
    snap["ai_connected"] = assist.available()
    snap["force_dry_run"] = config.FORCE_DRY_RUN            # hard-lock: forces dry-run regardless of go-live
    snap["force_manual_approval"] = config.FORCE_MANUAL_APPROVAL  # hard-lock: blocks autonomous auto-approve
    snap["strategies"] = [{
        "id": s.id, "name": s.name, "description": s.description, "rules": s.rules,
        "asset_class": s.asset_class,
        "allocation_usd": float(s.allocation_usd), "enabled": s.enabled,
        "live": bool(s.live), "autonomous": bool(s.autonomous),
        "allowed_symbols": list(s.allowed_symbols or []),
        "deployed": float(deployed.get(s.id, 0) or 0),
        "realized_pl": float(strat_realized.get(s.id, 0) or 0),
        "unrealized_pl": round(unreal.get(s.id, 0.0), 2),
    } for s in strategies.load_strategies()]
    # header aggregates (any bot live / autonomous)
    snap["live"] = any(x["live"] for x in snap["strategies"])
    snap["autonomous"] = any(x["autonomous"] for x in snap["strategies"])
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
            s = by_id.get(sid)
            if s is None:
                if not ch.get("name"):
                    continue  # an override for a bot we don't have + no definition to create
                s = strategies.Strategy(id=sid, name=ch.get("name", sid), description="",
                                        allocation_usd=Decimal("0"))
                strats.append(s); by_id[sid] = s; dirty = True
                log.info("created bot from dashboard: %s", sid)
            for f in ("name", "description", "rules", "asset_class"):
                if f in ch and getattr(s, f) != ch[f]:
                    setattr(s, f, ch[f]); dirty = True
            if "allowed_symbols" in ch:
                nv = [str(x).upper() for x in (ch["allowed_symbols"] or [])]
                if nv != s.allowed_symbols:
                    s.allowed_symbols = nv; dirty = True
            if "allocation_usd" in ch:
                na = Decimal(str(ch["allocation_usd"]))
                if na != s.allocation_usd:
                    s.allocation_usd = na; dirty = True
            for f in ("enabled", "live", "autonomous"):
                if f in ch and bool(ch[f]) != getattr(s, f):
                    setattr(s, f, bool(ch[f])); dirty = True
        if dirty:
            strategies.save_strategies(strats)
            log.info("applied bot changes from dashboard")

    return cfg


def post_assist_result(result: dict):
    """Send the client-LLM's suggestion back to the platform for display."""
    if not enabled():
        return
    try:
        r = requests.post(_url("/api/assist-result"), json=result, headers=_headers(), timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.warning("assist result post failed: %s", e)


def post_chat_reply(text: str, actions=None, reply_to=None, bot_states=None, conversation_id=None):
    """Post the client-AI's chat reply into its conversation (+ a summary of what it
    changed, + the new state of any bots it touched so the dashboard stays in sync)."""
    if not enabled():
        return
    try:
        r = requests.post(_url("/api/chat-reply"),
                          json={"text": text, "actions": actions or [], "reply_to": reply_to,
                                "bot_states": bot_states or {}, "conversation_id": conversation_id},
                          headers=_headers(), timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.warning("chat reply post failed: %s", e)
