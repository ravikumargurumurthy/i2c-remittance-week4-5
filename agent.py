# agent.py
"""
Remittance extraction agent — Day 5 with master data resolution.

State machine:
    START → triage → extract_bank_credits → extract_allocations
          → reconcile → assemble → resolve → END

Day 1: Triage (rule-based)
Day 2: Bank credit extraction (LLM, per-row)
Day 3: Allocation extraction (LLM, batched)
Day 4: Reconciliation, confidence, routing, assembly
Day 5: Master data resolution (deterministic DB lookups)

Output: a complete RemittanceExtraction per email with resolution status,
ready for Project 2's matching logic.
"""

from operator import add
from typing import Annotated, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from assemble import assemble_extraction
from extract_allocation import extract_allocations_from_table
from extract_bank_credit import extract_bank_credits_from_table
from html_tools import find_allocation_table, find_bank_credit_table
from reconcile import ReconciliationResult, reconcile
from resolve import resolve
from schemas import (
    BankCreditLine,
    EmailKind,
    InvoiceAllocation,
    RemittanceExtraction,
)
from triage import TriageResult, classify_email


BANK_CREDIT_ELIGIBLE_KINDS = {
    EmailKind.FULL_BOOKING,
    EmailKind.PARTIAL_BOOKING,
    EmailKind.ON_ACCOUNT_ONLY,
}

ALLOCATION_ELIGIBLE_KINDS = {
    EmailKind.FULL_BOOKING,
}


class AgentState(BaseModel):
    message_id: str
    email_json: dict

    triage: Optional[TriageResult] = None
    bank_credits: list[BankCreditLine] = Field(default_factory=list)
    invoice_allocations: list[InvoiceAllocation] = Field(default_factory=list)
    reconciliation: Optional[ReconciliationResult] = None
    extraction: Optional[RemittanceExtraction] = None

    messages: Annotated[list[BaseMessage], add] = Field(default_factory=list)
    error: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ============================================================
# Nodes
# ============================================================

def triage_node(state: AgentState) -> dict:
    try:
        result = classify_email(state.email_json)
    except Exception as e:
        return {"error": f"Triage failed: {type(e).__name__}: {e}"}
    return {"triage": result}


def extract_bank_credits_node(state: AgentState) -> dict:
    if state.error:
        return {}
    if not state.triage:
        return {"error": "extract_bank_credits called before triage"}
    if state.triage.email_kind not in BANK_CREDIT_ELIGIBLE_KINDS:
        return {"bank_credits": []}

    html = state.email_json.get("body", {}).get("content", "") or ""
    table = find_bank_credit_table(html)
    if not table:
        return {
            "error": (
                f"Email classified as {state.triage.email_kind.value} but no "
                f"bank credit table found."
            )
        }

    try:
        credits = extract_bank_credits_from_table(table)
    except Exception as e:
        return {"error": f"Bank credit extraction failed: {type(e).__name__}: {e}"}

    return {"bank_credits": credits}


def extract_allocations_node(state: AgentState) -> dict:
    if state.error:
        return {}
    if not state.triage:
        return {"error": "extract_allocations called before triage"}
    if state.triage.email_kind not in ALLOCATION_ELIGIBLE_KINDS:
        return {"invoice_allocations": []}

    html = state.email_json.get("body", {}).get("content", "") or ""
    table = find_allocation_table(html)
    if not table:
        return {"invoice_allocations": []}

    try:
        allocations = extract_allocations_from_table(table)
    except Exception as e:
        return {"error": f"Allocation extraction failed: {type(e).__name__}: {e}"}

    return {"invoice_allocations": allocations}


def reconcile_node(state: AgentState) -> dict:
    if state.error:
        return {}
    if not state.triage:
        return {"error": "reconcile called before triage"}

    try:
        result = reconcile(
            email_kind=state.triage.email_kind,
            bank_credits=state.bank_credits,
            allocations=state.invoice_allocations,
        )
    except Exception as e:
        return {"error": f"Reconciliation failed: {type(e).__name__}: {e}"}

    return {"reconciliation": result}


