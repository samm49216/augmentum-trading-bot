"""Main loop skeleton. By default trades NOTHING (empty strategy + DRY_RUN on).

Wires: read-only market data -> Strategy.decide() -> executor.submit().
Respects the HALT kill switch and the DRY_RUN gate.
"""
import logging
import time

import config
import guardrails
from executor import submit
from strategy import Strategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("runner")

POLL_SECONDS = 30


def build_client():
    from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
    from public_api_sdk.auth_config import ApiKeyAuthConfig
    return PublicApiClient(
        ApiKeyAuthConfig(
            api_secret_key=config.API_SECRET_KEY,
            validity_minutes=config.TOKEN_VALIDITY_MINUTES,
        ),
        config=PublicApiClientConfiguration(
            default_account_number=config.DEFAULT_ACCOUNT_NUMBER,
        ),
    )


def main():
    config.require_credentials()
    log.info("Starting. DRY_RUN=%s  allowlist=%s", config.DRY_RUN, config.SYMBOL_ALLOWLIST)
    if config.DRY_RUN:
        log.info("DRY_RUN is ON — no live orders will be placed.")

    client = build_client()
    strategy = Strategy()
    try:
        while True:
            if guardrails.kill_switch_active():
                log.warning("HALT present — idling. Remove ./HALT to resume.")
                time.sleep(POLL_SECONDS)
                continue

            # TODO(owner): gather the read-only market data your strategy needs
            # (e.g. client.get_quotes([...]), client.get_bars(...)).
            market = {}

            for intent, build_order_request in strategy.decide(market):
                submit(client, intent, build_order_request)

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
