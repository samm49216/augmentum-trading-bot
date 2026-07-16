"""READ-ONLY AI connectivity check. Confirms your Anthropic key works and which
model your chat can use. Makes one tiny call; places no trades.

    python check_ai.py
"""
import config

# Tried in order if the configured model fails (newest → most widely available).
FALLBACKS = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"]


def _try(client, model):
    try:
        r = client.messages.create(
            model=model, max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}])
        txt = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        return True, (txt.strip()[:40] or "(empty)")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    print("== AI connectivity check (READ-ONLY — no trades) ==\n")
    if not config.ANTHROPIC_API_KEY:
        print("✗ ANTHROPIC_API_KEY is NOT set in your .env.")
        print("  Add a line:  ANTHROPIC_API_KEY=sk-ant-...   then restart the bot.")
        raise SystemExit(1)
    print(f"✓ ANTHROPIC_API_KEY is present (ends …{config.ANTHROPIC_API_KEY[-4:]}).")
    print(f"  Configured model: {config.ANTHROPIC_MODEL}")

    try:
        import anthropic
    except ImportError:
        raise SystemExit("✗ The 'anthropic' package isn't installed. Run: pip install -r requirements.txt")
    print(f"  anthropic SDK version: {anthropic.__version__}\n")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    ok, msg = _try(client, config.ANTHROPIC_MODEL)
    if ok:
        print(f"✓ Your key works with {config.ANTHROPIC_MODEL} — the chat should respond. (reply: {msg!r})")
        print("\nIf the chat still doesn't answer, the bot may be running old code — update with git pull + restart.")
        return

    print(f"✗ Call with {config.ANTHROPIC_MODEL} FAILED:\n    {msg}\n")
    print("  Trying other models your key may have access to…")
    working = []
    for m in FALLBACKS:
        if m == config.ANTHROPIC_MODEL:
            continue
        ok2, msg2 = _try(client, m)
        print(f"    {m}: {'OK ✓' if ok2 else msg2[:90]}")
        if ok2:
            working.append(m)
    if working:
        print(f"\n  → FIX: set  ANTHROPIC_MODEL={working[0]}  in your .env and restart. Your chat will use it.")
    else:
        print("\n  → No model worked, so the key itself is the problem — it's likely invalid or out of credit.")
        print("    Check console.anthropic.com (API Keys + Billing), then update ANTHROPIC_API_KEY in .env.")


if __name__ == "__main__":
    main()
