# triage.py
"""
Rule-based triage classifier for incoming emails.

Decides which EmailKind an email belongs to using deterministic rules
on the email body's HTML structure. No LLM calls.

Rules (applied in order):

1. If body has NO bank-credit table:
   - If hasAttachments=true → NEEDS_ATTACHMENT_PARSING
   - Otherwise → NON_REMITTANCE

2. If body has a bank-credit table AND an allocation table → FULL_BOOKING

3. If body has a bank-credit table BUT no allocation table:
   - If body has a customer-only table → PARTIAL_BOOKING
   - If body has 'On A/C - <number>' reference → ON_ACCOUNT_ONLY
   - Otherwise → PARTIAL_BOOKING (best-guess fallback; conservative)
"""

from dataclasses import dataclass
from typing import Optional

from html_tools import (
    extract_plain_text,
    find_account_reference,
    find_allocation_table,
    find_bank_credit_table,
    find_customer_only_table,
    find_payment_intent,
)
from schemas import EmailKind


@dataclass
class TriageResult:
    """Output of the triage classifier."""
    email_kind: EmailKind
    reasoning: str  # human-readable explanation, useful for debugging
    detected_signals: dict  # which finders fired and which didn't


def classify_email(email_json: dict) -> TriageResult:
    """Classify an email into one of the five EmailKind values.

    Pure function. No state. No external calls.
    """
    body = email_json.get("body", {})
    html = body.get("content", "") or ""
    has_attachments = bool(email_json.get("hasAttachments", False))
    text = extract_plain_text(html)

    # Run all three finders
    bank_credit = find_bank_credit_table(html)
    allocation = find_allocation_table(html)
    customer_only = find_customer_only_table(html)
    account_ref = find_account_reference(text)

    intent_value, intent_remark_raw = find_payment_intent(text)

    if account_ref and not intent_value:
        intent_value = "on_account"
        intent_remark_raw = f"On A/C - {account_ref}"

    signals = {
        "has_bank_credit_table": bank_credit is not None,
        "has_allocation_table": allocation is not None,
        "has_customer_only_table": customer_only is not None,
        "account_reference": account_ref,
        "has_attachments": has_attachments,
        "payment_intent": intent_value,   # NEW
        "intent_remark_raw": intent_remark_raw,   # NEW
    }

    # Rule 1: no bank-credit table
    if not bank_credit:
        if has_attachments:
            return TriageResult(
                email_kind=EmailKind.NEEDS_ATTACHMENT_PARSING,
                reasoning=(
                    "No bank-credit table found in body, but hasAttachments=true. "
                    "Remittance details may be in attachments — defer to attachment "
                    "parsing (Project 1 Day 8-9 extension)."
                ),
                detected_signals=signals,
            )
        return TriageResult(
            email_kind=EmailKind.NON_REMITTANCE,
            reasoning=(
                "No bank-credit table found in body and no attachments. "
                "Email does not appear to contain payment notification."
            ),
            detected_signals=signals,
        )

    # Rule 2: bank credit + allocation table → full_booking
    if allocation:
        return TriageResult(
            email_kind=EmailKind.FULL_BOOKING,
            reasoning=(
                f"Bank-credit table found ({bank_credit['row_count']}×"
                f"{bank_credit['col_count']}) and allocation table found "
                f"({allocation['row_count']}×{allocation['col_count']}). "
                f"Both tables present indicates a complete booking instruction."
            ),
            detected_signals=signals,
        )

    # Rule 3: bank credit but no allocation
    if customer_only:
        return TriageResult(
            email_kind=EmailKind.PARTIAL_BOOKING,
            reasoning=(
                f"Bank-credit table found ({bank_credit['row_count']}×"
                f"{bank_credit['col_count']}) and customer-only table found "
                f"({customer_only['row_count']}×{customer_only['col_count']}). "
                f"Customer is identified but invoice allocation is deferred "
                f"(typically a 'FIFO basis' or 'oldest first' instruction)."
            ),
            detected_signals=signals,
        )

    if account_ref:
        return TriageResult(
            email_kind=EmailKind.ON_ACCOUNT_ONLY,
            reasoning=(
                f"Bank-credit table found and 'On A/C - {account_ref}' reference "
                f"detected. Customer's account is identified but specific invoice "
                f"allocation is deferred to a later step."
            ),
            detected_signals=signals,
        )

    # Fallback: bank credit alone, no other signals — treat as partial_booking
    # (conservative: assume customer info is implicit and downstream will handle)
    return TriageResult(
        email_kind=EmailKind.PARTIAL_BOOKING,
        reasoning=(
            "Bank-credit table found but no allocation, customer-only table, "
            "or account reference. Treating as partial_booking (conservative); "
            "may need HITL review."
        ),
        detected_signals=signals,
    )
