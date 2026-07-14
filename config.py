"""Loads configuration + risk limits from environment (.env)."""
import os
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def _dec(name, default):
    raw = os.getenv(name, "").strip()
    return Decimal(raw) if raw else Decimal(str(default))


def _int(name, default):
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()
DEFAULT_ACCOUNT_NUMBER = os.getenv("DEFAULT_ACCOUNT_NUMBER", "").strip()
TOKEN_VALIDITY_MINUTES = _int("TOKEN_VALIDITY_MINUTES", 15)

# Moore Platform (hosted dashboard) — read-only snapshot push + config pull.
PLATFORM_URL = os.getenv("PLATFORM_URL", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PLATFORM_SYNC_SECONDS = _int("PLATFORM_SYNC_SECONDS", 30)

# The CLIENT's own LLM key (their AI generates suggestions; never the operator's).
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8").strip()  # override to sonnet/haiku to cut cost

# Master safety switch. Anything other than an explicit false-y value => DRY_RUN on.
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() not in ("false", "0", "no", "off")

# Risk guardrails (fail closed)
SYMBOL_ALLOWLIST = [s.strip().upper() for s in os.getenv("SYMBOL_ALLOWLIST", "").split(",") if s.strip()]
MAX_ORDER_NOTIONAL = _dec("MAX_ORDER_NOTIONAL", 250)
MAX_DAILY_NOTIONAL = _dec("MAX_DAILY_NOTIONAL", 1000)
MAX_DAY_TRADES = _int("MAX_DAY_TRADES", 3)
DAILY_LOSS_LIMIT = _dec("DAILY_LOSS_LIMIT", 100)
ACCOUNT_EQUITY_FLOOR = _dec("ACCOUNT_EQUITY_FLOOR", 0)


def require_api_key():
    if not API_SECRET_KEY:
        raise SystemExit("Missing API_SECRET_KEY. Copy .env.example -> .env and paste your key in.")


def require_credentials():
    missing = [k for k, v in {
        "API_SECRET_KEY": API_SECRET_KEY,
        "DEFAULT_ACCOUNT_NUMBER": DEFAULT_ACCOUNT_NUMBER,
    }.items() if not v]
    if missing:
        raise SystemExit(
            f"Missing required env var(s): {', '.join(missing)}. "
            "Copy .env.example -> .env and fill them in."
        )
