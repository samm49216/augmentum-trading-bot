"""READ-ONLY connectivity + account diagnostic. Places NO orders. Run this first.

    python check_connection.py

Needs only API_SECRET_KEY. Lists every account you have with its balances, and
flags whether DEFAULT_ACCOUNT_NUMBER in your .env points at the funded one — the
usual reason a funded account shows $0 on the dashboard. No orders are ever placed.
"""
import config


def _num(v):
    try:
        return float(v)
    except Exception:
        return None


def _first_attr(obj, names):
    """Return (field_name, value) for the first present, non-None attribute."""
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return n, v
    return None, None


def _acct_number(a):
    for n in ("account_number", "account_id", "accountNumber", "id", "number"):
        v = getattr(a, n, None)
        if v:
            return str(v)
    return None


def _accounts_list(accounts):
    inner = getattr(accounts, "accounts", None)
    if inner:
        return list(inner)
    if isinstance(accounts, (list, tuple)):
        return list(accounts)
    return [accounts]


def main():
    config.require_api_key()
    try:
        from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
        from public_api_sdk.auth_config import ApiKeyAuthConfig
    except ImportError as e:
        raise SystemExit(f"SDK not installed. Run: pip install -r requirements.txt  ({e})")

    print("== Public connection check (READ-ONLY — no orders) ==\n")
    client = PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=config.API_SECRET_KEY, validity_minutes=config.TOKEN_VALIDITY_MINUTES),
        config=PublicApiClientConfiguration(),
    )
    try:
        accounts = _accounts_list(client.get_accounts())
        print(f"✓ Authenticated. Found {len(accounts)} account(s).\n")

        funded = []
        for a in accounts:
            number = _acct_number(a)
            atype = getattr(a, "account_type", None) or getattr(a, "type", "") or ""
            print(f"── Account {number}  {('· ' + str(atype)) if atype else ''}")
            if not number:
                print("   (couldn't read an account number from this entry)\n"); continue
            try:
                pf = client.get_portfolio(account_id=number)
                ef, ev = _first_attr(pf, ("equity", "total_equity", "account_value", "equity_value", "total_value"))
                cf, cv = _first_attr(pf, ("buying_power", "cash", "cash_balance", "available_cash", "withdrawable_cash"))
                npos = len(getattr(pf, "positions", []) or [])
                print(f"   equity:       {ev}   (field: {ef})")
                print(f"   cash/bp:      {cv}   (field: {cf})")
                print(f"   positions:    {npos}")
                if (_num(ev) or 0) > 0 or (_num(cv) or 0) > 0:
                    funded.append(number)
            except Exception as e:
                print(f"   ⚠️  couldn't fetch this account's portfolio: {e}")
            print()

        want = config.DEFAULT_ACCOUNT_NUMBER
        print("── Diagnosis " + "─" * 30)
        print(f"   .env DEFAULT_ACCOUNT_NUMBER = {want or '(not set)'}")
        print(f"   account(s) with money      = {', '.join(funded) if funded else 'none detected'}")
        if funded and want not in funded:
            print("\n   ❗ Your bot is pointed at an account with no balance while another")
            print(f"      account is funded. Set DEFAULT_ACCOUNT_NUMBER={funded[0]} in .env and restart.")
        elif not funded:
            print("\n   ❗ No account shows a balance. If you know it's funded, the balance may")
            print("      sit under a field name above we didn't map — send this whole output over.")
        else:
            print("\n   ✓ DEFAULT_ACCOUNT_NUMBER points at a funded account. Looks correct.")
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
