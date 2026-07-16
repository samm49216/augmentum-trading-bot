"""Multi-strategy model.

A "strategy" here is a named capital bucket with an allocation, a plain-English
description (shown in the client portal), and an asset class. A client can run
several at once with different dollar allocations. Trade *decisions* come from
the client's own Claude / choices (proposals); this module just defines the
buckets and their limits.
"""
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

STRATEGIES_PATH = Path(__file__).parent / "strategies.json"
EXAMPLE_PATH = Path(__file__).parent / "strategies.example.json"


@dataclass
class Strategy:
    """A "bot": a named capital bucket with a plain-English mandate (rules), its own
    dollar allocation, and its own execution state (enabled / live / autonomous)."""
    id: str
    name: str
    description: str
    allocation_usd: Decimal
    enabled: bool = True
    asset_class: str = "equity"     # equity | crypto | option
    params: dict = field(default_factory=dict)
    rules: str = ""                 # free-text trading mandate the client's AI acts on
    live: bool = False              # per-bot: real orders (True) vs dry-run (False)
    autonomous: bool = False        # per-bot: trade 24/7 without per-trade approval
    allowed_symbols: list = field(default_factory=list)  # ticker restriction; empty = any


def load_strategies(path: Path = STRATEGIES_PATH):
    if not path.exists():
        # strategies.json is local (gitignored) so the bot can edit it without
        # colliding with git self-updates. Bootstrap it from the shipped example.
        if EXAMPLE_PATH.exists():
            path.write_text(EXAMPLE_PATH.read_text())
        else:
            return []
    raw = json.loads(path.read_text())
    out = []
    for s in raw.get("strategies", []):
        out.append(Strategy(
            id=s["id"],
            name=s.get("name", s["id"]),
            description=s.get("description", ""),
            allocation_usd=Decimal(str(s.get("allocation_usd", 0))),
            enabled=bool(s.get("enabled", True)),
            asset_class=s.get("asset_class", "equity"),
            params=s.get("params", {}),
            rules=s.get("rules", ""),
            live=bool(s.get("live", False)),
            autonomous=bool(s.get("autonomous", False)),
            allowed_symbols=list(s.get("allowed_symbols", []) or []),
        ))
    return out


def get_strategy(strategy_id, path: Path = STRATEGIES_PATH):
    for s in load_strategies(path):
        if s.id == strategy_id:
            return s
    return None


def save_strategies(strats, path: Path = STRATEGIES_PATH):
    data = {"strategies": [
        {
            "id": s.id, "name": s.name, "description": s.description,
            "allocation_usd": float(s.allocation_usd), "enabled": bool(s.enabled),
            "asset_class": s.asset_class, "params": s.params,
            "rules": s.rules, "live": bool(s.live), "autonomous": bool(s.autonomous),
            "allowed_symbols": list(s.allowed_symbols or []),
        }
        for s in strats
    ]}
    path.write_text(json.dumps(data, indent=2))

