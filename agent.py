# agent.py
"""
Remittance extraction agent — Day 1 skeleton with triage only.

State machine:
    START → triage → END

Days 2-7 will add nodes for bank-credit extraction, allocation extraction,
reconciliation, and routing decisions. The state structure is set up to
support those without rewrites.
"""

from operator import add
from typing import Annotated, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from schemas import EmailKind, RemittanceExtraction
from triage import TriageResult, classify_email


# ============================================================
# Agent state
# ============================================================

class AgentState(BaseModel):
    # Input
    message_id: str
    email_json: dict

    # Triage result
    triage: Optional[TriageResult] = None

    # Conversation log (used by LLM nodes in later days)
    messages: Annotated[list[BaseMessage], add] = Field(default_factory=list)

    # Final output (built up across nodes; complete at END)
    extraction: Optional[RemittanceExtraction] = None

    # Error tracking
    error: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True  # for TriageResult dataclass


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


# ============================================================
# Build graph
# ============================================================

builder = StateGraph(AgentState)
builder.add_node("triage", triage_node)
builder.add_edge(START, "triage")
builder.add_edge("triage", END)

graph = builder.compile()


# ============================================================
# Public API
# ============================================================

def process_email(message_id: str, email_json: dict) -> dict:
    """Run the agent on one email.

    Returns a dict with:
        - 'triage': TriageResult
        - 'extraction': RemittanceExtraction (None for now; built in Days 2-7)
        - 'error': str if anything failed
    """
    initial = AgentState(message_id=message_id, email_json=email_json)
    final_state = graph.invoke(initial)

    return {
        "triage": final_state.get("triage"),
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
    print("Triage demo — all 10 samples")
    print("=" * 80)
    for mid in src.list_known_message_ids():
        email = src.get_email(mid)
        result = process_email(mid, email)
        triage = result["triage"]
        if triage:
            subject = email.get("subject", "(no subject)")[:50]
            print(f"  {triage.email_kind.value:30s} {subject}")
        else:
            print(f"  ERROR: {result['error']}")
