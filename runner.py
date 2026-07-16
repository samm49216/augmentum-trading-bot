"""Execution daemon + platform sync + conversational fleet control.

Each cycle (every POLL_SECONDS): self-update → pull config (apply per-bot dashboard
changes + approvals) → per-bot autonomous generation → auto-approve autonomous bots'
proposals → execute approved (per-bot dry-run gating) → answer the client's chat →
push a read-only snapshot. Honors the HALT file and the dashboard STOP.
"""
import json
import logging
import os
import re
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import assist
import config
import guardrails
import proposals
import strategies
import sync
from executor import execute_proposal, runtime_stopped

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("runner")

POLL_SECONDS = 10          # ~10s round-trip for chat + dashboard control
CHAT_SEEN = Path(__file__).parent / "state" / "chat_seen.json"


def build_client():
    from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
    from public_api_sdk.auth_config import ApiKeyAuthConfig
    return PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=config.API_SECRET_KEY, validity_minutes=config.TOKEN_VALIDITY_MINUTES),
        config=PublicApiClientConfiguration(default_account_number=config.DEFAULT_ACCOUNT_NUMBER),
    )


def _load_chat_seen():
    return set(json.loads(CHAT_SEEN.read_text())) if CHAT_SEEN.exists() else set()


def _save_chat_seen(seen):
    CHAT_SEEN.parent.mkdir(exist_ok=True)
    CHAT_SEEN.write_text(json.dumps(sorted(seen)))


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "bot"


def _portfolio_ctx(client):
    try:
        if config.DEFAULT_ACCOUNT_NUMBER:
            pf = client.get_portfolio(account_id=config.DEFAULT_ACCOUNT_NUMBER)
            return {"equity": str(getattr(pf, "equity", "")),
                    "buying_power": str(getattr(pf, "buying_power", ""))}
    except Exception as e:
        log.warning("portfolio context unavailable: %s", e)
    return {}


def autonomous_tick(client, strat):
    """Autonomous mode for ONE bot: its own AI proposes trades within that bot's
    rules. Proposals still pass guardrails + the dry-run gate before executing."""
    if not assist.available():
        return
    ctx = [{"id": strat.id, "name": strat.name, "description": strat.description,
            "rules": strat.rules, "allocation_usd": float(strat.allocation_usd),
            "asset_class": strat.asset_class}]
    sugg = assist.run_autonomous(ctx, _portfolio_ctx(client),
                                 max_trades=config.MAX_AUTONOMOUS_TRADES_PER_TICK)
    if sugg is None:
        return
    added = 0
    for t in sugg.proposed_trades[: config.MAX_AUTONOMOUS_TRADES_PER_TICK]:
        try:
            proposals.add(strat.id, t.symbol, t.side, t.asset_class or strat.asset_class,
                          amount=t.amount, rationale=t.rationale, source="autonomous-ai")
            added += 1
        except Exception as e:
            log.warning("autonomous trade rejected [%s]: %s", strat.id, e)
    if added:
        log.info("autonomous tick [%s]: generated %s proposal(s)", strat.id, added)


