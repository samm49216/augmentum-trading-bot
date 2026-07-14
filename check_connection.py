"""READ-ONLY connectivity + auth check. Places NO orders. Run this first.

    python check_connection.py

Needs only API_SECRET_KEY to list your accounts. If DEFAULT_ACCOUNT_NUMBER is
also set, it fetches that account's portfolio too. No orders are ever placed.
"""
import config


def main():
    config.require_api_key()

    try:
        from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
        from public_api_sdk.auth_config import ApiKeyAuthConfig
    except ImportError as e:
        raise SystemExit(f"SDK not installed. Run: pip install -r requirements.txt  ({e})")

    print("== Public connection check (READ-ONLY — no orders) ==")

    cfg = (PublicApiClientConfiguration(default_account_number=config.DEFAULT_ACCOUNT_NUMBER)
           if config.DEFAULT_ACCOUNT_NUMBER else PublicApiClientConfiguration())
    client = PublicApiClient(
        ApiKeyAuthConfig(
            api_secret_key=config.API_SECRET_KEY,
            validity_minutes=config.TOKEN_VALIDITY_MINUTES,
        ),
        config=cfg,
    )
    try:
        accounts = client.get_accounts()
        print("✓ Authenticated. Accounts:")
        print(accounts)

        if config.DEFAULT_ACCOUNT_NUMBER:
            portfolio = client.get_portfolio(account_id=config.DEFAULT_ACCOUNT_NUMBER)
            print(f"\n✓ Portfolio snapshot for {config.DEFAULT_ACCOUNT_NUMBER}:")
            print(portfolio)
        else:
            print("\nℹ️  DEFAULT_ACCOUNT_NUMBER isn't set. Copy your account number from "
                  "the accounts above into .env, then re-run to also see the portfolio.")

        print("\nRead access OK. No orders were placed.")
    except Exception as e:
        raise SystemExit(f"✗ Read check failed: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
