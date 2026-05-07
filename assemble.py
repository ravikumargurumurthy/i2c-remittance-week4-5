# assemble.py
"""
Assemble the final RemittanceExtraction from upstream extraction stages.

Combines:
- Email metadata (from input)
- Triage results (email_kind, payment_intent)
- Bank credits (from Day 2)
- Allocations (from Day 3)
- Reconciliation (from Day 4 reconcile.py)
- Confidence + routing (from Day 4 confidence.py)

Produces a RemittanceExtraction object that's the agent's deliverable.
"""

from datetime import datetime
from typing import Optional

from confidence import compute_confidence, route_by_confidence
from reconcile import ReconciliationResult
from schemas import (
    BankCreditLine,
    EmailKind,
    InvoiceAllocation,
    PaymentIntent,
    RemittanceExtraction,
)
from triage import TriageResult


def assemble_extraction(
    message_id: str,
    email_json: dict,
    triage: TriageResult,
    bank_credits: list[BankCreditLine],
    allocations: list[InvoiceAllocation],
    reconciliation: ReconciliationResult,
) -> RemittanceExtraction:
    """Build the final RemittanceExtraction object."""
    # Pull email metadata
    received_at = _parse_datetime(email_json.get("receivedDateTime"))
    sender_email = (
        email_json.get("sender", {})
        .get("emailAddress", {})
        .get("address")
    )
    subject = email_json.get("subject")

    # Pull payment_intent from triage signals
    intent_value = triage.detected_signals.get("payment_intent")
    intent_remarks_raw = triage.detected_signals.get("intent_remark_raw")
    payment_intent = _coerce_payment_intent(intent_value)

    # Compute confidence
    confidence, confidence_reasoning = compute_confidence(
        email_kind=triage.email_kind,
        payment_intent=payment_intent,
        bank_credits=bank_credits,
        allocations=allocations,
        extraction_status=reconciliation.extraction_status,
    )

    # Route based on confidence
    routing = route_by_confidence(confidence)

    # Aggregate single-customer fields when all allocations agree
    customer_reference, customer_name = _resolve_unified_customer(allocations)

    # Combine reconciliation notes + confidence reasoning into extraction_notes
    notes_parts = [reconciliation.notes]
    if confidence_reasoning:
        notes_parts.append(f"Confidence breakdown: {confidence_reasoning}")
    extraction_notes = " | ".join(notes_parts)

    return RemittanceExtraction(
        message_id=message_id,
        received_at=received_at,
        sender_email=sender_email,
        subject=subject,
        email_kind=triage.email_kind,
        payment_intent=payment_intent or PaymentIntent.INVOICE_PAYMENT,
        intent_remarks_raw=intent_remarks_raw,
        bank_credits=bank_credits,
        invoice_allocations=allocations,
        customer_reference=customer_reference,
        customer_name=customer_name,
        total_bank_credits=reconciliation.total_bank_credits,
        total_net_allocated=reconciliation.total_net_allocated,
        reconciliation_diff=reconciliation.reconciliation_diff,
        extraction_status=reconciliation.extraction_status,
        routing_decision=routing,
        confidence=confidence,
        extraction_notes=extraction_notes,
    )


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse Microsoft Graph datetime format like '2025-12-30T07:00:39Z'."""
    if not value:
        return None
    try:
        # Replace 'Z' with '+00:00' for Python datetime parsing
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _coerce_payment_intent(value: Optional[str]) -> Optional[PaymentIntent]:
    """Coerce string value to PaymentIntent enum."""
    if not value:
        return None
    try:
        return PaymentIntent(value)
    except ValueError:
        return None


def _resolve_unified_customer(
    allocations: list[InvoiceAllocation],
) -> tuple[Optional[str], Optional[str]]:
    """If all allocations have the same customer, return (ref, name).

    Returns (None, None) when allocations are empty or customers differ.
    """
    if not allocations:
        return None, None

    refs = {a.customer_reference for a in allocations if a.customer_reference}
    names = {a.customer_name for a in allocations if a.customer_name}

    customer_reference = refs.pop() if len(refs) == 1 else None
    customer_name = names.pop() if len(names) == 1 else None

    return customer_reference, customer_name
