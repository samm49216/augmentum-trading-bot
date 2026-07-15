# Migrating the bot off the zip → git + always-on

Goal: stop shipping zip files. After this, every future bot update is **`git pull` + restart**
(one line), and the bot runs 24/7 instead of dying when a laptop sleeps.

The client's API key still lives only in `.env` on the client's own machine (Path A).
`.env` is gitignored and never leaves the box.

---

## Step 1 — put the bot in a private GitHub repo (one time, operator side)

From `~/projects/public-trading-bot`:

```bash
gh repo create augmentum-trading-bot --private --source=. --remote=origin --push
```

- `.gitignore` already excludes `.env`, `.venv/`, and `state/`, so no secrets or local
  state get pushed — verify with `git ls-files | grep -E '\.env$|\.venv|state/'` (should be empty).
- Give the client **read access** (or keep it operator-only and you run the updates).

## Step 2 — install on an always-on host (client side)

Pick one:

### Option A — the client's Mac (simplest, but only runs while the Mac is on)
```bash
git clone git@github.com:<you>/augmentum-trading-bot.git
cd augmentum-trading-bot
./install.sh                 # creates .venv + .env
# edit .env (API key, BOT_TOKEN, DEFAULT_ACCOUNT_NUMBER), keep DRY_RUN=true
```
Keep it running through sleep with a LaunchAgent + `caffeinate`:
```bash
# ~/Library/LaunchAgents/systems.augmentum.bot.plist runs:  caffeinate -i .venv/bin/python runner.py
launchctl load ~/Library/LaunchAgents/systems.augmentum.bot.plist
```

### Option B — a small VPS ($5/mo, true 24/7) — recommended for live/autonomous
```bash
git clone https://github.com/<you>/augmentum-trading-bot.git
cd augmentum-trading-bot && ./install.sh
# edit .env, then install the systemd unit already in deploy/
sudo cp deploy/public-bot.service /etc/systemd/system/
sudo systemctl enable --now public-bot
```
`deploy/DEPLOY.md` has the full VPS walkthrough.

## Step 3 — future updates (the whole point)

Operator pushes a change → client (or operator) runs:
```bash
cd augmentum-trading-bot && git pull
# Mac:  launchctl kickstart -k gui/$(id -u)/systems.augmentum.bot
# VPS:  sudo systemctl restart public-bot
```

No more zips. A new feature (multi-bot, notifications, hard-STOP) reaches the client
in seconds.

---

### Non-technical client?
The Claude Code prompt we use for setup can drive this too — it can clone, run
`install.sh`, fill `.env`, and register the service, all by conversation.
