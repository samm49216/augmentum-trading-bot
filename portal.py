"""Client-facing portal (Streamlit) — self-hosted, your own key.

    streamlit run portal.py

SELF-DIRECTED TOOL. NOT INVESTMENT ADVICE. You approve and execute every trade on
your own account. Trade ideas come from your own Claude; nothing trades on its own,
and DRY_RUN blocks live orders until you deliberately turn it off.
"""
from decimal import Decimal

import pandas as pd
import streamlit as st

import config
import guardrails
import proposals
import strategies
from executor import execute_proposal

st.set_page_config(page_title="My Trading Strategies", page_icon="📈", layout="wide")
DRY = config.DRY_RUN

st.sidebar.title("My Strategies")
page = st.sidebar.radio("View", ["Strategies", "Proposals", "Portfolio"])
st.sidebar.info("🟡 DRY-RUN — no live orders" if DRY else "🟢 LIVE — real orders")
st.sidebar.caption("Self-directed tool. Not investment advice. You approve & execute every trade.")


@st.cache_resource
def get_client():
    from public_api_sdk import PublicApiClient, PublicApiClientConfiguration
    from public_api_sdk.auth_config import ApiKeyAuthConfig
    return PublicApiClient(
        ApiKeyAuthConfig(api_secret_key=config.API_SECRET_KEY, validity_minutes=config.TOKEN_VALIDITY_MINUTES),
        config=PublicApiClientConfiguration(default_account_number=config.DEFAULT_ACCOUNT_NUMBER),
    )


def strat_by_id():
    return {s.id: s for s in strategies.load_strategies()}


# ------------------------- Strategies -------------------------
if page == "Strategies":
    st.title("Strategies")
    st.caption("Each strategy is a capital bucket with its own allocation. Adjust and save.")
    strats = strategies.load_strategies()
    deployed = guardrails.snapshot_state().get("strategy_notional", {})
    if not strats:
        st.info("No strategies yet — add them to strategies.json.")
    for s in strats:
        with st.container(border=True):
            st.subheader(s.name)
            st.write(s.description)
            c1, c2, c3 = st.columns(3)
            alloc = c1.number_input(f"Allocation ($) — {s.id}", min_value=0.0,
                                    value=float(s.allocation_usd), step=25.0, key=f"alloc_{s.id}")
            enabled = c2.toggle("Enabled", value=s.enabled, key=f"en_{s.id}")
            used = float(deployed.get(s.id, 0) or 0)
            c3.metric("Deployed today", f"${used:,.2f}", help="Approx. notional this strategy placed today")
            s.allocation_usd = Decimal(str(alloc))
            s.enabled = enabled
    if strats and st.button("💾 Save strategies"):
        strategies.save_strategies(strats)
        st.success("Saved.")

# ------------------------- Proposals -------------------------
elif page == "Proposals":
    st.title("Proposals")
    st.caption("Trade ideas from your Claude — you approve & execute. Nothing happens on its own."
               + (" (DRY-RUN: simulated)" if DRY else ""))
    sbid = strat_by_id()

    pending = proposals.list_all("pending")
    st.subheader(f"Pending ({len(pending)})")
    if not pending:
        st.info("No pending proposals.")
    for p in pending:
        with st.container(border=True):
            size = f"${p['amount']}" if p.get("amount") is not None else f"{p.get('quantity')} units"
            st.markdown(f"**{p['side']} {p['symbol']}** · {size} · {p['order_type']}"
                        + (f" @ {p['limit_price']}" if p.get("limit_price") else ""))
            st.caption(f"Strategy: {p['strategy_id']} · {p.get('rationale','')} · from {p.get('source','')}")
            a, b = st.columns(2)
            if a.button("✅ Approve & Execute", key=f"ap_{p['id']}"):
                try:
                    execute_proposal(get_client(), p, sbid.get(p["strategy_id"]),
                                     account_id=config.DEFAULT_ACCOUNT_NUMBER)
                    st.success("Processed — see History below.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
            if b.button("✖ Reject", key=f"rj_{p['id']}"):
                proposals.set_status(p["id"], "rejected")
                st.rerun()

    with st.expander("➕ Add a trade idea manually"):
        with st.form("addprop"):
            ids = [s.id for s in strategies.load_strategies()] or ["default"]
            sid = st.selectbox("Strategy", ids)
            sym = st.text_input("Symbol", "BTC")
            side = st.selectbox("Side", ["BUY", "SELL"])
            ac = st.selectbox("Asset class", ["crypto", "equity", "option"])
            amt = st.number_input("Dollar amount ($)", min_value=0.0, value=25.0, step=5.0)
            rat = st.text_input("Rationale", "")
            if st.form_submit_button("Add proposal"):
                proposals.add(sid, sym, side, ac, amount=amt, rationale=rat, source="manual")
                st.success("Added to pending.")
                st.rerun()

    st.divider()
    st.subheader("History")
    hist = [p for p in proposals.list_all() if p["status"] != "pending"]
    if hist:
        st.dataframe(pd.DataFrame([{
            "id": p["id"], "status": p["status"], "side": p["side"], "symbol": p["symbol"],
            "amount": p.get("amount"), "strategy": p["strategy_id"], "updated": p.get("updated_at"),
        } for p in reversed(hist)]), use_container_width=True, hide_index=True)
    else:
        st.caption("No history yet.")

# ------------------------- Portfolio -------------------------
elif page == "Portfolio":
    st.title("Portfolio")
    if not config.DEFAULT_ACCOUNT_NUMBER:
        st.warning("Set DEFAULT_ACCOUNT_NUMBER in .env to load your live portfolio.")
        st.stop()
    try:
        pf = get_client().get_portfolio(account_id=config.DEFAULT_ACCOUNT_NUMBER)
    except Exception as e:
        st.error(f"Couldn't load portfolio: {e}")
        st.stop()

    c1, c2 = st.columns(2)
    c1.metric("Equity", f"${getattr(pf, 'equity', '?')}")
    c2.metric("Buying power", f"${getattr(pf, 'buying_power', '?')}")

    positions = getattr(pf, "positions", []) or []
    if positions:
        rows = []
        for pos in positions:
            inst = getattr(pos, "instrument", None)
            rows.append({
                "symbol": getattr(inst, "symbol", str(inst)),
                "quantity": str(getattr(pos, "quantity", "")),
                "value": float(getattr(pos, "current_value", 0) or 0),
                "pct": float(getattr(pos, "percent_of_portfolio", 0) or 0),
                "daily_gain": str(getattr(pos, "position_daily_gain", "")),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.bar_chart(df.set_index("symbol")["value"])
        tot = df["value"].sum()
        if tot:
            top = df.loc[df["value"].idxmax()]
            if 100 * top["value"] / tot >= 40:
                st.warning(f"Concentration: {top['symbol']} is {100*top['value']/tot:.0f}% of the portfolio.")
    else:
        st.info("No positions.")
    st.caption("Live, read-only view of your own Public account.")
