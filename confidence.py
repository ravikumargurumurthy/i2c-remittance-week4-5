# confidence.py
"""
Confidence scoring for remittance extractions.

Rule-based, no LLM. Confidence is a function of:
- Field completeness (which fields are populated)
- Reconciliation outcome (clean vs diff vs deferred)
- Extraction quality signals (UTRs present, customer references resolved)

Output: a 0.0-1.0 score that drives routing band.

Why rule-based: confidence formulas are easier to test, easier to explain,
and stay stable across LLM variance. The LLM has done its work upstream;
confidence is downstream summarization of structured fields.
"""

from decimal import Decimal
from typing import Optional

from schemas import (
    BankCreditLine,
    EmailKind,
    ExtractionStatus,
    InvoiceAllocation,
    PaymentIntent,
    RoutingDecision,
)


# ============================================================
# Routing thresholds (mirror Week 3's bank importer)
# ============================================================

AUTO_APPLY_THRESHOLD = 0.94
HITL_REVIEW_THRESHOLD = 0.70


def compute_confidence(
    email_kind: EmailKind,
    payment_intent: Optional[PaymentIntent],
    bank_credits: list[BankCreditLine],
    allocations: list[InvoiceAllocation],
    extraction_status: ExtractionStatus,
) -> tuple[float, str]:
    """Compute confidence (0.0-1.0) and a human-readable reasoning string.

    Returns (confidence_score, reasoning).
    """
    # Special cases
    if email_kind == EmailKind.NON_REMITTANCE:
        # Not a remittance — high confidence in the "skip" classification
        return 1.0, "Not a remittance email; no extraction needed."

    if email_kind == EmailKind.NEEDS_ATTACHMENT_PARSING:
        # Deferred — moderate confidence in the "defer" decision
        return 0.85, (
            "Email has attachments but body lacks tables; deferred to "
            "attachment processing (planned). Confidence is in the routing "
            "decision, not in extraction completeness."
        )

    # Real extraction cases
    score = 0.0
    reasoning_parts = []

    # ---- Bank credit completeness (max 0.30) ----
    if bank_credits:
        bc_score = _score_bank_credits(bank_credits)
        score += bc_score * 0.30
        reasoning_parts.append(
            f"bank_credit_completeness={bc_score:.2f} (weight 0.30)"
        )
    else:
        reasoning_parts.append("no bank credits (-0.30)")

    # ---- Allocation completeness (max 0.30) ----
    if allocations:
        alloc_score = _score_allocations(allocations)
        score += alloc_score * 0.30
        reasoning_parts.append(
            f"allocation_completeness={alloc_score:.2f} (weight 0.30)"
        )
    elif email_kind == EmailKind.FULL_BOOKING:
        # full_booking with no allocations = bug; penalize heavily
        reasoning_parts.append("full_booking but NO allocations extracted (-0.30)")
    else:
        # partial_booking / on_account_only legitimately have no allocations
        # Don't penalize — give partial credit for the data we do have
        score += 0.30  # full credit — these are known operational patterns
        reasoning_parts.append(
            f"{email_kind.value}: no allocations expected (+0.30 full credit)"
        )

    # ---- Reconciliation status (max 0.25) ----
    recon_score = _score_reconciliation_status(extraction_status)
    score += recon_score * 0.25
    reasoning_parts.append(
        f"reconciliation={extraction_status.value} → {recon_score:.2f} "
        f"(weight 0.25)"
    )

    # ---- Special instruction handling (max 0.15) ----
    if payment_intent and payment_intent != PaymentIntent.INVOICE_PAYMENT:
        # Special instruction detected — slightly lower confidence
        # (these need human review more often)
        score += 0.10
        reasoning_parts.append(
            f"payment_intent={payment_intent.value} (+0.10; needs review)"
        )
    else:
        score += 0.15
        reasoning_parts.append(
            "payment_intent=invoice_payment / standard (+0.15)"
        )

    # Clamp to [0, 1]
    score = max(0.0, min(1.0, score))
    reasoning = "; ".join(reasoning_parts)

    return score, reasoning


def _score_bank_credits(bank_credits: list[BankCreditLine]) -> float:
    """Score 0.0-1.0 based on bank credit field completeness.

    Per credit:
    - amount populated: required (always true if we got here)
    - bank_utr populated: +0.4
    - payer_name_in_narrative populated: +0.3
    - payer_bank populated: +0.2
    - payment_mode != OTHER: +0.1

    Average across all credits.
    """
    if not bank_credits:
        return 0.0

    from schemas import PaymentMode

    total = 0.0
    for bc in bank_credits:
        per_credit = 0.0
        if bc.bank_utr:
            per_credit += 0.4
        if bc.payer_name_in_narrative:
            per_credit += 0.3
        if bc.payer_bank:
            per_credit += 0.2
        if bc.payment_mode and bc.payment_mode != PaymentMode.OTHER:
            per_credit += 0.1
        total += per_credit

    return total / len(bank_credits)


def _score_allocations(allocations: list[InvoiceAllocation]) -> float:
    """Score 0.0-1.0 based on allocation field completeness.

    Per allocation:
    - customer_reference populated: +0.3
    - invoice_number populated: +0.4 (most important)
    - At least gross_amount or net_amount populated: +0.3

    Average across all allocations.
    """
    if not allocations:
        return 0.0

    total = 0.0
    for alloc in allocations:
        per_alloc = 0.0
        if alloc.customer_reference:
            per_alloc += 0.3
        if alloc.invoice_number:
            per_alloc += 0.4
        if alloc.gross_amount is not None or alloc.net_amount is not None:
            per_alloc += 0.3
        total += per_alloc

    return total / len(allocations)


def _score_reconciliation_status(status: ExtractionStatus) -> float:
    """Score 0.0-1.0 based on reconciliation outcome."""
    return {
        ExtractionStatus.CLEAN: 1.0,
        ExtractionStatus.ROUNDING_DIFF: 0.9,  # near-clean, paise-level
        ExtractionStatus.ACCESS_PAYMENT: 0.7,  # known case but needs handling
        ExtractionStatus.ALLOCATION_EXCEEDS_PAYMENT: 0.4,  # unusual; HITL
        ExtractionStatus.NOT_APPLICABLE: 1.0,  # legitimate for partial/on_account
        ExtractionStatus.NOT_REMITTANCE: 1.0,  # confident in skip
        ExtractionStatus.DEFERRED: 0.7,  # confident in defer decision
    }.get(status, 0.5)


def route_by_confidence(confidence: float) -> RoutingDecision:
    """Map confidence score to routing band."""
    if confidence >= AUTO_APPLY_THRESHOLD:
        return RoutingDecision.AUTO_APPLY
    if confidence >= HITL_REVIEW_THRESHOLD:
        return RoutingDecision.HITL_REVIEW
    return RoutingDecision.EXCEPTION
