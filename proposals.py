"""Propose -> approve -> execute queue (non-discretionary backbone).

Trade ideas (from the client's own Claude, or the client entering them) are added
as PENDING proposals. The client approves/rejects them in the portal. The runner
executes only APPROVED ones, through guardrails + the DRY_RUN gate. Nothing is
ever auto-executed without an explicit client approval.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROPOSALS_PATH = Path(__file__).parent / "state" / "proposals.json"

STATUSES = ("pending", "approved", "rejected", "executing", "executed", "failed")


def _load():
    return json.loads(PROPOSALS_PATH.read_text()) if PROPOSALS_PATH.exists() else []


def _save(items):
    PROPOSALS_PATH.parent.mkdir(exist_ok=True)
    PROPOSALS_PATH.write_text(json.dumps(items, indent=2))


def _now():
    return datetime.now(timezone.utc).isoformat()


def add(strategy_id, symbol, side, asset_class, *, quantity=None, amount=None,
        order_type="MARKET", limit_price=None, rationale="", source="client-claude",
        is_day_trade=False):
    items = _load()
    p = {
        "id": uuid.uuid4().hex[:12],
        "strategy_id": strategy_id,
        "symbol": symbol.upper(),
        "side": side.upper(),
        "asset_class": asset_class.lower(),
        "quantity": quantity,
        "amount": amount,
        "order_type": order_type.upper(),
        "limit_price": limit_price,
        "is_day_trade": bool(is_day_trade),
        "rationale": rationale,
        "source": source,
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
        "result": None,
    }
    items.append(p)
    _save(items)
    return p


def list_all(status=None):
    return [p for p in _load() if status is None or p["status"] == status]


def get(pid):
    for p in _load():
        if p["id"] == pid:
            return p
    return None


def set_status(pid, status, result=None):
    assert status in STATUSES, status
    items = _load()
    for p in items:
        if p["id"] == pid:
            p["status"] = status
            p["updated_at"] = _now()
            if result is not None:
                p["result"] = result
            _save(items)
            return p
    return None
