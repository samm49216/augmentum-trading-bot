# Public Trading Bot (client-self-hosted)

A deployable, **safety-first** trading bot that connects a **Public.com** brokerage
account to a Claude-driven strategy via the official `publicdotcom-py` SDK.

Designed to run **standalone on an isolated instance**, configured with the
**account owner's own** Public API secret key. The account owner self-directs;
this package is the plumbing + guardrails, not a trading strategy.

---

## ⚠️ Read this first

- This software can place **real orders with real money** once `DRY_RUN=false`.
  Trading involves substantial risk of loss. Nothing here is investment advice.
- **The account owner is solely responsible** for every order placed and for
  turning the bot live. Keep it in `DRY_RUN` until you have validated everything.
- Public has **no sandbox/paper environment**, so safety is enforced in code:
  a hard **DRY-RUN gate**, a **kill switch**, and **risk guardrails** that fail closed.

## Setup sequence

1. **Onboarding** — account owner opens/funds Public, gets options approval,
   and generates an API secret key + account number. See [`ONBOARDING.md`](ONBOARDING.md).
2. **Deploy** — provision an isolated instance and install. See [`deploy/DEPLOY.md`](deploy/DEPLOY.md).
3. **Read-only check** — `python check_connection.py` (no orders — proves auth + read access).
4. **Configure guardrails** — set limits in `.env` (see `.env.example`).
5. **Dry run** — `python runner.py` with `DRY_RUN=true`: logs *intended* orders, places nothing.
6. **Go live** — only the account owner flips `DRY_RUN=false`, starting at tiny size.

## Layout

| File | Purpose |
|------|---------|
| `check_connection.py` | READ-ONLY connectivity + auth verifier (run first) |
| `config.py` | Loads `.env` (credentials + risk limits) |
| `guardrails.py` | Fail-closed risk checks: allowlist, notional caps, PDT counter, loss limit, kill switch |
| `executor.py` | Safe order wrapper: preflight → guardrails → (dry-run \| place) |
| `strategy.py` | **Stub** decision interface — the account owner implements what to trade |
| `runner.py` | Main loop wiring strategy → executor (respects DRY_RUN + HALT) |
| `deploy/` | systemd unit + isolated-instance deploy guide |

## Kill switch

Create a file named `HALT` in the project root to immediately block all order
placement (read-only calls still work). Delete it to resume.
