"""Augmentum bot — full head-to-toe self-diagnostic.

    .venv/bin/python selfcheck.py                    # READ-ONLY: checks everything, places NO orders
    .venv/bin/python selfcheck.py --place-test-order # also places ONE tiny REAL order (asks first)

Verifies, in order: files + git, python + deps, .env config, the AI key (a real
call), the brokerage (auth + funded account + balance), dashboard sync, the risk
gates, and — the important one — the LIVE ORDER PATH, proven with a broker
"preflight" what-if that authenticates the trade endpoint WITHOUT placing a fill.

With --place-test-order it will, only after you type an explicit confirmation,
push the smallest possible REAL order through the exact executor path and report
the fill — the 100% end-to-end proof that live trading works. API keys are never
printed (masked to the last 4 chars). Nothing here changes your strategies.
"""
import os
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)   # so config's load_dotenv() finds ./.env

import argparse
import subprocess
import sys
from decimal import Decimal

import config

PASS, FAIL, WARN = "✓", "✗", "!"
results = []
flags = {"ai": False, "broker": False, "funded": False, "live_path": False, "bot_ready": False}


def _p(sym, label, detail=""):
    print(f"  {sym} {label}" + (f"  —  {detail}" if detail else ""))


def ok(label, detail=""):   results.append((PASS, label, detail)); _p(PASS, label, detail)
def bad(label, detail=""):  results.append((FAIL, label, detail)); _p(FAIL, label, detail)
def warn(label, detail=""): results.append((WARN, label, detail)); _p(WARN, label, detail)
def section(t): print(f"\n=== {t} ===")
def mask(s):
    s = s or ""
    return f"…{s[-4:]}" if len(s) >= 4 else ("(set)" if s else "(EMPTY)")


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        pass
    for attr in ("value", "amount", "total", "raw"):
        inner = v.get(attr) if isinstance(v, dict) else getattr(v, attr, None)
        if inner is not None:
            try:
                return float(inner)
            except Exception:
                pass
    return None


def _parse_total(pf):
    eq = getattr(pf, "equity", None)
    if isinstance(eq, (list, tuple)):
        tot, found = 0.0, False
        for it in eq:
            n = _num(it.get("value") if isinstance(it, dict) else getattr(it, "value", None))
            if n is not None:
                found = True; tot += n
        if found:
            return tot
    n = _num(eq)
    if n is not None:
        return n
    return _num(getattr(pf, "buying_power", None))


def _acct_number(a):
    for n in ("account_number", "account_id", "accountNumber", "id", "number"):
        v = getattr(a, n, None)
        if v:
            return str(v)
    return None


