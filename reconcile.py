# reconcile.py
"""
Email-internal reconciliation for the remittance extraction agent.

Verifies the email's OWN arithmetic — does the email's bank credit total
match the email's allocation total? This is NOT cross-source matching
against the open ledger or bank statements (that's Project 2).

Outputs:
- total_bank_credits: sum of bank_credits[].amount
- total_net_allocated: sum of allocations[].net_amount (or gross_amount
  fallback when net is absent in template)
- reconciliation_diff: total_bank_credits - total_net_allocated
- extraction_status: classification of the diff

Real-world patterns observed in samples:
- Clean (KNK, YES BANK): diff is exactly zero
- Access payment (MOANA): diff > 0; customer overpaid
- Rounding diff (MPSEZ): diff is small (<1 INR), source-data artifact
- Not applicable (partial_booking, on_account_only): no allocations to
  reconcile against
"""

from decimal import Decimal
from typing import Optional

from schemas import (
    BankCreditLine,
    EmailKind,
    ExtractionStatus,
    InvoiceAllocation,
)


# ============================================================
# Tolerance for "rounding diff" classification
# ============================================================

# Rounding-diff threshold per allocation row.
# Real-world Indian financial tables can have paise-level inconsistencies.
# 1 INR per row is generous; protects against hidden bugs while accepting
# legitimate source-data rounding.
ROUNDING_TOLERANCE_PER_ROW = Decimal("1.00")


# ============================================================
# Result type
# ============================================================

class ReconciliationResult:
    """Outcome of email-internal reconciliation."""

    def __init__(
        self,
        total_bank_credits: Optional[Decimal],
        total_net_allocated: Optional[Decimal],
        reconciliation_diff: Optional[Decimal],
        extraction_status: ExtractionStatus,
        notes: str,
    ):
        self.total_bank_credits = total_bank_credits
        self.total_net_allocated = total_net_allocated
        self.reconciliation_diff = reconciliation_diff
        self.extraction_status = extraction_status
        self.notes = notes


# ============================================================
# Core function
# ============================================================

def reconcile(
    email_kind: EmailKind,
    bank_credits: list[BankCreditLine],
    allocations: list[InvoiceAllocation],
) -> ReconciliationResult:
    """Reconcile email-internal arithmetic.

    Args:
        email_kind: classification from triage
        bank_credits: extracted by Day 2
        allocations: extracted by Day 3 (may be empty)

    Returns:
        ReconciliationResult with totals, diff, and status classification.
    """
    # Skip cases where reconciliation isn't applicable
    if email_kind == EmailKind.NON_REMITTANCE:
        return ReconciliationResult(
            total_bank_credits=None,
            total_net_allocated=None,
            reconciliation_diff=None,
            extraction_status=ExtractionStatus.NOT_REMITTANCE,
            notes="Not a payment notification; reconciliation skipped.",
        )

    if email_kind == EmailKind.NEEDS_ATTACHMENT_PARSING:
        return ReconciliationResult(
            total_bank_credits=None,
            total_net_allocated=None,
            reconciliation_diff=None,
            extraction_status=ExtractionStatus.DEFERRED,
            notes=(
                "Body lacks tables; reconciliation deferred to attachment "
                "processing (Project 1 Day 8-9 extension)."
            ),
        )

    # Compute bank credit total (sum of all bank_credits)
    if not bank_credits:
        # Should not happen for FULL/PARTIAL/ON_ACCOUNT after Day 2;
        # treat as exception-level reconciliation failure
        return ReconciliationResult(
            total_bank_credits=None,
            total_net_allocated=None,
            reconciliation_diff=None,
            extraction_status=ExtractionStatus.NOT_APPLICABLE,
            notes=(
                f"Email kind {email_kind.value} but no bank credits "
                f"extracted. Reconciliation cannot proceed."
            ),
        )

    total_bank = sum(
        (bc.amount for bc in bank_credits),
        start=Decimal("0"),
    )

    # If there are no allocations (partial_booking, on_account_only):
    # reconciliation isn't applicable — the email isn't claiming a
    # specific allocation to reconcile against.
    if not allocations:
        return ReconciliationResult(
            total_bank_credits=total_bank,
            total_net_allocated=None,
            reconciliation_diff=None,
            extraction_status=ExtractionStatus.NOT_APPLICABLE,
            notes=(
                f"Bank credit total {total_bank} but no allocations to "
                f"reconcile against. Customer reference or account number "
                f"present; specific invoice allocation deferred to "
                f"downstream matching."
            ),
        )

    # Compute allocations total: prefer net_amount, fall back to gross_amount
    # when net is absent (J B BODA / YES BANK templates).
    # Note: for negative-amount rows (credit memos), the sign is preserved.
    total_allocated = _compute_allocations_total(allocations)

    if total_allocated is None:
        return ReconciliationResult(
            total_bank_credits=total_bank,
            total_net_allocated=None,
            reconciliation_diff=None,
            extraction_status=ExtractionStatus.NOT_APPLICABLE,
            notes=(
                f"Allocations present but no usable amount column "
                f"(neither net_amount nor gross_amount populated). "
                f"Reconciliation cannot be computed."
            ),
        )

    # Compute the diff
    diff = total_bank - total_allocated

    # Classify
    status, notes = _classify_diff(
        diff=diff,
        n_allocations=len(allocations),
        total_bank=total_bank,
        total_allocated=total_allocated,
    )

    return ReconciliationResult(
        total_bank_credits=total_bank,
        total_net_allocated=total_allocated,
        reconciliation_diff=diff,
        extraction_status=status,
        notes=notes,
    )


