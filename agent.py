# agent.py
"""
Remittance extraction agent — Day 2 expanded with bank credit extraction.

State machine:
    START → triage → extract_bank_credits → END

Day 1: Triage (rule-based) — classifies email_kind and detects payment_intent
Day 2: Bank credit extraction (LLM) — populates bank_credits list

Days 3-7 will add nodes for invoice allocation extraction, reconciliation,
customer master resolution, and routing decisions. The state structure is
ready for those without rewrites.
"""

from operator import add
from typing import Annotated, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from extract_bank_credit import extract_bank_credits_from_table
from html_tools import find_bank_credit_table
from schemas import (
    BankCreditLine,
    EmailKind,
    RemittanceExtraction,
)
from triage import TriageResult, classify_email


# ============================================================
# Email kinds that should run bank credit extraction
# ============================================================

EXTRACTION_ELIGIBLE_KINDS = {
    EmailKind.FULL_BOOKING,
    EmailKind.PARTIAL_BOOKING,
    EmailKind.ON_ACCOUNT_ONLY,
}


# ============================================================
# Agent state
# ============================================================

class AgentState(BaseModel):
    # Input
    message_id: str
    email_json: dict

    # Triage result (populated by triage_node)
    triage: Optional[TriageResult] = None

    # Bank credit extraction (populated by extract_bank_credits_node)
    bank_credits: list[BankCreditLine] = Field(default_factory=list)

    # Final output (built up across nodes; complete at END)
    extraction: Optional[RemittanceExtraction] = None

    # Conversation log (used by future LLM nodes)
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
    """Extract bank credit rows for emails that have a bank credit table.

    No-op for non_remittance and needs_attachment_parsing.
    """
    if state.error:
        return {}
    if not state.triage:
        return {"error": "extract_bank_credits called before triage completed"}
    if state.triage.email_kind not in EXTRACTION_ELIGIBLE_KINDS:
        # Non-remittance or deferred — nothing to extract
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
        return {"error": f"Bank credit extraction failed: {type(e).__name__}: {e}"}

    return {"bank_credits": credits}


# ============================================================
# Build graph
# ============================================================

builder = StateGraph(AgentState)
builder.add_node("triage", triage_node)
builder.add_node("extract_bank_credits", extract_bank_credits_node)

builder.add_edge(START, "triage")
builder.add_edge("triage", "extract_bank_credits")
builder.add_edge("extract_bank_credits", END)

graph = builder.compile()


# ============================================================
# Public API
# ============================================================

def process_email(message_id: str, email_json: dict) -> dict:
    """Run the agent on one email.

    Returns a dict with:
        - 'triage': TriageResult
        - 'bank_credits': list[BankCreditLine]
        - 'extraction': RemittanceExtraction (None for now; built in Days 3-7)
        - 'error': str if anything failed
    """
    initial = AgentState(message_id=message_id, email_json=email_json)
    final_state = graph.invoke(initial)

    return {
        "triage": final_state.get("triage"),
        "bank_credits": final_state.get("bank_credits", []),
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
    print("Day 2 Demo — Triage + Bank Credit Extraction")
    print("=" * 80)
    for mid in src.list_known_message_ids():
        email = src.get_email(mid)
        result = process_email(mid, email)

        if result["error"]:
            print(f"\n  ERROR: {result['error']}")
            continue

        triage = result["triage"]
        credits = result["bank_credits"]
        subject = email.get("subject", "")[:40]

        print(f"\n  {subject}")
        print(f"    kind: {triage.email_kind.value}")
        print(f"    intent: {triage.detected_signals.get('payment_intent')}")
        print(f"    bank_credits: {len(credits)}")
        for c in credits:
            mode = c.payment_mode.value if c.payment_mode else "?"
            payer = (c.payer_name_in_narrative or "?")[:25]
            print(f"      • {mode} | UTR={c.bank_utr} | {payer} | {c.amount}")