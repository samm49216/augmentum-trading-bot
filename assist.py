"""Natural-language assist — runs on the CLIENT's own bot with the CLIENT's own
Anthropic key. The client types a request in the portal; their OWN AI reviews
their strategies + portfolio and returns structured suggestions. The operator's
servers never generate this advice (compliance posture: the advice is the
client's own AI's, not the operator's).

Suggestions are non-binding: allocation changes are shown for the client to apply,
and proposed trades become pending proposals the client must still approve.
"""
import json
import logging
from typing import List

from pydantic import BaseModel

import config

log = logging.getLogger("assist")

SYSTEM = (
    "You are the account owner's own trading assistant, running privately on their "
    "machine with their own API key. They fully control their account and approve "
    "every trade themselves. Given their current strategies (each a capital bucket "
    "with a dollar allocation) and a portfolio snapshot, interpret their plain-English "
    "request and propose concrete, conservative adjustments.\n\n"
    "Return: a short plain-English summary; allocation_changes (adjust a strategy's "
    "dollar allocation, with a one-line reason); proposed_trades (specific orders that "
    "fit a strategy and its asset class — the owner will still approve each one); and "
    "notes (risks/caveats). Respect the owner's stated risk and budget. Only propose "
    "trades in an existing strategy's asset class. Keep proposed_trades small and few. "
    "This is the owner's own tool, not investment advice from any third party."
)


class AllocationChange(BaseModel):
    strategy_id: str
    new_allocation: float
    reason: str


class ProposedTrade(BaseModel):
    strategy_id: str
    symbol: str
    side: str          # "BUY" | "SELL"
    asset_class: str   # "crypto" | "equity" | "option"
    amount: float      # dollar amount
    rationale: str


class AssistSuggestion(BaseModel):
    summary: str
    allocation_changes: List[AllocationChange]
    proposed_trades: List[ProposedTrade]
    notes: str


def available() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def run_assist(request_text: str, strategies_ctx: list, portfolio_ctx: dict):
    """Call the client's own Claude for structured suggestions. Returns an
    AssistSuggestion or None (best-effort; never raises)."""
    if not available():
        return None
    try:
        import anthropic
    except ImportError:
        log.error("anthropic SDK not installed (pip install anthropic)")
        return None

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    user_content = (
        f"Current strategies:\n{json.dumps(strategies_ctx, indent=2)}\n\n"
        f"Portfolio snapshot:\n{json.dumps(portfolio_ctx, indent=2)}\n\n"
        f'The account owner asked:\n"{request_text}"\n\n'
        "Propose adjustments per your instructions."
    )
    try:
        resp = client.messages.parse(
            model=config.ANTHROPIC_MODEL,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            output_format=AssistSuggestion,
        )
        return resp.parsed_output
    except Exception as e:
        log.error("assist call failed: %s", e)
        return None
