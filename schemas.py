# schemas.py
"""
Pydantic schemas for the remittance extraction agent.

Designed against the 10 real Adani O2C-GCC sample emails. Handles the four
distinct email kinds observed:
- full_booking: bank credit table + invoice allocation table (most common)
- partial_booking: customer named but no invoice allocation (FIFO instructions)
- on_account_only: bank credit + account reference, no customer name
- non_remittance: not a payment notification (vendor invoice requests, etc.)

And the six payment modes observed across samples:
NEFT, RTGS, IMPS, UPI, IFT, OTHER.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _empty_to_none(value):
    """Coerce empty strings to None. Used by validators for Optional[str] fields."""
    if value == "" or value is None:
        return None
    return value


# ============================================================
# Enums
# ============================================================

class EmailKind(str, Enum):
    """Triage classification for an incoming email."""
    FULL_BOOKING = "full_booking"               # bank credit + invoice allocation in body
    PARTIAL_BOOKING = "partial_booking"         # customer known, allocation deferred
    ON_ACCOUNT_ONLY = "on_account_only"         # bank credit + account ref, no customer
    NON_REMITTANCE = "non_remittance"           # not a payment notification
    NEEDS_ATTACHMENT_PARSING = "needs_attachment_parsing"
    # ↑ Body lacks recognizable tables, but hasAttachments=true.
    # Attachment parsing is planned Project 1 Day 8-9 extension; until then,
    # these emails route to a deferred band rather than wrong classification.


class PaymentMode(str, Enum):
    """Payment modes observed in real bank narratives."""
    NEFT = "NEFT"     # most common — National Electronic Funds Transfer
    RTGS = "RTGS"     # Real-Time Gross Settlement (high-value)
    IMPS = "IMPS"     # Immediate Payment Service
    UPI = "UPI"       # Unified Payments Interface
    IFT = "IFT"       # Internal Funds Transfer / cheque-via-bank
    OTHER = "OTHER"   # truncated / non-standard / unrecognized


# ============================================================
# Component schemas
# ============================================================

class BankCreditLine(BaseModel):
    """One row from the email's bank-credit table.

    Captures both the raw narrative (always present) and parsed fields
    (best-effort, may be None for non-standard formats).
    """
    tran_date: Optional[date] = None
    narrative_raw: str
    payment_mode: Optional[PaymentMode] = None
    bank_utr: Optional[str] = None
    payer_name_in_narrative: Optional[str] = None
    payer_bank: Optional[str] = None
    amount: Decimal
    notes: Optional[str] = Field(
        None,
        description="Extra metadata from narrative (e.g., 'WAGES', sub-categorization)"
    )

    @field_validator("bank_utr", "payer_name_in_narrative", "payer_bank", "notes",
                     mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        return _empty_to_none(v)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_to_decimal(cls, v):
        if v is None:
            raise ValueError("amount is required")
        return Decimal(str(v))

    model_config = ConfigDict(from_attributes=True)


class InvoiceAllocation(BaseModel):
    """One row from the email's invoice-allocation table.

    Real templates use varying column names — Customer/Cust.No/Co Code/Customer
    Code/Code/Company Code for the customer ID column, etc. The schema doesn't
    care what columns are CALLED; the LLM extracts based on what they MEAN.
    """
    customer_reference: Optional[str] = Field(
        None,
        description=(
            "Customer ID as it appears in the email. Format varies: 3-digit "
            "(761), 5-digit (16736), 10-digit (4000000420). Resolved against "
            "t_customer_master.customer_number downstream."
        ),
    )
    customer_name: Optional[str] = None
    invoice_number: Optional[str] = None
    document_type: Optional[str] = Field(
        None,
        description=(
            "SAP doc type observed: RV (regular invoice), DZ (posted payment), "
            "AB (adjustment / credit memo), SA (statement adjustment). Open "
            "vocabulary — others may exist."
        ),
    )
    gross_amount: Optional[Decimal] = None
    tds_amount: Optional[Decimal] = None
    net_amount: Optional[Decimal] = None

    @field_validator("customer_reference", "customer_name", "invoice_number",
                     "document_type", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        return _empty_to_none(v)

    @field_validator("gross_amount", "tds_amount", "net_amount", mode="before")
    @classmethod
    def _amount_to_decimal(cls, v):
        if v is None or v == "":
            return None
        return Decimal(str(v))

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# Top-level schema
# ============================================================

class RemittanceExtraction(BaseModel):
    """The agent's structured output for one email.

    Field population varies by email_kind:
    - full_booking:    bank_credits + invoice_allocations both populated
    - partial_booking: bank_credits populated, allocations empty, customer_ref set
    - on_account_only: bank_credits populated, allocations empty, customer_ref set
    - non_remittance:  both empty, agent explains in extraction_notes
    """
    # Email metadata (echoed from input for audit trail)
    message_id: str
    received_at: Optional[datetime] = None
    sender_email: Optional[str] = None
    subject: Optional[str] = None

    # Triage result
    email_kind: EmailKind

    # Extracted data
    bank_credits: list[BankCreditLine] = Field(default_factory=list)
    invoice_allocations: list[InvoiceAllocation] = Field(default_factory=list)

    # When all rows reference the same customer, populate these for convenience.
    # Otherwise leave None and rely on per-row customer_reference.
    customer_reference: Optional[str] = None
    customer_name: Optional[str] = None

    # Reconciliation (computed when both lists populated)
    total_bank_credits: Optional[Decimal] = None
    total_net_allocated: Optional[Decimal] = None
    reconciliation_diff: Optional[Decimal] = Field(
        None,
        description=(
            "total_bank_credits - total_net_allocated. Zero means clean match. "
            "Non-zero indicates 'access payment' (overpayment) or partial allocation."
        ),
    )

    # Reasoning
    confidence: float = Field(..., ge=0.0, le=1.0)
    extraction_notes: Optional[str] = None

    @field_validator("sender_email", "subject", "customer_reference",
                     "customer_name", "extraction_notes", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        return _empty_to_none(v)

    @field_validator("total_bank_credits", "total_net_allocated",
                     "reconciliation_diff", mode="before")
    @classmethod
    def _amount_to_decimal(cls, v):
        if v is None or v == "":
            return None
        return Decimal(str(v))

    model_config = ConfigDict(from_attributes=True)
