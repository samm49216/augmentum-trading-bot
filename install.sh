#!/usr/bin/env bash
# One-command setup for the Public trading bot on a fresh machine (macOS/Linux).
# Run from the project directory:  ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "→ Creating Python venv + installing dependencies…"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt -r requirements-portal.txt

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env   # secrets — owner read/write only
  echo "→ Created .env from template."
else
  chmod 600 .env 2>/dev/null || true   # tighten perms on an existing .env too
  echo "→ .env already exists (left as-is)."
fi

cat <<'EOF'

✅ Installed. Now, in order:

  1. Edit .env — set:
       API_SECRET_KEY          (from your Public account: Settings → API)
       DEFAULT_ACCOUNT_NUMBER   (your brokerage account, e.g. 5OH89740)
       BOT_TOKEN               (from your onboarding email)
       ANTHROPIC_API_KEY       (optional — enables the plain-English AI assistant)
     Keep DRY_RUN=true for now.

  2. .venv/bin/python check_connection.py     # read-only: confirms your key works
  3. .venv/bin/python runner.py               # DRY-RUN daemon — feeds your dashboard, places nothing
  4. .venv/bin/streamlit run portal.py        # optional local control panel

  Go live only when ready: set DRY_RUN=false in .env (real trading, start tiny).
  Run 24/7 as background services → see deploy/DEPLOY.md.
EOF