def apply_chat_actions(actions):
    """Apply the AI's bot actions to the local fleet. Returns human-readable summaries."""
    strats = strategies.load_strategies()
    by_id = {s.id: s for s in strats}
    applied, dirty, touched = [], False, set()
    for a in actions or []:
        t = (a.type or "").lower()
        if t == "create_bot":
            base = _slug(a.name or a.bot_id)
            sid, i = base, 2
            while sid in by_id:
                sid, i = f"{base}-{i}", i + 1
            s = strategies.Strategy(
                id=sid, name=a.name or sid, description=a.description or "",
                allocation_usd=Decimal(str(a.allocation_usd or 0)), enabled=True,
                asset_class=(a.asset_class or "equity"), rules=a.rules or "",
                live=False, autonomous=False,
                allowed_symbols=[x.strip().upper() for x in (a.allowed_symbols or "").split(",") if x.strip()])
            strats.append(s); by_id[sid] = s; dirty = True; touched.add(sid)
            applied.append(f"Created bot '{s.name}' (dry-run, manual)")
        elif t == "propose_trade":
            s = by_id.get(a.bot_id)
            try:
                proposals.add(a.bot_id, a.symbol, a.side, a.asset_class or (s.asset_class if s else "equity"),
                              amount=a.amount, rationale=a.rationale, source="chat-ai")
                applied.append(f"Proposed {a.side} {a.symbol} (needs your approval)")
            except Exception as e:
                applied.append(f"Trade not added: {e}")
        else:
            s = by_id.get(a.bot_id)
            if not s:
                applied.append(f"(couldn't find bot '{a.bot_id}')")
                continue
            if t == "adjust_bot":
                if a.name: s.name = a.name
                if a.description: s.description = a.description
                if a.rules: s.rules = a.rules
                if a.asset_class: s.asset_class = a.asset_class
                if a.allocation_usd is not None: s.allocation_usd = Decimal(str(a.allocation_usd))
                if a.allowed_symbols: s.allowed_symbols = [x.strip().upper() for x in a.allowed_symbols.split(",") if x.strip()]
                applied.append(f"Adjusted '{s.name}'")
            elif t == "pause_bot": s.enabled = False; applied.append(f"Paused '{s.name}'")
            elif t == "resume_bot": s.enabled = True; applied.append(f"Resumed '{s.name}'")
            elif t == "set_live": s.live = True; applied.append(f"'{s.name}' → LIVE")
            elif t == "set_dry": s.live = False; applied.append(f"'{s.name}' → dry-run")
            elif t == "set_autonomous": s.autonomous = True; applied.append(f"'{s.name}' → autonomous")
            elif t == "set_manual": s.autonomous = False; applied.append(f"'{s.name}' → manual")
            else:
                applied.append(f"(unknown action '{t}')"); continue
            dirty = True; touched.add(s.id)
    if dirty:
        strategies.save_strategies(strats)
    return applied, touched


def process_chat(client, cfg):
    """Answer new client chat messages with the client's OWN AI; apply the actions
    it returns and post the reply back into the thread."""
    msgs = cfg.get("chat", []) or []
    seen = _load_chat_seen()
    todo = [m for m in msgs if m.get("role") == "client" and m.get("id") and m.get("id") not in seen]
    if not todo:
        return
    if not assist.available():
        for m in todo:
            sync.post_chat_reply(
                "Your AI isn't connected yet. Add your Anthropic API key to your bot's .env "
                "(ANTHROPIC_API_KEY=...) and restart — then I can manage your bots from here.",
                [], reply_to=m["id"])
            seen.add(m["id"])
        _save_chat_seen(seen)
        return

    bots_ctx = [{"id": s.id, "name": s.name, "description": s.description, "rules": s.rules,
                 "asset_class": s.asset_class, "allocation_usd": float(s.allocation_usd),
                 "enabled": s.enabled, "live": s.live, "autonomous": s.autonomous}
                for s in strategies.load_strategies()]
    port_ctx = _portfolio_ctx(client)
    history = [{"role": m.get("role"), "text": m.get("text", "")} for m in msgs[-8:]]

    for m in todo:
        log.info("chat: answering %s", m.get("id"))
        resp = assist.run_chat(m.get("text", ""), bots_ctx, port_ctx, history)
        seen.add(m["id"])
        if resp is None:
            sync.post_chat_reply("I couldn't reach your AI just now — please try again in a moment.",
                                 [], reply_to=m["id"])
            continue
        if isinstance(resp, dict) and "__error__" in resp:
            log.error("chat AI error: %s", resp["__error__"])   # detail stays in the bot log
            sync.post_chat_reply("Sorry — I hit a snag on that one. Please try again, or rephrase it a little.",
                                 [], reply_to=m["id"])
            continue
        summary, touched = apply_chat_actions(resp.actions)
        states = {s.id: {"name": s.name, "description": s.description, "rules": s.rules,
                         "asset_class": s.asset_class, "allocation_usd": float(s.allocation_usd),
                         "enabled": s.enabled, "live": s.live, "autonomous": s.autonomous,
                         "allowed_symbols": list(s.allowed_symbols or [])}
                  for s in strategies.load_strategies() if s.id in touched}
        sync.post_chat_reply(resp.reply or "Done.", summary, reply_to=m["id"], bot_states=states)
    _save_chat_seen(seen)