def _test_order_for(bot):
    """A tiny, sensible test order dict for this bot (symbol from its allowlist if set)."""
    al = [s.upper() for s in (getattr(bot, "allowed_symbols", []) or [])]
    ac = (getattr(bot, "asset_class", "equity") or "equity").lower()
    sym = al[0] if al else {"crypto": "BTC", "equity": "F", "index": "SPY"}.get(ac, "F")
    amt = min(5.0, float(getattr(bot, "allocation_usd", 0) or 0) or 5.0)
    return {"symbol": sym, "side": "BUY", "asset_class": ac, "order_type": "MARKET",
            "amount": str(round(max(amt, 1.0), 2))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--place-test-order", action="store_true",
                    help="After all checks, place ONE tiny REAL order (asks for confirmation first).")
    args = ap.parse_args()

    print("=" * 64)
    print("  AUGMENTUM BOT — FULL SELF-DIAGNOSTIC")
    print(f"  dir: {Path.cwd()}")
    print("  READ-ONLY unless you pass --place-test-order")
    print("=" * 64)

    # 1 ── files + repo ────────────────────────────────────────────────
    section("1. Files & repository")
    for f in ("runner.py", "config.py", "executor.py", "guardrails.py", "sync.py",
              "assist.py", "strategies.py", "proposals.py", "pnl.py", "requirements.txt", ".env"):
        (ok if Path(f).exists() else bad)(f"{f} present")
    (ok if Path(".venv").exists() else warn)(".venv present")
    try:
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        head = subprocess.run(["git", "log", "-1", "--format=%h %s"],
                              capture_output=True, text=True).stdout.strip()
        ok("git checkout", f"{branch} @ {head}")
        subprocess.run(["git", "fetch", "--quiet"], capture_output=True, text=True)
        behind = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"],
                                capture_output=True, text=True).stdout.strip()
        if behind and behind != "0":
            warn("up to date with origin", f"{behind} commit(s) behind — run: git pull --ff-only && restart")
        else:
            ok("up to date with origin")
    except Exception as e:
        warn("git checks", str(e)[:120])
    try:
        st = subprocess.run(["systemctl", "is-active", "augmentum-bot"],
                            capture_output=True, text=True).stdout.strip()
        (ok if st == "active" else warn)("systemd service 'augmentum-bot'", st or "not found (may run another way)")
    except Exception:
        pass

    # 2 ── python + deps ───────────────────────────────────────────────
    section("2. Python & dependencies")
    ok("python", sys.version.split()[0])
    for mod, nice in (("anthropic", "anthropic (AI)"), ("public_api_sdk", "public_api_sdk (broker)"),
                      ("requests", "requests"), ("dotenv", "python-dotenv"), ("pydantic", "pydantic")):
        try:
            m = __import__(mod)
            ok(nice, getattr(m, "__version__", "ok"))
        except Exception as e:
            bad(nice, f"NOT importable — {e}")

    # 3 ── .env config ─────────────────────────────────────────────────
    section("3. .env configuration")
    (ok if config.API_SECRET_KEY else bad)("API_SECRET_KEY (Public broker key)", mask(config.API_SECRET_KEY))
    (ok if config.DEFAULT_ACCOUNT_NUMBER else bad)("DEFAULT_ACCOUNT_NUMBER", config.DEFAULT_ACCOUNT_NUMBER or "(MISSING)")
    (ok if config.ANTHROPIC_API_KEY else bad)("ANTHROPIC_API_KEY (AI brain)", mask(config.ANTHROPIC_API_KEY))
    ok("ANTHROPIC_MODEL", config.ANTHROPIC_MODEL)
    (ok if config.PLATFORM_URL else warn)("PLATFORM_URL", config.PLATFORM_URL or "(dashboard sync off)")
    (ok if config.BOT_TOKEN else warn)("BOT_TOKEN", mask(config.BOT_TOKEN))
    (bad if config.FORCE_DRY_RUN else ok)("FORCE_DRY_RUN off (required for live orders)")
    (bad if config.FORCE_MANUAL_APPROVAL else ok)("FORCE_MANUAL_APPROVAL off (required for autonomous)")
    print(f"    flags: DRY_RUN={config.DRY_RUN}  AUTONOMOUS(default)={config.AUTONOMOUS}")
    print(f"    allowlist: {config.SYMBOL_ALLOWLIST or '(empty = ALL symbols allowed)'}")
    print(f"    caps: order<=${config.MAX_ORDER_NOTIONAL}  daily<=${config.MAX_DAILY_NOTIONAL}  loss-stop=${config.DAILY_LOSS_LIMIT}")
    print(f"    autonomous: every {config.AUTONOMOUS_TICK_SECONDS}s  (~${config.EST_AUTONOMOUS_MONTHLY_COST}/mo on {config.ANTHROPIC_MODEL})")
    try:
        mode = oct(Path(".env").stat().st_mode & 0o777)
        (ok if mode == "0o600" else warn)(".env permissions", f"{mode}" + ("" if mode == "0o600" else " (want 0o600: chmod 600 .env)"))
    except Exception:
        pass

    # 4 ── AI connectivity (real call) ─────────────────────────────────
    section("4. AI brain — your Anthropic key (makes one real call)")
    try:
        import anthropic
        if not config.ANTHROPIC_API_KEY:
            bad("AI key present", "ANTHROPIC_API_KEY is empty in .env")
        else:
            cl = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            r = cl.messages.create(model=config.ANTHROPIC_MODEL, max_tokens=8,
                                   messages=[{"role": "user", "content": "Reply with the single word OK."}])
            txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text").strip()
            ok(f"AI call works ({config.ANTHROPIC_MODEL})", f"key {mask(config.ANTHROPIC_API_KEY)} · replied {txt!r}")
            flags["ai"] = True
    except Exception as e:
        bad(f"AI call FAILED ({config.ANTHROPIC_MODEL})", str(e)[:200])
        print("      → This is why no trades generate. Fix the key/model/billing, then restart.")

    # 5 ── brokerage ───────────────────────────────────────────────────
    section("5. Brokerage (Public.com) — auth, account, balance")
    client = None
    target_total = None
    try:
        from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
        from public_api_sdk.auth_config import ApiKeyAuthConfig
        client = PublicApiClient(
            ApiKeyAuthConfig(api_secret_key=config.API_SECRET_KEY, validity_minutes=config.TOKEN_VALIDITY_MINUTES),
            config=PublicApiClientConfiguration(default_account_number=config.DEFAULT_ACCOUNT_NUMBER))
        raw = client.get_accounts()
        accts = list(getattr(raw, "accounts", None) or (raw if isinstance(raw, (list, tuple)) else [raw]))
        ok("broker authenticated", f"{len(accts)} account(s)")
        flags["broker"] = True
        seen = []
        for a in accts:
            num = _acct_number(a)
            seen.append(num)
            try:
                total = _parse_total(client.get_portfolio(account_id=num))
            except Exception as e:
                warn(f"account {num}", f"portfolio read failed: {str(e)[:90]}"); continue
            tag = "  ← DEFAULT_ACCOUNT_NUMBER" if num == config.DEFAULT_ACCOUNT_NUMBER else ""
            print(f"      account {num}: ${total if total is not None else '?'}{tag}")
            if num == config.DEFAULT_ACCOUNT_NUMBER:
                target_total = total
        if config.DEFAULT_ACCOUNT_NUMBER not in seen:
            bad("target account exists", f"{config.DEFAULT_ACCOUNT_NUMBER} is NOT one of your accounts {seen}")
        elif not target_total or target_total <= 0:
            bad("target account funded", f"{config.DEFAULT_ACCOUNT_NUMBER} shows $0 — fund it or point at a funded account")
        else:
            ok("target account funded", f"${target_total}")
            flags["funded"] = True
    except Exception as e:
        bad("brokerage auth/read", str(e)[:200])

    # 6 ── dashboard sync ──────────────────────────────────────────────
    section("6. Dashboard sync (Moore Platform)")
    if config.PLATFORM_URL and config.BOT_TOKEN:
        try:
            import requests
            base = config.PLATFORM_URL.rstrip("/")
            rc = requests.get(base + "/api/config", headers={"Authorization": f"Bearer {config.BOT_TOKEN}"}, timeout=15)
            (ok if rc.status_code == 200 else bad)("pull config (GET /api/config)", f"HTTP {rc.status_code}")
        except Exception as e:
            bad("reach dashboard", str(e)[:140])
    else:
        warn("dashboard sync", "PLATFORM_URL / BOT_TOKEN not set")

    # 7 ── bots & gates ────────────────────────────────────────────────
    section("7. Bots & risk gates")
    import executor
    import strategies
    strats = strategies.load_strategies()
    live_bot = None
    if not strats:
        warn("bots defined", "none")
    for s in strats:
        dry = executor.effective_dry_run(s)
        alloc = float(getattr(s, "allocation_usd", 0) or 0)
        detail = f"enabled={s.enabled} live={s.live} auto={s.autonomous} alloc=${alloc:g} class={s.asset_class}"
        if not dry and alloc <= 0:
            bad(f"bot '{s.id}' LIVE but alloc $0", detail + "  ← $0 allocation BLOCKS every order; set an allocation")
        elif not dry:
            ok(f"bot '{s.id}' — LIVE (real orders)", detail)
            if s.enabled and live_bot is None:
                live_bot = s
        else:
            print(f"      bot '{s.id}' — dry-run  ({detail})")
    print(f"    runtime: stopped={executor.runtime_stopped()}  effective_autonomous={executor.effective_autonomous()}")
    if live_bot and live_bot.enabled and live_bot.autonomous and float(live_bot.allocation_usd or 0) > 0:
        flags["bot_ready"] = True

    # 8 ── LIVE ORDER PATH (broker what-if, no fill) ───────────────────
    section("8. LIVE ORDER PATH — broker what-if (NO fill)")
    p = None
    endpoint_ok = False
    if not client:
        bad("live path", "no broker client (fix section 5)")
    elif not live_bot:
        warn("live path", "no enabled bot is set LIVE — flip one to Live on the dashboard, then rerun")
    else:
        import guardrails
        from guardrails import OrderIntent
        # a) prove the broker's TRADE endpoint responds (plain liquid equity, $1 what-if)
        probe = {"symbol": "F", "side": "BUY", "asset_class": "equity", "order_type": "MARKET", "amount": "1"}
        try:
            est_probe = executor._estimate_notional(client, probe, config.DEFAULT_ACCOUNT_NUMBER)
            if est_probe is not None:
                ok("broker trade endpoint reachable", f"preflight F $1 → est ${est_probe}")
                endpoint_ok = True
            else:
                warn("broker trade endpoint", "preflight returned no estimate (send output over)")
        except Exception as e:
            bad("broker trade endpoint", str(e)[:160])
        # b) this LIVE bot's own order: builds + passes the risk guardrails
        p = _test_order_for(live_bot)
        print(f"      bot order: {p['side']} ~${p['amount']} {p['symbol']} ({p['asset_class']}) for '{live_bot.id}'")
        try:
            executor.build_order_request(p); ok("bot order request builds")
        except Exception as e:
            bad("bot order request builds", str(e)[:160])
        est = None
        try:
            est = executor._estimate_notional(client, p, config.DEFAULT_ACCOUNT_NUMBER)
            if est is None:
                warn("bot-symbol preflight", f"{p['symbol']} returned no estimate — check its symbol/asset_class")
        except Exception as e:
            warn("bot-symbol preflight", f"{p['symbol']}: {str(e)[:120]}")
        intent = OrderIntent(symbol=p["symbol"], side="BUY", asset_class=p["asset_class"],
                             strategy_id=live_bot.id, allocation=live_bot.allocation_usd,
                             allowed_symbols=live_bot.allowed_symbols,
                             notional=(est if est is not None else Decimal(p["amount"])))
        g_ok, reason = guardrails.authorize(intent)
        (ok if g_ok else bad)("risk guardrails authorize the order", reason)
        if endpoint_ok and g_ok and not executor.effective_dry_run(live_bot):
            ok("LIVE PATH ARMED", "broker trade endpoint works + guardrails pass — real orders WILL place")
            flags["live_path"] = True
        else:
            bad("LIVE PATH not fully armed", "see the items above")

    # 9 ── optional REAL order ─────────────────────────────────────────
    if args.place_test_order:
        section("9. REAL ORDER TEST — spends real money")
        if not (client and live_bot and p and flags["live_path"]):
            bad("real order", "live path isn't armed — fix sections above first")
        else:
            print(f"\n  This places a REAL {p['side']} order for ~${p['amount']} of {p['symbol']} "
                  f"in account {config.DEFAULT_ACCOUNT_NUMBER}.")
            print("  It spends real money and is your decision as the account owner.")
            try:
                ans = input("  Type EXACTLY  YES PLACE IT  to proceed (anything else skips): ").strip()
            except EOFError:
                ans = ""
            if ans != "YES PLACE IT":
                warn("real order", "skipped")
            else:
                try:
                    order = client.place_order(executor.build_order_request(p), account_id=config.DEFAULT_ACCOUNT_NUMBER)
                    res = order.wait_for_terminal_status(timeout=120)
                    ok("REAL ORDER PLACED", f"terminal status: {getattr(res, 'status', res)}")
                    try:
                        pf = client.get_portfolio(account_id=config.DEFAULT_ACCOUNT_NUMBER)
                        for pos in getattr(pf, "positions", []) or []:
                            inst = getattr(pos, "instrument", None)
                            print(f"      position: {getattr(inst, 'symbol', inst)}  qty {getattr(pos, 'quantity', '?')}"
                                  f"  value ${_num(getattr(pos, 'current_value', None))}")
                    except Exception:
                        pass
                    print("  → To close it: sell that symbol from your dashboard or the Public app.")
                except Exception as e:
                    bad("REAL ORDER", str(e)[:200])

    if client:
        try:
            client.close()
        except Exception:
            pass

    # summary ──────────────────────────────────────────────────────────
    section("SUMMARY")
    nbad = sum(1 for r in results if r[0] == FAIL)
    nwarn = sum(1 for r in results if r[0] == WARN)
    for sym, label, detail in results:
        if sym in (FAIL, WARN):
            print(f"  {sym} {label}" + (f" — {detail}" if detail else ""))
    if nbad == 0 and nwarn == 0:
        print("  All checks green.")
    print(f"\n  {len(results)} checks · {nbad} failed · {nwarn} warnings")

    exec_ready = flags["broker"] and flags["funded"] and flags["live_path"] and not config.FORCE_DRY_RUN
    print("\n  " + "-" * 58)
    print(f"  ORDER EXECUTION PATH : {'READY ✓  (broker + preflight + guardrails all pass)' if exec_ready else 'NOT READY ✗  (see failures above)'}")
    print(f"  AUTONOMOUS AI BRAIN  : {'READY ✓  (key makes real calls)' if flags['ai'] else 'NOT READY ✗  (AI key/model/billing — section 4)'}")
    full = exec_ready and flags["ai"] and flags["bot_ready"]
    print(f"  >> AUTONOMOUS LIVE TRADING : {'READY ✓' if full else 'NOT READY ✗'}")
    print("  " + "-" * 58)
    if exec_ready and not args.place_test_order:
        print("  To prove a REAL fill end-to-end, rerun with:  --place-test-order  (it confirms before spending).")
    print()
    sys.exit(1 if nbad else 0)


if __name__ == "__main__":
    main()
