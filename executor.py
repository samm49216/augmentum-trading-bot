"""Safe order execution wrapper: preflight -> guardrails -> (dry-run | place).

Placement is GATED by config.DRY_RUN. In dry-run (the default) nothing is sent.
Only the account owner should set DRY_RUN=false. This module never decides WHAT
to trade — it only enforces safety around an order the strategy already produced.
"""
import logging
from decimal import Decimal

import config
import guardrails
from guardrails import OrderIntent

log = logging.getLogger("executor")


def submit(client, intent: OrderIntent, build_order_request):
    """
    client              : an initialized PublicApiClient
    intent              : OrderIntent (symbol/side/asset_class/is_day_trade + optional
                          rough notional estimate from the strategy)
    build_order_request : zero-arg callable returning the SDK OrderRequest for LIVE placement
    """
    if guardrails.kill_switch_active():
        log.warning("HALT engaged — refusing order for %s", intent.symbol)
        return None

    # 1) Preflight: estimate cost WITHOUT hitting the market, and refine notional.
    try:
        estimate = _preflight(client, intent, build_order_request)
        if estimate is not None:
            intent.notional = Decimal(str(estimate))
    except Exception as e:
        log.error("preflight failed for %s: %s — refusing (fail-closed)", intent.symbol, e)
        return None

    # 2) Guardrails (fail-closed).
    ok, reason = guardrails.authorize(intent)
    if not ok:
        log.warning("BLOCKED %s %s: %s", intent.side, intent.symbol, reason)
        return None

    # 3) Dry-run gate.
    if config.DRY_RUN:
        log.info("[DRY-RUN] would place %s %s (~$%s). Nothing sent.",
                 intent.side, intent.symbol, intent.notional)
        return {"dry_run": True, "intent": intent}

    # 4) LIVE placement — reached only when the account owner set DRY_RUN=false.
    log.info("LIVE placing %s %s (~$%s)", intent.side, intent.symbol, intent.notional)
    order = client.place_order(build_order_request())
    result = order.wait_for_terminal_status(timeout=120)
    guardrails.record_fill(intent)
    log.info("order terminal status: %s", getattr(result, "status", result))
    return result


def _preflight(client, intent, build_order_request):
    """Best-effort cost estimate. Returns estimated notional ($) or None.

    Verify the exact call against your installed publicdotcom-py version, e.g.:
        req  = build_order_request()
        calc = client.perform_preflight_calculation(PreflightRequest(order=req),
                                                     validate_order=False)  # what-if, no account checks
        return calc.estimated_cost
    Until implemented this returns None; combined with the guardrail rule
    "notional not estimated => BLOCK", that keeps the bot fail-closed.
    """
    return None
