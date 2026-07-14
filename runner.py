"""Execution daemon + platform sync + AI assist.

Each cycle: pull config from the platform (apply approvals + allocation changes,
collect natural-language assist requests) -> run the client's OWN AI on new assist
requests -> execute client-APPROVED proposals -> push a read-only snapshot back.

Generates no trades on its own. Respects the HALT kill switch and DRY_RUN.
"""
import json
import logging
import time
from pathlib import Path

import assist
import config
import guardrails
import proposals
import strategies
import sync
from executor import execute_proposal, effective_autonomous

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("runner")

POLL_SECONDS = 15
ASSIST_SEEN = Path(__file__).parent / "state" / "assist_seen.json"


def build_client():
    from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
    from public_api_sdk.auth_config import ApiKeyAuthConfig
    return PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=config.API_SECRET_KEY, validity_minutes=config.TOKEN_VALIDITY_MINUTES),
        config=PublicApiClientConfiguration(default_account_number=config.DEFAULT_ACCOUNT_NUMBER),
    )


def _load_seen():
    return set(json.loads(ASSIST_SEEN.read_text())) if ASSIST_SEEN.exists() else set()


def _save_seen(seen):
    ASSIST_SEEN.parent.mkdir(exist_ok=True)
    ASSIST_SEEN.write_text(json.dumps(sorted(seen)))


def process_assist(client, requests_):
    """Run the client's own AI on new NL requests; turn proposed trades into pending
    proposals and push the suggestions back to the platform for display."""
    if not requests_ or not assist.available():
        return
    seen = _load_seen()
    strat_ctx = [{
        "id": s.id, "name": s.name, "description": s.description,
        "allocation_usd": float(s.allocation_usd), "enabled": s.enabled, "asset_class": s.asset_class,
    } for s in strategies.load_strategies()]
    port_ctx = {}
    try:
        if config.DEFAULT_ACCOUNT_NUMBER:
            pf = client.get_portfolio(account_id=config.DEFAULT_ACCOUNT_NUMBER)
            port_ctx = {"equity": str(getattr(pf, "equity", "")),
                        "buying_power": str(getattr(pf, "buying_power", ""))}
    except Exception as e:
        log.warning("portfolio context unavailable for assist: %s", e)

    for r in requests_:
        rid = r.get("id")
        if not rid or rid in seen:
            continue
        log.info("assist: reviewing request %s", rid)
        sugg = assist.run_assist(r.get("text", ""), strat_ctx, port_ctx)
        seen.add(rid)  # mark seen even on failure so we don't loop on it
        if sugg is not None:
            for t in sugg.proposed_trades:
                try:
                    proposals.add(t.strategy_id, t.symbol, t.side, t.asset_class,
                                  amount=t.amount, rationale=t.rationale, source="assist-ai")
                except Exception as e:
                    log.warning("assist proposed trade rejected: %s", e)
            sync.post_assist_result({
                "request_id": rid,
                "text": r.get("text", ""),
                "summary": sugg.summary,
                "allocation_changes": [ac.model_dump() for ac in sugg.allocation_changes],
                "notes": sugg.notes,
            })
        _save_seen(seen)


def autonomous_tick(client):
    """Autonomous mode: the client's OWN AI proposes trades to place now, within
    their enabled strategies + risk limits. Adds them as proposals (which the loop
    then auto-approves + executes). Guardrails still gate every order."""
    if not assist.available():
        log.warning("autonomous is ON but no ANTHROPIC_API_KEY is set — can't generate "
                    "trades. Add your key to .env to enable autonomous generation.")
        return
    strat_ctx = [{
        "id": s.id, "name": s.name, "description": s.description,
        "allocation_usd": float(s.allocation_usd), "enabled": s.enabled, "asset_class": s.asset_class,
    } for s in strategies.load_strategies() if s.enabled]
    if not strat_ctx:
        log.info("autonomous tick: no enabled strategies — nothing to do.")
        return
    port_ctx = {}
    try:
        if config.DEFAULT_ACCOUNT_NUMBER:
            pf = client.get_portfolio(account_id=config.DEFAULT_ACCOUNT_NUMBER)
            port_ctx = {"equity": str(getattr(pf, "equity", "")),
                        "buying_power": str(getattr(pf, "buying_power", ""))}
    except Exception as e:
        log.warning("portfolio context unavailable for autonomous tick: %s", e)

    sugg = assist.run_autonomous(strat_ctx, port_ctx, max_trades=config.MAX_AUTONOMOUS_TRADES_PER_TICK)
    if sugg is None:
        return
    added = 0
    for t in sugg.proposed_trades[: config.MAX_AUTONOMOUS_TRADES_PER_TICK]:
        try:
            proposals.add(t.strategy_id, t.symbol, t.side, t.asset_class,
                          amount=t.amount, rationale=t.rationale, source="autonomous-ai")
            added += 1
        except Exception as e:
            log.warning("autonomous proposed trade rejected: %s", e)
    log.info("autonomous tick: generated %s proposal(s). %s", added, (sugg.summary or "")[:200])


def main():
    config.require_credentials()
    log.info("Starting. DRY_RUN=%s  autonomous=%s  account=%s  platform=%s  assist=%s",
             config.DRY_RUN, effective_autonomous(), config.DEFAULT_ACCOUNT_NUMBER,
             "on" if sync.enabled() else "off", "on" if assist.available() else "off")
    if config.DRY_RUN:
        log.info("DRY_RUN is ON — approved proposals are simulated, no live orders.")
    if config.FORCE_MANUAL_APPROVAL:
        log.info("FORCE_MANUAL_APPROVAL is ON — autonomous is hard-locked off; every trade needs manual approval.")
    elif effective_autonomous():
        log.warning("AUTONOMOUS is ON — proposals will be auto-approved and executed "
                    "without manual approval (still gated by guardrails + dry-run).")

    client = build_client()
    last_auto = 0.0
    try:
        while True:
            if guardrails.kill_switch_active():
                log.warning("HALT present — idling. Remove ./HALT to resume.")
                time.sleep(POLL_SECONDS)
                continue

            assist_requests = sync.pull_and_apply_config()   # applies approvals + allocation changes
            auto = effective_autonomous()

            # Autonomous generation: the client's OWN AI proposes trades (rate-limited).
            if auto and (time.time() - last_auto >= config.AUTONOMOUS_TICK_SECONDS):
                log.info("autonomous mode ON — running strategy tick")
                autonomous_tick(client)
                last_auto = time.time()

            # Turn any new natural-language requests into pending proposals.
            process_assist(client, assist_requests)

            # Autonomous mode auto-approves pending proposals (no manual approval needed).
            if auto:
                for p in proposals.list_all(status="pending"):
                    proposals.set_status(p["id"], "approved")
                    log.info("autonomous auto-approve: %s %s (%s)", p["side"], p["symbol"], p["id"])

            strat_by_id = {s.id: s for s in strategies.load_strategies()}
            for p in proposals.list_all(status="approved"):
                execute_proposal(client, p, strat_by_id.get(p["strategy_id"]),
                                 account_id=config.DEFAULT_ACCOUNT_NUMBER)

            sync.push_snapshot(client)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("Stopping.")
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
