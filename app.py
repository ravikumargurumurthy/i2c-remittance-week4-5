# app.py
"""
Streamlit HITL UI for the remittance extraction agent.

Three views:
1. Inbox: list of all extractions with routing band, key fields, action status
2. Detail: full RemittanceExtraction for one email + body preview + reasoning
3. Actions: accept/reject buttons for HITL_REVIEW emails (file-backed)

Run:
    streamlit run app.py
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import streamlit as st


# ============================================================
# Configuration
# ============================================================

CACHE_PATH = Path("data/extractions_cache.json")
ACTIONS_PATH = Path("data/actions.json")

st.set_page_config(
    page_title="I2C Remittance Extraction Agent",
    page_icon="💸",
    layout="wide",
)


# ============================================================
# Data loading
# ============================================================

@st.cache_data
def load_cache():
    """Load cached extractions from JSON."""
    if not CACHE_PATH.exists():
        return None
    with open(CACHE_PATH) as f:
        return json.load(f)


def load_actions() -> dict:
    """Load persisted actions from JSON. Returns empty dict if file missing."""
    if not ACTIONS_PATH.exists():
        return {}
    with open(ACTIONS_PATH) as f:
        return json.load(f)


def save_action(message_id: str, action: str, notes: Optional[str] = None):
    """Persist an action decision for a message."""
    actions = load_actions()
    actions[message_id] = {
        "action": action,
        "notes": notes,
        "decided_at": datetime.utcnow().isoformat() + "Z",
    }
    ACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIONS_PATH, "w") as f:
        json.dump(actions, f, indent=2)


# ============================================================
# Helpers — formatting
# ============================================================

def fmt_money(value) -> str:
    """Format a Decimal/string amount in Indian comma style."""
    if value is None or value == "":
        return "—"
    try:
        d = Decimal(str(value))
    except Exception:
        return str(value)
    sign = "-" if d < 0 else ""
    abs_str = f"{abs(d):,.2f}"
    return f"{sign}₹{abs_str}"


def fmt_routing(routing: str) -> str:
    """Format routing with color emoji."""
    return {
        "auto_apply": "🟢 Auto Apply",
        "hitl_review": "🟡 HITL Review",
        "exception": "🔴 Exception",
    }.get(routing, routing)


def fmt_status(status: str) -> str:
    """Format extraction status."""
    return {
        "clean": "✓ Clean",
        "rounding_diff": "≈ Rounding Diff",
        "access_payment": "↑ Access Payment",
        "allocation_exceeds_payment": "↓ Allocation > Payment",
        "not_applicable": "— Not Applicable",
        "not_remittance": "— Not Remittance",
        "deferred": "⋯ Deferred",
    }.get(status, status)


# ============================================================
# Sidebar — view selection + global actions
# ============================================================

cache = load_cache()
if not cache:
    st.error(
        "No cached extractions found. Run `python cache_extractions.py` "
        "to generate the cache first."
    )
    st.stop()

actions = load_actions()
extractions = cache.get("extractions", [])

st.sidebar.title("I2C Agent")
st.sidebar.caption(f"Cached: {cache['cached_at'][:19].replace('T', ' ')} UTC")
st.sidebar.caption(f"Extractions: {len(extractions)}")

if st.sidebar.button("🔄 Refresh extractions"):
    st.sidebar.info(
        "To refresh, run `python cache_extractions.py` from the terminal, "
        "then reload this page."
    )

# View selector
view = st.sidebar.radio(
    "View",
    ["📥 Inbox", "📄 Detail", "📊 Summary"],
    index=0,
)
# Honor the view override set by inbox "View →" buttons
if "__view_override" in st.session_state:
    view = st.session_state["__view_override"]
    del st.session_state["__view_override"]

# In sidebar: routing band counts
auto_count = sum(1 for e in extractions
                 if e["extraction"]["routing_decision"] == "auto_apply")
hitl_count = sum(1 for e in extractions
                 if e["extraction"]["routing_decision"] == "hitl_review")
exception_count = sum(1 for e in extractions
                      if e["extraction"]["routing_decision"] == "exception")

st.sidebar.divider()
st.sidebar.metric("🟢 Auto Apply", auto_count)
st.sidebar.metric("🟡 HITL Review", hitl_count)
st.sidebar.metric("🔴 Exception", exception_count)

# Action counts
accepted = sum(1 for v in actions.values() if v["action"] == "accept")
rejected = sum(1 for v in actions.values() if v["action"] == "reject")
st.sidebar.divider()
st.sidebar.metric("✓ Accepted", accepted)
st.sidebar.metric("✗ Rejected", rejected)


# ============================================================
# View: Inbox
# ============================================================

def view_inbox():
    st.title("📥 Inbox")
    st.caption(
        "All extractions, sorted by routing band. Click an extraction to see details."
    )

    # Filter
    routing_filter = st.multiselect(
        "Filter by routing decision",
        options=["auto_apply", "hitl_review", "exception"],
        default=["auto_apply", "hitl_review", "exception"],
        format_func=lambda x: fmt_routing(x),
    )

    # Sort by routing band (HITL first), then by confidence (descending)
    routing_order = {"hitl_review": 0, "exception": 1, "auto_apply": 2}
    sorted_extractions = sorted(
        [e for e in extractions
         if e["extraction"]["routing_decision"] in routing_filter],
        key=lambda e: (
            routing_order.get(e["extraction"]["routing_decision"], 99),
            -e["extraction"].get("confidence", 0),
        ),
    )

    if not sorted_extractions:
        st.info("No extractions match the filter.")
        return

    st.markdown(f"**{len(sorted_extractions)} extractions**")

    # Build a clean display table
    for entry in sorted_extractions:
        ext = entry["extraction"]
        meta = entry["email_metadata"]
        mid = entry["message_id"]

        # Action status
        action = actions.get(mid, {})
        action_label = ""
        if action.get("action") == "accept":
            action_label = "✓ Accepted"
        elif action.get("action") == "reject":
            action_label = "✗ Rejected"

        # Build the row
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns([3, 2, 2, 1])

            with col1:
                st.markdown(f"**{meta.get('subject') or '(no subject)'}**")
                st.caption(f"From: {meta.get('sender') or '?'}")
                st.caption(f"Kind: {ext['email_kind']} | "
                          f"Intent: {ext.get('payment_intent', 'invoice_payment')}")

            with col2:
                st.markdown(fmt_routing(ext["routing_decision"]))
                st.caption(f"Confidence: {ext['confidence']:.3f}")
                st.caption(f"Status: {fmt_status(ext['extraction_status'])}")

            with col3:
                bank_total = fmt_money(ext.get("total_bank_credits"))
                alloc_total = fmt_money(ext.get("total_net_allocated"))
                st.caption(f"Bank: {bank_total}")
                st.caption(f"Alloc: {alloc_total}")
                if ext.get("reconciliation_diff") is not None:
                    diff = fmt_money(ext["reconciliation_diff"])
                    st.caption(f"Diff: {diff}")

            with col4:
                if action_label:
                    st.markdown(action_label)
                if st.button("View →", key=f"view_{mid}"):
                    st.session_state["selected_message_id"] = mid
                    st.session_state["__view_override"] = "📄 Detail"
                    st.rerun()


# ============================================================
# View: Detail
# ============================================================

def view_detail():
    selected_mid = st.session_state.get("selected_message_id")
    if not selected_mid:
        st.warning("No extraction selected. Go to the Inbox view and click 'View →'.")
        return

    entry = next((e for e in extractions if e["message_id"] == selected_mid), None)
    if not entry:
        st.error(f"Extraction not found: {selected_mid}")
        return

    ext = entry["extraction"]
    meta = entry["email_metadata"]
    mid = entry["message_id"]

    # Header
    st.title(meta.get("subject") or "(no subject)")
    st.caption(f"From: {meta.get('sender') or '?'}")
    st.caption(f"Received: {meta.get('received_at') or '?'}")

    # Routing banner
    routing = ext["routing_decision"]
    if routing == "auto_apply":
        st.success(f"{fmt_routing(routing)} | Confidence: {ext['confidence']:.3f}")
    elif routing == "hitl_review":
        st.warning(f"{fmt_routing(routing)} | Confidence: {ext['confidence']:.3f}")
    else:
        st.error(f"{fmt_routing(routing)} | Confidence: {ext['confidence']:.3f}")

    # Two columns: extraction summary | email body
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("Triage")
        st.write(f"**Email kind:** {ext['email_kind']}")
        st.write(f"**Payment intent:** {ext.get('payment_intent', 'invoice_payment')}")
        if ext.get("intent_remarks_raw"):
            st.write(f"**Intent remarks:** {ext['intent_remarks_raw']}")

        st.subheader("Bank Credits")
        bank_credits = ext.get("bank_credits") or []
        if bank_credits:
            for i, bc in enumerate(bank_credits, 1):
                with st.container(border=True):
                    st.write(f"**Credit #{i}**")
                    st.write(f"Amount: {fmt_money(bc.get('amount'))}")
                    st.write(f"Mode: {bc.get('payment_mode') or '—'}")
                    st.write(f"UTR: `{bc.get('bank_utr') or '—'}`")
                    st.write(f"Payer: {bc.get('payer_name_in_narrative') or '—'}")
                    st.write(f"Bank: {bc.get('payer_bank') or '—'}")
                    if bc.get("notes"):
                        st.caption(f"Notes: {bc['notes']}")
                    if bc.get("narrative_raw"):
                        st.caption(f"Raw: `{bc['narrative_raw']}`")
        else:
            st.caption("No bank credits.")

        st.subheader("Invoice Allocations")
        allocations = ext.get("invoice_allocations") or []
        if allocations:
            # Build a small dataframe-style table
            for i, alloc in enumerate(allocations, 1):
                with st.container(border=True):
                    st.write(f"**Allocation #{i}**")
                    st.write(f"Customer: `{alloc.get('customer_reference')}` "
                            f"({alloc.get('customer_name') or '—'})")
                    st.write(f"Invoice: `{alloc.get('invoice_number')}` "
                            f"({alloc.get('document_type') or '—'})")
                    st.write(f"Gross: {fmt_money(alloc.get('gross_amount'))} | "
                            f"TDS: {fmt_money(alloc.get('tds_amount'))} | "
                            f"Net: {fmt_money(alloc.get('net_amount'))}")
        else:
            st.caption("No invoice allocations.")

        st.subheader("Reconciliation")
        st.write(f"**Status:** {fmt_status(ext['extraction_status'])}")
        st.write(f"**Bank credits total:** {fmt_money(ext.get('total_bank_credits'))}")
        st.write(f"**Allocations total:** {fmt_money(ext.get('total_net_allocated'))}")
        if ext.get("reconciliation_diff") is not None:
            st.write(f"**Diff:** {fmt_money(ext['reconciliation_diff'])}")

        st.subheader("Master Resolution")
        resolution = ext.get("resolution")
        if resolution:
            cust_resolved = resolution.get("customer_resolved")
            if cust_resolved is True:
                st.write(f"✓ **Customer resolved:** "
                        f"`{resolution.get('canonical_customer_number')}` "
                        f"({resolution.get('canonical_customer_name') or '—'})")
            elif cust_resolved is False:
                st.write(f"✗ **Customer NOT FOUND** in t_customer_master")
            else:
                st.write("— Customer lookup not applicable")

            inv_total = resolution.get("invoices_total", 0)
            inv_resolved = resolution.get("invoices_resolved", 0)
            if inv_total > 0:
                if inv_resolved == inv_total:
                    st.write(f"✓ **Invoices resolved:** {inv_resolved}/{inv_total}")
                else:
                    st.write(f"⚠ **Invoices resolved:** {inv_resolved}/{inv_total}")
                    unresolved = resolution.get("unresolved_invoice_numbers") or []
                    if unresolved:
                        st.caption(f"Unresolved: {', '.join(unresolved)}")
            else:
                st.write("— No invoices to resolve")

            if resolution.get("resolution_error"):
                st.error(f"Resolution error: {resolution['resolution_error']}")
        else:
            st.caption("No resolution data.")

        # Agent reasoning
        st.subheader("Agent Reasoning")
        st.caption(ext.get("extraction_notes") or "(no reasoning notes)")

        # HITL actions
        st.subheader("Actions")
        existing_action = actions.get(mid, {})
        if existing_action:
            decided_at = existing_action.get("decided_at", "?")[:19].replace("T", " ")
            if existing_action.get("action") == "accept":
                st.success(f"✓ Accepted at {decided_at} UTC")
            elif existing_action.get("action") == "reject":
                st.error(f"✗ Rejected at {decided_at} UTC")
            if existing_action.get("notes"):
                st.caption(f"Notes: {existing_action['notes']}")

            if st.button("Clear decision", key=f"clear_{mid}"):
                actions_copy = load_actions()
                actions_copy.pop(mid, None)
                with open(ACTIONS_PATH, "w") as f:
                    json.dump(actions_copy, f, indent=2)
                st.rerun()
        else:
            notes_input = st.text_area(
                "Notes (optional)",
                key=f"notes_{mid}",
                placeholder="Reasoning, follow-up, or correction notes...",
            )
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("✓ Accept", key=f"accept_{mid}", type="primary",
                            use_container_width=True):
                    save_action(mid, "accept", notes_input or None)
                    st.success("Accepted!")
                    st.rerun()
            with col_b:
                if st.button("✗ Reject", key=f"reject_{mid}",
                            use_container_width=True):
                    save_action(mid, "reject", notes_input or None)
                    st.error("Rejected!")
                    st.rerun()

    with col_right:
        st.subheader("Email Body")
        # Render the email's HTML body in an iframe-safe way
        body_html = entry.get("email_body_html") or ""
        if body_html:
            st.components.v1.html(body_html, height=800, scrolling=True)
        else:
            st.caption("No body content.")


# ============================================================
# View: Summary
# ============================================================

def view_summary():
    st.title("📊 Summary")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Total extractions", len(extractions))
        st.metric("Auto Apply", auto_count)
        st.metric("HITL Review", hitl_count)
        st.metric("Exception", exception_count)

    with col2:
        st.metric("Accepted", accepted)
        st.metric("Rejected", rejected)
        st.metric(
            "Pending review",
            sum(1 for e in extractions
                if e["extraction"]["routing_decision"] == "hitl_review"
                and e["message_id"] not in actions)
        )

    with col3:
        # Distribution by email kind
        kind_counts = {}
        for e in extractions:
            kind = e["extraction"]["email_kind"]
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
        st.write("**By email kind:**")
        for kind, count in sorted(kind_counts.items(), key=lambda x: -x[1]):
            st.caption(f"{kind}: {count}")

    # Average confidence by routing
    st.divider()
    st.subheader("Confidence distribution")
    routing_confs = {"auto_apply": [], "hitl_review": [], "exception": []}
    for e in extractions:
        r = e["extraction"]["routing_decision"]
        if r in routing_confs:
            routing_confs[r].append(e["extraction"]["confidence"])

    for routing, confs in routing_confs.items():
        if confs:
            avg = sum(confs) / len(confs)
            st.caption(
                f"{fmt_routing(routing)}: avg confidence "
                f"{avg:.3f} ({len(confs)} extractions)"
            )

    # Resolution stats
    st.divider()
    st.subheader("Master data resolution")
    customers_resolved = sum(
        1 for e in extractions
        if e["extraction"].get("resolution", {}).get("customer_resolved") is True
    )
    customers_attempted = sum(
        1 for e in extractions
        if e["extraction"].get("resolution", {}).get("customer_resolved") is not None
    )
    invoices_resolved = sum(
        e["extraction"].get("resolution", {}).get("invoices_resolved", 0)
        for e in extractions
    )
    invoices_total = sum(
        e["extraction"].get("resolution", {}).get("invoices_total", 0)
        for e in extractions
    )

    st.metric("Customers resolved", f"{customers_resolved}/{customers_attempted}")
    st.metric("Invoices resolved", f"{invoices_resolved}/{invoices_total}")


# ============================================================
# Main router
# ============================================================

if view == "📥 Inbox":
    view_inbox()
elif view == "📄 Detail":
    view_detail()
elif view == "📊 Summary":
    view_summary()
