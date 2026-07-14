# Onboarding checklist

## Account owner does (e.g. the client) — holds their own credentials
1. **Open + fund** a Public.com account.
2. **Apply for options approval** if options are in scope (Public gates multi-leg by tier).
3. **Generate an API secret key** — in Public, go to the API / developer section
   (see https://public.com/api) and create a personal access token / API secret key.
4. **Note the account number** the bot should trade (the default account).
5. **Hand off credentials securely** — a password manager / secrets vault, **never**
   plain email or chat. The key goes only into `.env` on the isolated instance.

## Operator does (you) — no credentials handled
6. Provision the isolated instance and install (see `deploy/DEPLOY.md`).
7. Put the owner's `API_SECRET_KEY` + `DEFAULT_ACCOUNT_NUMBER` into `.env`.
8. Set guardrails in `.env`: `SYMBOL_ALLOWLIST`, `MAX_ORDER_NOTIONAL`,
   `MAX_DAILY_NOTIONAL`, `MAX_DAY_TRADES`, `DAILY_LOSS_LIMIT`.
9. Run `check_connection.py` (read-only), then `runner.py` in `DRY_RUN=true`.
10. Implement the trading logic in `strategy.py` (the owner's rules).

## Then, deliberately
11. **Account owner** flips `DRY_RUN=false` and starts at tiny size.

## Things to keep in mind
- **PDT rule:** in a margin account under $25k, 4+ day trades in 5 business days flags
  Pattern Day Trader. `MAX_DAY_TRADES` guards stocks/options; crypto is exempt.
- **No paper mode:** Public has no sandbox, so "testing" live means real (tiny) orders.
  Lean on `DRY_RUN`, preflight what-ifs, and a short allowlist first.
- **Credentials never leave the isolated box.** Rotate the API key if it's ever exposed.
- This is trading infrastructure, **not** investment advice; the account owner owns every trade.
