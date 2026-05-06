# agent.py
"""
Remittance extraction agent — Day 3 expanded with invoice allocation extraction.

State machine:
    START → triage → extract_bank_credits → extract_allocations → END

Day 1: Triage (rule-based) — classifies email_kind and detects payment_intent
Day 2: Bank credit extraction (LLM, per-row) — populates bank_credits list
Day 3: Allocation extraction (LLM, batched per-table) — populates invoice_allocations

Days 4-7 will add reconciliation, customer master resolution, and the final
RemittanceExtraction assembly.
"""

from operator import add
from typing import Annotated, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from extract_allocation import extract_allocations_from_table
from extract_bank_credit import extract_bank_credits_from_table
from html_tools import find_allocation_table, find_bank_credit_table
from schemas import (
    BankCreditLine,
    EmailKind,
    InvoiceAllocation,
    RemittanceExtraction,
)
from triage import TriageResult, classify_email


# ============================================================
# Email kinds eligible for each extraction stage
# ============================================================

BANK_CREDIT_ELIGIBLE_KINDS = {
    EmailKind.FULL_BOOKING,
    EmailKind.PARTIAL_BOOKING,
    EmailKind.ON_ACCOUNT_ONLY,
}

ALLOCATION_ELIGIBLE_KINDS = {
    EmailKind.FULL_BOOKING,
    # partial_booking / on_account_only / non_remittance / needs_attachment_parsing
    # do not have allocation tables; skip
}


# ============================================================
# Agent state
# ============================================================

class AgentState(BaseModel):
    # Input
    message_id: str
    email_json: dict

    # Triage result
    triage: Optional[TriageResult] = None

    # Bank credit extraction
    bank_credits: list[BankCreditLine] = Field(default_factory=list)

    # Allocation extraction
    invoice_allocations: list[InvoiceAllocation] = Field(default_factory=list)

    # Final output (assembled in Day 4+)
    extraction: Optional[RemittanceExtraction] = None

    # Conversation log
    messages: Annotated[list[BaseMessage], add] = Field(default_factory=list)

    # Error tracking
    error: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ============================================================
# Nodes
# ============================================================

def triage_node(state: AgentState) -> dict:
    """Classify the email's kind. Pure rule-based, no LLM."""
    try:
        result = classify_email(state.email_json)
    except Exception as e:
        return {"error": f"Triage failed: {type(e).__name__}: {e}"}
    return {"triage": result}


def extract_bank_credits_node(state: AgentState) -> dict:
    """Extract bank credit rows for emails that have a bank credit table."""
    if state.error:
        return {}
    if not state.triage:
        return {"error": "extract_bank_credits called before triage completed"}
    if state.triage.email_kind not in BANK_CREDIT_ELIGIBLE_KINDS:
        return {"bank_credits": []}

    html = state.email_json.get("body", {}).get("content", "") or ""
    table = find_bank_credit_table(html)
    if not table:
        return {
            "error": (
                f"Email classified as {state.triage.email_kind.value} but no "
                f"bank credit table found. This indicates a bug in triage logic."
            )
        }

    try:
        credits = extract_bank_credits_from_table(table)
    except Exception as e:
        return {
            "error": f"Bank credit extraction failed: {type(e).__name__}: {e}"
        }

    return {"bank_credits": credits}


def extract_allocations_node(state: AgentState) -> dict:
    """Extract allocation rows for full_booking emails.

    No-op for partial_booking, on_account_only, non_remittance,
    needs_attachment_parsing.
    """
    if state.error:
        return {}
    if not state.triage:
        return {"error": "extract_allocations called before triage completed"}
    if state.triage.email_kind not in ALLOCATION_ELIGIBLE_KINDS:
        # Only full_booking emails have allocation tables
        return {"invoice_allocations": []}

    html = state.email_json.get("body", {}).get("content", "") or ""
    table = find_allocation_table(html)
    if not table:
        # Triage said full_booking but allocation table not found — degraded
        # extraction, not a fatal error. Continue with empty list and let
        # downstream reconciliation flag the discrepancy.
        return {"invoice_allocations": []}

    try:
        allocations = extract_allocations_from_table(table)
    except Exception as e:
        return {
            "error": f"Allocation extraction failed: {type(e).__name__}: {e}"
        }

    return {"invoice_allocations": allocations}


# ============================================================
# Build graph
# ============================================================

builder = StateGraph(AgentState)
builder.add_node("triage", triage_node)
builder.add_node("extract_bank_credits", extract_bank_credits_node)
builder.add_node("extract_allocations", extract_allocations_node)

builder.add_edge(START, "triage")
builder.add_edge("triage", "extract_bank_credits")
builder.add_edge("extract_bank_credits", "extract_allocations")
builder.add_edge("extract_allocations", END)

graph = builder.compile()


# ============================================================
# Public API
# ============================================================

def process_email(message_id: str, email_json: dict) -> dict:
    """Run the agent on one email.

    Returns a dict with:
        - 'triage': TriageResult
        - 'bank_credits': list[BankCreditLine]
        - 'invoice_allocations': list[InvoiceAllocation]
        - 'extraction': RemittanceExtraction (None for now; assembled in Day 4)
        - 'error': str if anything failed
    """
    initial = AgentState(message_id=message_id, email_json=email_json)
    final_state = graph.invoke(initial)

    return {
        "triage": final_state.get("triage"),
        "bank_credits": final_state.get("bank_credits", []),
        "invoice_allocations": final_state.get("invoice_allocations", []),
        "extraction": final_state.get("extraction"),
        "error": final_state.get("error"),
    }


# ============================================================
# Demo
# ============================================================

if __name__ == "__main__":
    from email_source import get_email_source

    src = get_email_source()

    print("=" * 80)
    print("Day 3 Demo — Triage + Bank Credit + Allocation Extraction")
    print("=" * 80)
    for mid in src.list_known_message_ids():
        email = src.get_email(mid)
        result = process_email(mid, email)

        if result["error"]:
            print(f"\n  ERROR: {result['error']}")
            continue

        triage = result["triage"]
        credits = result["bank_credits"]
        allocs = result["invoice_allocations"]
        subject = email.get("subject", "")[:40]

        print(f"\n  {subject}")
        print(f"    kind: {triage.email_kind.value}")
        print(f"    bank_credits: {len(credits)}")
        for c in credits:
            mode = c.payment_mode.value if c.payment_mode else "?"
            payer = (c.payer_name_in_narrative or "?")[:25]
            print(f"      • {mode} | UTR={c.bank_utr} | {payer} | {c.amount}")
        print(f"    allocations: {len(allocs)}")
        for a in allocs:
            payer = (a.customer_name or "?")[:20]
            doc = a.document_type or "-"
            print(f"      • {a.customer_reference} | {payer} | "
                  f"inv={a.invoice_number} | {doc} | "
                  f"gross={a.gross_amount} | tds={a.tds_amount} | net={a.net_amount}")