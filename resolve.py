# resolve.py
"""
Master data resolution for the remittance extraction agent.

For each RemittanceExtraction:
- Look up the customer_reference (from allocations or top-level) in
  t_customer_master to get canonical customer_number and customer_name
- For each allocation's invoice_number, verify it exists in t_invoice_header
- Aggregate findings into a ResolutionInfo object

Why deterministic (no LLM): these are pure database lookups. Same input,
same output. LLM would add cost and variance with no benefit.

Why caught at extraction time (vs deferred to Project 2): unresolved
references are a real signal something's off — wrong customer ID, typo'd
invoice number, an invoice that's already been settled. Catching them at
extraction time means they show up in HITL review with full context,
rather than as a mysterious match failure later.
"""

import sys
from typing import Optional

from db import get_customer_by_number, get_invoice_by_number
from schemas import EmailKind, RemittanceExtraction, ResolutionInfo


# Email kinds that should run resolution
RESOLUTION_ELIGIBLE_KINDS = {
    EmailKind.FULL_BOOKING,
    EmailKind.PARTIAL_BOOKING,
    EmailKind.ON_ACCOUNT_ONLY,
}


def resolve(extraction: RemittanceExtraction) -> ResolutionInfo:
    """Resolve customer and invoice references against master tables.

    Returns a ResolutionInfo capturing what was looked up and what was found.
    On any DB error, returns a ResolutionInfo with resolution_error set
    rather than raising — caller continues with degraded confidence.
    """
    # Skip resolution for kinds that don't have references to resolve
    if extraction.email_kind not in RESOLUTION_ELIGIBLE_KINDS:
        return ResolutionInfo(
            resolution_notes=(
                f"Resolution skipped for {extraction.email_kind.value}; "
                f"no customer or invoice references to resolve."
            ),
        )

    # Resolve the customer (single lookup; one customer per remittance)
    customer_ref = _pick_customer_reference(extraction)
    customer_resolved = None
    canonical_customer_number = None
    canonical_customer_name = None

    if customer_ref:
        try:
            customer = get_customer_by_number(customer_ref)
        except Exception as e:
            print(
                f"[WARN] Customer lookup failed for {customer_ref!r}: "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )
            return ResolutionInfo(
                customer_resolved=None,
                resolution_error=(
                    f"Customer lookup failed: {type(e).__name__}: {e}"
                ),
                resolution_notes="Resolution aborted due to DB error.",
            )

        if customer:
            customer_resolved = True
            canonical_customer_number = getattr(customer, "customer_number", None)
            canonical_customer_name = getattr(customer, "customer_name", None)
        else:
            customer_resolved = False

    # Resolve invoices (one lookup per allocation row)
    invoices_total = 0
    invoices_resolved = 0
    unresolved_invoices = []

    for allocation in extraction.invoice_allocations:
        if not allocation.invoice_number:
            continue
        invoices_total += 1

        lookup_customer = (
            allocation.customer_reference
            or canonical_customer_number
            or customer_ref
        )

        if not lookup_customer:
            # Can't look up the invoice without a customer
            unresolved_invoices.append(allocation.invoice_number)
            continue

        try:
            invoice = get_invoice_by_number(
                customer_number=lookup_customer,
                invoice_number=allocation.invoice_number,
            )
        except Exception as e:
            print(
                f"[WARN] Invoice lookup failed for {allocation.invoice_number!r} "
                f"(customer={lookup_customer!r}): "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )
            return ResolutionInfo(
                customer_resolved=customer_resolved,
                canonical_customer_number=canonical_customer_number,
                canonical_customer_name=canonical_customer_name,
                invoices_total=invoices_total,
                invoices_resolved=invoices_resolved,
                resolution_error=(
                    f"Invoice lookup failed at row {allocation.invoice_number!r} "
                    f"(customer={lookup_customer!r}): {type(e).__name__}: {e}"
                ),
                resolution_notes="Resolution partially complete; DB error mid-loop.",
            )

        if invoice:
            invoices_resolved += 1
        else:
            unresolved_invoices.append(allocation.invoice_number)

    # Build summary notes
    notes = _build_resolution_notes(
        customer_ref=customer_ref,
        customer_resolved=customer_resolved,
        canonical_customer_number=canonical_customer_number,
        invoices_total=invoices_total,
        invoices_resolved=invoices_resolved,
        unresolved_invoices=unresolved_invoices,
    )

    return ResolutionInfo(
        customer_resolved=customer_resolved,
        canonical_customer_number=canonical_customer_number,
        canonical_customer_name=canonical_customer_name,
        invoices_total=invoices_total,
        invoices_resolved=invoices_resolved,
        unresolved_invoice_numbers=unresolved_invoices,
        resolution_notes=notes,
    )


def _pick_customer_reference(extraction: RemittanceExtraction) -> Optional[str]:
    """Pick the best customer reference to resolve.

    Priority:
    1. extraction.customer_reference (already aggregated by assemble_node when
       all allocations agree)
    2. First allocation's customer_reference
    3. None (nothing to resolve)
    """
    if extraction.customer_reference:
        return extraction.customer_reference
    for alloc in extraction.invoice_allocations:
        if alloc.customer_reference:
            return alloc.customer_reference
    return None


def _build_resolution_notes(
    customer_ref: Optional[str],
    customer_resolved: Optional[bool],
    canonical_customer_number: Optional[str],
    invoices_total: int,
    invoices_resolved: int,
    unresolved_invoices: list[str],
) -> str:
    """Assemble a human-readable summary of resolution outcomes."""
    parts = []

    if customer_ref:
        if customer_resolved:
            parts.append(
                f"Customer {customer_ref!r} resolved to "
                f"{canonical_customer_number!r}."
            )
        else:
            parts.append(
                f"Customer {customer_ref!r} NOT FOUND in t_customer_master."
            )
    else:
        parts.append("No customer reference present in extraction.")

    if invoices_total > 0:
        if invoices_resolved == invoices_total:
            parts.append(
                f"All {invoices_total} invoice(s) resolved in t_invoice_header."
            )
        else:
            parts.append(
                f"{invoices_resolved}/{invoices_total} invoices resolved; "
                f"unresolved: {unresolved_invoices}"
            )
    else:
        parts.append("No invoices to resolve.")

    return " ".join(parts)
