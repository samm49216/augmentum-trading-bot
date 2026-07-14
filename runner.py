"""Execution daemon. Picks up client-APPROVED proposals and executes them
(through guardrails + the DRY_RUN gate). Generates nothing on its own — trade
ideas come from the client's Claude / the portal as pending proposals, and only
the client's approval moves them here.

Respects the HALT kill switch and DRY_RUN.
"""
import logging
import time

import config
import guardrails
import proposals
import strategies
from executor import execute_proposal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("runner")

POLL_SECONDS = 15


def build_client():
    from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
    from public_api_sdk.auth_config import ApiKeyAuthConfig
    return PublicApiClient(
        ApiKeyAuthConfig(
            api_secret_key=config.API_SECRET_KEY,
            validity_minutes=config.TOKEN_VALIDITY_MINUTES,
        ),
        config=PublicApiClientConfiguration(default_account_number=config.DEFAULT_ACCOUNT_NUMBER),
    )


def main():
    config.require_credentials()
    log.info("Starting. DRY_RUN=%s  account=%s", config.DRY_RUN, config.DEFAULT_ACCOUNT_NUMBER)
    if config.DRY_RUN:
        log.info("DRY_RUN is ON — approved proposals are simulated, no live orders.")

    strat_by_id = {s.id: s for s in strategies.load_strategies()}
    client = build_client()
    try:
        while True:
            if guardrails.kill_switch_active():
                log.warning("HALT present — idling. Remove ./HALT to resume.")
                time.sleep(POLL_SECONDS)
                continue

            for p in proposals.list_all(status="approved"):
                strat = strat_by_id.get(p["strategy_id"])
                execute_proposal(client, p, strat, account_id=config.DEFAULT_ACCOUNT_NUMBER)

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
