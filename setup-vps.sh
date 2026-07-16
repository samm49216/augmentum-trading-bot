#!/usr/bin/env bash
# One-shot VPS setup for the Augmentum trading bot on a fresh Ubuntu/Debian server.
#
# On the droplet (fresh DigitalOcean droplets log in as root), run:
#   curl -fsSL https://raw.githubusercontent.com/samm49216/augmentum-trading-bot/main/setup-vps.sh | bash
#
# Then put your keys in the .env it creates and start it:
#   nano /opt/augmentum-trading-bot/.env      # fill in your keys, keep DRY_RUN=true
#   systemctl restart augmentum-bot
#
# Safe to re-run (idempotent). Places no trades. Runs the bot as an unprivileged user.
set -euo pipefail

REPO="https://github.com/samm49216/augmentum-trading-bot.git"
USER_NAME="augmentum"
APP_DIR="/opt/augmentum-trading-bot"
SERVICE="augmentum-bot"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root (a fresh droplet is root by default), e.g.  sudo bash setup-vps.sh"; exit 1
fi

echo "→ Installing system packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null

echo "→ Creating unprivileged service user '$USER_NAME'…"
id -u "$USER_NAME" >/dev/null 2>&1 || adduser --system --group --home "$APP_DIR" "$USER_NAME"
mkdir -p "$APP_DIR"
chown -R "$USER_NAME":"$USER_NAME" "$APP_DIR"

echo "→ Fetching the bot…"
if [ -d "$APP_DIR/.git" ]; then
  sudo -u "$USER_NAME" git -C "$APP_DIR" pull --ff-only
else
  sudo -u "$USER_NAME" git clone --depth 1 "$REPO" "$APP_DIR"
fi

echo "→ Installing Python environment (takes a minute)…"
sudo -u "$USER_NAME" bash -c "cd '$APP_DIR' && ./install.sh"

if [ ! -f "$APP_DIR/.env" ]; then
  sudo -u "$USER_NAME" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  chown "$USER_NAME":"$USER_NAME" "$APP_DIR/.env"
fi

echo "→ Installing the always-on service…"
cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=Augmentum trading bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python runner.py
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${APP_DIR}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1 || true

cat <<EOF

──────────────────────────────────────────────
✅  Base install complete. The bot is NOT trading yet — it needs your keys.

NEXT (one time):
  1. Add your keys to:   ${APP_DIR}/.env
        API_SECRET_KEY=...            your Public.com API key
        DEFAULT_ACCOUNT_NUMBER=...    your funded Public account number
        BOT_TOKEN=...                 from your Augmentum email
        ANTHROPIC_API_KEY=...         your Anthropic key (powers the chat)
        # leave DRY_RUN=true
  2. Start it:           systemctl restart ${SERVICE}
  3. Watch it:           journalctl -u ${SERVICE} -f     (Ctrl-C to stop watching)
  4. Check it's healthy: sudo -u ${USER_NAME} ${APP_DIR}/.venv/bin/python ${APP_DIR}/check_connection.py

It now runs 24/7, restarts on crash + reboot, and self-updates via git.
⚠️  STOP the old bot on your Mac so only ONE bot runs on your account.
──────────────────────────────────────────────
EOF
