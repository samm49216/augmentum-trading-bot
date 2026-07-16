"""Loads configuration + risk limits from environment (.env)."""
import os
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def _clean(v):
    # Strip an inline "# comment" (older python-dotenv versions keep it as part of
    # the value) and surrounding whitespace. Our numeric/flag/symbol config values
    # never legitimately contain '#'. This keeps a blank field truly blank —
    # critical for SYMBOL_ALLOWLIST, where empty MUST mean "block everything".
    return (v or "").split("#", 1)[0].strip()


def _dec(name, default):
    raw = _clean(os.getenv(name, ""))
    return Decimal(raw) if raw else Decimal(str(default))


def _int(name, default):
    raw = _clean(os.getenv(name, ""))
    return int(raw) if raw else default


def _flag(name, default_true=False):
    raw = _clean(os.getenv(name, "true" if default_true else "false")).lower()
    return raw not in ("false", "0", "no", "off") if default_true else raw in ("true", "1", "yes", "on")


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

# Master safety switch (standalone default when no platform is connected).
DRY_RUN = _flag("DRY_RUN", default_true=True)

# Local hard-lock: if true, the bot stays in dry-run NO MATTER WHAT the dashboard
# says (blocks remote go-live). Leave false to let the account owner flip live
# from their dashboard.
FORCE_DRY_RUN = _flag("FORCE_DRY_RUN")

# Autonomous mode — auto-approve + execute the client's own-AI-generated proposals
# WITHOUT manual per-trade approval (24/7). Standalone default when no platform is
# connected; normally driven by the dashboard toggle.
AUTONOMOUS = _flag("AUTONOMOUS")

# Local hard-lock: if true, EVERY trade requires manual approval no matter what the
# dashboard says (blocks remote autonomous). The account owner's box always keeps a
# veto over unattended trading. Leave false to let them enable autonomous remotely.
FORCE_MANUAL_APPROVAL = _flag("FORCE_MANUAL_APPROVAL")

# Autonomous generation cadence + per-cycle trade cap (risk guardrails still apply
# on top of this — autonomous is never unbounded).
AUTONOMOUS_TICK_SECONDS = _int("AUTONOMOUS_TICK_SECONDS", 900)
MAX_AUTONOMOUS_TRADES_PER_TICK = _int("MAX_AUTONOMOUS_TRADES_PER_TICK", 2)

# Self-update: periodically `git pull` and restart so operator fixes reach the
# client automatically (only when run from a git clone). Set AUTO_UPDATE=false to pin.
AUTO_UPDATE = _flag("AUTO_UPDATE", default_true=True)
AUTO_UPDATE_SECONDS = _int("AUTO_UPDATE_SECONDS", 21600)  # 6h

# Risk guardrails (fail closed). EMPTY allowlist = block EVERY symbol (fail-closed),
# so it must parse truly empty when unset — hence _clean (strips any inline comment).
SYMBOL_ALLOWLIST = [s.strip().upper() for s in _clean(os.getenv("SYMBOL_ALLOWLIST", "")).split(",") if s.strip()]
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
