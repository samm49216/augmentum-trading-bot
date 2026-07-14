"""READ-ONLY connectivity + auth check. Places NO orders. Run this first.

    python check_connection.py

Proves your API secret key works and read access is granted. Exact field names
inside the returned objects vary by SDK version — this only confirms connectivity.
"""
import config


def main():
    config.require_credentials()

    try:
        from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
        from public_api_sdk.auth_config import ApiKeyAuthConfig
    except ImportError as e:
        raise SystemExit(f"SDK not installed. Run: pip install -r requirements.txt  ({e})")

    print("== Public connection check (READ-ONLY — no orders) ==")
    client = PublicApiClient(
        ApiKeyAuthConfig(
            api_secret_key=config.API_SECRET_KEY,
            validity_minutes=config.TOKEN_VALIDITY_MINUTES,
        ),
        config=PublicApiClientConfiguration(
            default_account_number=config.DEFAULT_ACCOUNT_NUMBER,
        ),
    )
    try:
        accounts = client.get_accounts()
        print(f"✓ Authenticated. Accounts: {accounts}")

        portfolio = client.get_portfolio(account_id=config.DEFAULT_ACCOUNT_NUMBER)
        print(f"✓ Portfolio snapshot retrieved: {portfolio}")

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
