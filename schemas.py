# schemas.py
"""
Pydantic schemas for the remittance extraction agent.

Designed against the 10 real Adani O2C-GCC sample emails. Handles the four
distinct email kinds observed:
- full_booking: bank credit table + invoice allocation table (most common)
- partial_booking: customer named but no invoice allocation (FIFO instructions)
- on_account_only: bank credit + account reference, no customer name
- non_remittance: not a payment notification (vendor invoice requests, etc.)
- needs_attachment_parsing: body lacks tables but attachments exist (deferred)

And the six payment modes observed across samples:
NEFT, RTGS, IMPS, UPI, IFT, OTHER.

PaymentIntent enum captures special instructions on remittances that
override default invoice-matching behavior (Advance Payment, Security
Deposit, On Account, FIFO instruction).
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


class PaymentIntent(str, Enum):
    """Special instructions about how the payment should be applied.

    Detected from remarks in the email body. When present, these override
    or modify the default invoice-matching behavior. The matching agent
    (Project 2) reads this field to decide which application workflow runs:

    - INVOICE_PAYMENT (default): apply against open invoices normally
    - ADVANCE: customer paying ahead; do not match to existing invoices,
      park as advance receipt
    - SECURITY_DEPOSIT: refundable deposit, separate ledger from AR
    - ON_ACCOUNT: apply to customer account, defer invoice selection
    - FIFO_INSTRUCTION: apply to oldest invoices first
    - OTHER_SPECIAL: remark detected but doesn't fit known categories;
      HITL review recommended
    """
    INVOICE_PAYMENT = "invoice_payment"      # default — normal AR application
    ADVANCE = "advance"                       # advance / advance payment
    SECURITY_DEPOSIT = "security_deposit"     # security deposit
    ON_ACCOUNT = "on_account"                 # on account / on a/c
    FIFO_INSTRUCTION = "fifo_instruction"     # 'Please book on FIFO basis'
    OTHER_SPECIAL = "other_special"           # remark exists but doesn't match known

class ExtractionStatus(str, Enum):
    """Classification of email-internal reconciliation result.

    Describes whether the email's own arithmetic is internally consistent.
    Does NOT involve open ledger or bank statement comparison — that's
    Project 2's job.

    - CLEAN: bank_credits total == allocations net total (within rounding)
    - ACCESS_PAYMENT: bank credit > allocations (customer overpaid; the
      MOANA case)
    - ALLOCATION_EXCEEDS_PAYMENT: bank credit < allocations (claims more
      than was paid; unusual)
    - ROUNDING_DIFF: small discrepancy attributable to source-data rounding
      (the MPSEZ 30-paise case)
    - NOT_APPLICABLE: email has bank credit but no allocations
      (partial_booking, on_account_only)
    - NOT_REMITTANCE: not a payment notification; reconciliation skipped
    - DEFERRED: needs_attachment_parsing; reconciliation pending
    """
    CLEAN = "clean"
    ACCESS_PAYMENT = "access_payment"
    ALLOCATION_EXCEEDS_PAYMENT = "allocation_exceeds_payment"
    ROUNDING_DIFF = "rounding_diff"
    NOT_APPLICABLE = "not_applicable"
    NOT_REMITTANCE = "not_remittance"
    DEFERRED = "deferred"


class RoutingDecision(str, Enum):
    """Routing band for the extraction.

    - AUTO_APPLY: confidence >= 0.95; downstream may apply automatically
    - HITL_REVIEW: 0.70 <= confidence < 0.95; needs human review
    - EXCEPTION: confidence < 0.70; route to exception queue

    Mirrors the same enum used in Week 3's bank importer for consistency
    across the cash app pipeline.
    """
    AUTO_APPLY = "auto_apply"
    HITL_REVIEW = "hitl_review"
    EXCEPTION = "exception"

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
    - needs_attachment_parsing: deferred; agent waits for attachment processing

    Payment intent (advance, on_account, etc.) is detected separately and
    can override default invoice-matching behavior in the downstream
    matching agent.
    """
    # Email metadata (echoed from input for audit trail)
    message_id: str
    received_at: Optional[datetime] = None
    sender_email: Optional[str] = None
    subject: Optional[str] = None

    # Triage result
    email_kind: EmailKind

    # Payment intent — detected from remarks
    payment_intent: PaymentIntent = Field(
        default=PaymentIntent.INVOICE_PAYMENT,
        description=(
            "Special instruction overriding default invoice-matching behavior. "
            "When not INVOICE_PAYMENT, downstream matching agent applies the "
            "appropriate alternative workflow (advance receipt, security deposit "
            "ledger, on-account application, FIFO matching, or HITL review)."
        ),
    )
    intent_remarks_raw: Optional[str] = Field(
        default=None,
        description=(
            "Raw remark text as it appeared in the email, preserved for audit. "
            "E.g., 'Advance Payment for January', 'Please book on FIFO basis', "
            "'On A/C - 4000000321'. Approximately 60 characters of context "
            "around the matched phrase."
        ),
    )

    # NEW: extraction status (email-internal reconciliation classification)
    extraction_status: ExtractionStatus = Field(
        default=ExtractionStatus.NOT_APPLICABLE,
        description=(
            "Classification of email-internal reconciliation. Does NOT "
            "represent open-ledger matching (that's Project 2's job)."
        ),
    )

    # NEW: routing decision based on confidence
    routing_decision: RoutingDecision = Field(
        default=RoutingDecision.HITL_REVIEW,
        description=(
            "Where this extraction should go next. Driven by confidence "
            "score and matches Week 3's bank importer enum for consistency."
        ),
    )

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
            "Non-zero indicates 'access payment' (overpayment) or partial "
            "allocation. Not applicable when payment_intent != INVOICE_PAYMENT."
        ),
    )

    # Reasoning
    confidence: float = Field(..., ge=0.0, le=1.0)
    extraction_notes: Optional[str] = None

    @field_validator("sender_email", "subject", "customer_reference",
                     "customer_name", "extraction_notes", "intent_remarks_raw",
                     mode="before")
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