def assemble_node(state: AgentState) -> dict:
    if state.error:
        return {}
    if not state.triage or not state.reconciliation:
        return {
            "error": "assemble called before triage or reconciliation completed"
        }

    try:
        extraction = assemble_extraction(
            message_id=state.message_id,
            email_json=state.email_json,
            triage=state.triage,
            bank_credits=state.bank_credits,
            allocations=state.invoice_allocations,
            reconciliation=state.reconciliation,
        )
    except Exception as e:
        return {"error": f"Assembly failed: {type(e).__name__}: {e}"}

    return {"extraction": extraction}


def resolve_node(state: AgentState) -> dict:
    """Resolve customer and invoice references against master tables.

    Mutates the existing extraction object to add the resolution field.
    On DB error, populates resolution.resolution_error rather than
    failing the agent — extraction is still useful even with degraded
    resolution data.
    """
    if state.error:
        return {}
    if not state.extraction:
        return {"error": "resolve called before extraction was assembled"}

    try:
        resolution = resolve(state.extraction)
    except Exception as e:
        # Catastrophic failure (shouldn't happen — resolve() catches its own
        # errors). If it does, log and continue with extraction unchanged.
        import sys
        print(
            f"[WARN] resolve() raised unhandled: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return {}

    # Update extraction with resolution info
    updated = state.extraction.model_copy(update={"resolution": resolution})
    return {"extraction": updated}


# ============================================================
# Build graph
# ============================================================

builder = StateGraph(AgentState)
builder.add_node("triage", triage_node)
builder.add_node("extract_bank_credits", extract_bank_credits_node)
builder.add_node("extract_allocations", extract_allocations_node)
builder.add_node("reconcile", reconcile_node)
builder.add_node("assemble", assemble_node)
builder.add_node("resolve", resolve_node)

builder.add_edge(START, "triage")
builder.add_edge("triage", "extract_bank_credits")
builder.add_edge("extract_bank_credits", "extract_allocations")
builder.add_edge("extract_allocations", "reconcile")
builder.add_edge("reconcile", "assemble")
builder.add_edge("assemble", "resolve")
builder.add_edge("resolve", END)

graph = builder.compile()


# ============================================================
# Public API
# ============================================================

def process_email(message_id: str, email_json: dict) -> dict:
    """Run the agent on one email."""
    initial = AgentState(message_id=message_id, email_json=email_json)
    final_state = graph.invoke(initial)

    return {
        "triage": final_state.get("triage"),
        "bank_credits": final_state.get("bank_credits", []),
        "invoice_allocations": final_state.get("invoice_allocations", []),
        "reconciliation": final_state.get("reconciliation"),
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
    print("Day 5 Demo — Full Pipeline with Master Data Resolution")
    print("=" * 80)
    for mid in src.list_known_message_ids():
        email = src.get_email(mid)
        result = process_email(mid, email)

        if result["error"]:
            print(f"\n  ERROR: {result['error']}")
            continue

        ext = result["extraction"]
        if not ext:
            continue

        subject = (ext.subject or "")[:40]
        print(f"\n  {subject}")
        print(f"    kind:                 {ext.email_kind.value}")
        print(f"    bank_credits:         {len(ext.bank_credits)} (total={ext.total_bank_credits})")
        print(f"    allocations:          {len(ext.invoice_allocations)} (total={ext.total_net_allocated})")
        print(f"    extraction_status:    {ext.extraction_status.value}")
        print(f"    confidence:           {ext.confidence:.3f}")
        print(f"    routing:              {ext.routing_decision.value}")

        if ext.resolution:
            res = ext.resolution
            print(f"    resolution:")
            print(f"      customer:           {ext.customer_reference} → resolved={res.customer_resolved}")
            print(f"      invoices:           {res.invoices_resolved}/{res.invoices_total}")
            if res.unresolved_invoice_numbers:
                print(f"      unresolved:         {res.unresolved_invoice_numbers}")
            if res.resolution_error:
                print(f"      ERROR:              {res.resolution_error}")