def self_update():
    """git pull; if the code changed, re-exec so operator fixes reach the client
    automatically. Best-effort, only when run from a git clone, never fatal."""
    if not config.AUTO_UPDATE:
        return
    here = Path(__file__).parent
    if not (here / ".git").exists():
        return
    try:
        def rev():
            return subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=30).stdout.strip()
        before = rev()
        subprocess.run(["git", "-C", str(here), "pull", "--ff-only"],
                       capture_output=True, text=True, timeout=120)
        after = rev()
        if before and after and before != after:
            log.warning("self-update: %s -> %s — restarting", before[:7], after[:7])
            os.execv(sys.executable, [sys.executable, str(here / "runner.py")])
    except Exception as e:
        log.warning("self-update check failed: %s", e)


def main():
    config.require_credentials()
    log.info("Starting. DRY_RUN=%s  account=%s  platform=%s  ai=%s  poll=%ss",
             config.DRY_RUN, config.DEFAULT_ACCOUNT_NUMBER,
             "on" if sync.enabled() else "off", "on" if assist.available() else "off", POLL_SECONDS)
    if config.FORCE_DRY_RUN:
        log.info("FORCE_DRY_RUN is ON — every bot is hard-locked to dry-run regardless of the dashboard.")
    if config.FORCE_MANUAL_APPROVAL:
        log.info("FORCE_MANUAL_APPROVAL is ON — autonomous is hard-locked off; every trade needs approval.")

    self_update()  # pull latest before starting (re-execs if there's an update)
    client = build_client()
    last_auto = 0.0
    last_update = time.time()
    try:
        while True:
            if guardrails.kill_switch_active():
                log.warning("HALT present — idling. Remove ./HALT to resume.")
                time.sleep(POLL_SECONDS)
                continue

            if time.time() - last_update >= config.AUTO_UPDATE_SECONDS:
                last_update = time.time()
                self_update()

            cfg = sync.pull_and_apply_config()   # applies per-bot dashboard changes + approvals

            if runtime_stopped():
                log.warning("STOP engaged — idling (no trades). Turn a bot back on to resume.")
                process_chat(client, cfg)        # still answer the client while stopped
                sync.push_snapshot(client)
                time.sleep(POLL_SECONDS)
                continue

            strats = strategies.load_strategies()
            by_id = {s.id: s for s in strats}
            manual_lock = config.FORCE_MANUAL_APPROVAL

            # per-bot autonomous generation (rate-limited across the fleet)
            if not manual_lock and assist.available() and (time.time() - last_auto >= config.AUTONOMOUS_TICK_SECONDS):
                for s in strats:
                    if s.enabled and s.autonomous:
                        autonomous_tick(client, s)
                last_auto = time.time()

            # per-bot auto-approval: only proposals belonging to an autonomous bot
            if not manual_lock:
                for p in proposals.list_all(status="pending"):
                    s = by_id.get(p["strategy_id"])
                    if s and s.enabled and s.autonomous:
                        proposals.set_status(p["id"], "approved")
                        log.info("autonomous auto-approve [%s]: %s %s", p["strategy_id"], p["side"], p["symbol"])

            # execute approved (per-bot dry-run gating happens inside execute_proposal)
            for p in proposals.list_all(status="approved"):
                execute_proposal(client, p, by_id.get(p["strategy_id"]),
                                 account_id=config.DEFAULT_ACCOUNT_NUMBER)

            # conversational fleet control
            process_chat(client, cfg)

            sync.push_snapshot(client)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("Stopping.")
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
