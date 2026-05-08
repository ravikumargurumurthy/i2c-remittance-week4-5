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

Customer fields (customer_reference, customer_name) are populated via:
1. Aggregation across allocations (when all agree) — primary source
2. Fallback for on_account_only / partial_booking: account reference from
   triage signals
3. Customer master lookup as the AUTHORITATIVE source for customer_name
   when customer_reference is known (preferred over bank narrative)
4. Bank narrative payer name as last-resort fallback for customer_name
"""

import sys
from datetime import datetime
from typing import Optional

from confidence import compute_confidence, route_by_confidence
from db import get_customer_by_number
from reconcile import ReconciliationResult
from schemas import (
    BankCreditLine,
    CustomerOnlyEntry,
    EmailKind,
    InvoiceAllocation,
    PaymentIntent,
    RemittanceExtraction,
)
from triage import TriageResult


# Email kinds where customer info should be derived from non-allocation sources
NON_ALLOCATION_KINDS = {
    EmailKind.ON_ACCOUNT_ONLY,
    EmailKind.PARTIAL_BOOKING,
}


def assemble_extraction(
    message_id: str,
    email_json: dict,
    triage: TriageResult,
    bank_credits: list[BankCreditLine],
    allocations: list[InvoiceAllocation],
    reconciliation: ReconciliationResult,
    customer_entries: Optional[list] = None,
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

    # Resolve customer reference and name with fallback chain
    customer_reference, customer_name = _resolve_customer_info(
        triage=triage,
        allocations=allocations,
        bank_credits=bank_credits,
        customer_entries=customer_entries,  # NEW
    )

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
        customer_entries=customer_entries,
        total_bank_credits=reconciliation.total_bank_credits,
        total_net_allocated=reconciliation.total_net_allocated,
        reconciliation_diff=reconciliation.reconciliation_diff,
        extraction_status=reconciliation.extraction_status,
        routing_decision=routing,
        confidence=confidence,
        extraction_notes=extraction_notes,
    )


# ============================================================
# Customer resolution with fallback chain
# ============================================================

def _resolve_customer_info(
    triage: TriageResult,
    allocations: list[InvoiceAllocation],
    bank_credits: list[BankCreditLine],
    customer_entries: list[CustomerOnlyEntry],
) -> tuple[Optional[str], Optional[str]]:
    """Resolve customer_reference and customer_name with fallback chain.

    Priority for customer_reference:
    1. Aggregated from allocations (when all agree)
    2. Customer-only table (e.g., VINAYAK's FIFO email)
    3. Account reference from triage signals (for non-allocation kinds)

    Priority for customer_name:
    1. Aggregated from allocations (when all agree)
    2. Customer-only table (when present, as authoritative source)
    3. Customer master lookup using customer_reference
    4. Bank credit's payer_name_in_narrative (last-resort fallback)
    """
    # Primary: aggregate across allocations
    customer_reference, customer_name = _resolve_unified_customer(allocations)

    # Layer 2: customer-only table (VINAYAK pattern)
    # If multiple entries, take the first one (rare in practice)
    if customer_entries and (not customer_reference or not customer_name):
        first_entry = customer_entries[0]
        if not customer_reference:
            customer_reference = first_entry.customer_number
        if not customer_name:
            customer_name = first_entry.customer_name

    # Layer 3: Triage signals (on_account_only, partial_booking)
    if not customer_reference and triage.email_kind in NON_ALLOCATION_KINDS:
        account_ref = (
            triage.detected_signals.get("account_reference")
            or triage.detected_signals.get("customer_reference")
        )
        if account_ref:
            customer_reference = str(account_ref)

    # Layer 4: Customer master lookup (authoritative source for name)
    if not customer_name and customer_reference:
        customer_name = _lookup_customer_name_in_master(customer_reference)

    # Layer 5: Bank narrative payer name (last resort)
    if not customer_name and bank_credits:
        for bc in bank_credits:
            if bc.payer_name_in_narrative:
                customer_name = bc.payer_name_in_narrative
                break

    return customer_reference, customer_name


def _lookup_customer_name_in_master(customer_reference: str) -> Optional[str]:
    """Look up canonical customer name from customer master.

    Returns customer_name from t_customer_master (or wherever
    get_customer_by_number reads from), or None if not found / lookup failed.
    """
    try:
        customer = get_customer_by_number(customer_reference)
        if customer and getattr(customer, "customer_name", None):
            return customer.customer_name
    except Exception as e:
        # Lookup failure shouldn't block extraction — fall through to next layer
        print(
            f"[WARN] Customer master lookup failed for {customer_reference!r}: "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
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


# ============================================================
# Helpers
# ============================================================

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