# Deploy — isolated instance

Run this on a **separate** host from the OpenClaw/Steve droplet (own box, own user,
own firewall) so the trader can't affect other services.

## 1. Provision
- A small Ubuntu VPS (1 vCPU / 1 GB is plenty). **Not** the 167.99.52.66 droplet.
- Create an unprivileged user: `sudo adduser --disabled-password botuser`
- Basic firewall: allow SSH only. The bot makes **outbound** HTTPS calls only; it needs no inbound ports.

## 2. Install
```bash
sudo mkdir -p /opt/public-trading-bot && sudo chown botuser:botuser /opt/public-trading-bot
sudo -u botuser -H bash
cd /opt/public-trading-bot
# copy the project files here (git clone or rsync)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then edit .env — see ONBOARDING.md for credentials
```

## 3. Validate BEFORE anything else
```bash
.venv/bin/python check_connection.py     # READ-ONLY: proves auth + read access
```
Keep `DRY_RUN=true` in `.env` and run `.venv/bin/python runner.py` to watch it log
*intended* orders without placing any.

## 4. Run 24/7 (only after dry-run looks right)
```bash
sudo cp deploy/public-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now public-bot
journalctl -u public-bot -f          # live logs
```

## Kill switch
```bash
touch /opt/public-trading-bot/HALT    # blocks all order placement immediately
rm    /opt/public-trading-bot/HALT    # resume
```

## Going live
Flipping `DRY_RUN=false` is the **account owner's** decision. Start with a tiny
`MAX_ORDER_NOTIONAL`, a short `SYMBOL_ALLOWLIST`, and watch `journalctl` closely.
Restart the service after any `.env` change: `sudo systemctl restart public-bot`.