def _compute_allocations_total(
    allocations: list[InvoiceAllocation],
) -> Optional[Decimal]:
    """Sum allocation amounts, preferring net_amount with gross_amount fallback.

    For each row, use net_amount if set; otherwise fall back to gross_amount.
    This handles templates that omit Net Amount column (J B BODA, YES BANK).

    If neither is set on any row, returns None (reconciliation impossible).
    """
    total = Decimal("0")
    has_any_amount = False

    for alloc in allocations:
        if alloc.net_amount is not None:
            total += alloc.net_amount
            has_any_amount = True
        elif alloc.gross_amount is not None:
            total += alloc.gross_amount
            has_any_amount = True
        # If neither is set, skip this row silently — but if NO row has
        # either, return None below.

    return total if has_any_amount else None


def _classify_diff(
    diff: Decimal,
    n_allocations: int,
    total_bank: Decimal,
    total_allocated: Decimal,
) -> tuple[ExtractionStatus, str]:
    """Classify the reconciliation diff into an ExtractionStatus."""
    abs_diff = abs(diff)

    # Tolerance scales with number of allocation rows (1 INR per row max)
    rounding_threshold = ROUNDING_TOLERANCE_PER_ROW * n_allocations

    if abs_diff == Decimal("0"):
        return (
            ExtractionStatus.CLEAN,
            f"Clean: bank credit ({total_bank}) matches allocations exactly.",
        )

    if abs_diff <= rounding_threshold:
        return (
            ExtractionStatus.ROUNDING_DIFF,
            (
                f"Rounding diff: bank credit {total_bank} vs allocations "
                f"{total_allocated} differs by {diff} (within {rounding_threshold} "
                f"tolerance for {n_allocations} rows). Likely source-data "
                f"rounding artifact, not a real discrepancy."
            ),
        )

    if diff > 0:
        # Bank credit exceeds allocations → overpayment
        return (
            ExtractionStatus.ACCESS_PAYMENT,
            (
                f"Access payment: customer paid {total_bank} but only "
                f"allocated {total_allocated}. Excess of {diff} should be "
                f"parked as advance receipt or credited to customer account."
            ),
        )

    # diff < 0 → allocations exceed bank credit
    return (
        ExtractionStatus.ALLOCATION_EXCEEDS_PAYMENT,
        (
            f"Allocation exceeds payment: allocations claim {total_allocated} "
            f"but bank credit is only {total_bank} (short by {abs_diff}). "
            f"Email may have a discrepancy; route to HITL review."
        ),
    )
