# extract_bank_credit.py
"""
LLM-based bank credit extraction.

Given one row from a bank credit table (already located by Day 1's
find_bank_credit_table), parse the narrative into structured BankCreditLine
fields: payment_mode, bank_utr, payer_name_in_narrative, payer_bank, notes.

Why LLM here:
- Six payment modes (NEFT/RTGS/IMPS/UPI/IFT/OTHER) with varying conventions
- Slash-delimited, space-delimited, and other formats
- Truncated payer names with non-standard separators
- Some narratives have no parseable structure (email 08)

Rule-based regex would handle clean NEFT (~70% of samples). The LLM handles
the long tail by reasoning about format rather than pattern-matching.

Domain note: across all observed Adani O2C-GCC email formats, the narrative
column is consistently named 'Particulars'. Amount column varies between
'Credit Amount' (NEFT 3-col format) and 'Collection Amt' (IFT 5-col format).
Date column varies between 'Tran Date' and 'Collection Date'.
"""

import json
import os
import re
import sys
from datetime import date
from decimal import Decimal
from typing import Optional

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI

from schemas import BankCreditLine, PaymentMode

load_dotenv()


# ============================================================
# LLM client
# ============================================================

_llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
)


# ============================================================
# Prompt
# ============================================================

PROMPT = """You are parsing a single row from an Indian bank statement's credit table.

Extract structured fields from the narrative. Indian banking narratives commonly use \
slash-delimited segments, but format varies by payment mode and bank.

Common formats observed:

  NEFT format:  NEFT/<UTR>/<PAYER NAME>/<PAYER BANK>/<optional flags>
                Example: "NEFT/BARBS25363480437/KNK SHIP MANAGEMENT WAGES/BANK OF BARODA/"
                - First segment "NEFT" → payment_mode = NEFT
                - Second segment "BARBS25363480437" → bank_utr
                - Third segment "KNK SHIP MANAGEMENT WAGES" → payer_name_in_narrative
                  (note: "WAGES" is sub-categorization, capture in notes)
                - Fourth segment "BANK OF BARODA" → payer_bank

  RTGS format:  Same structure as NEFT, but first segment is "RTGS"
  IMPS format:  Same structure as NEFT, but first segment is "IMPS"

  UPI format:   UPI/P2A/<reference>/<payer>/<payer-bank-or-handle>/<purpose>
                Example: "UPI/P2A/714193529342/THACKER /UTIB/Payment/"
                - First segment "UPI" → payment_mode = UPI
                - Third segment "714193529342" → bank_utr (UPI reference)
                - Fourth segment "THACKER" → payer_name_in_narrative
                - "UTIB" is the bank handle (Axis Bank UPI handle) → payer_bank

  IFT format:   IFT/<cheque-document-id>/<account-or-reference-id>/<flag>
                Example: "CB0106096477 IFT/CB0106096477/290042000013722025/M"
                (Note: cheque number may appear once before the slash structure
                AND once inside the slash structure — they refer to the same UTR.)
                - "IFT" segment → payment_mode = IFT
                - "CB0106096477" → bank_utr (cheque-document UTR)
                - "290042000013722025" → internal account ref, capture in notes
                - "M" → flag, capture in notes
                - payer_name_in_narrative should be null for IFT — the payer
                  name is NOT in the narrative, it lives in the email body
                  context above the table

  Other:        Non-standard or truncated narratives like "SAURASHTRA FREI/ 790038"
                where the format doesn't match any of the above modes.
                - payment_mode = OTHER
                - bank_utr = null (no clear UTR present)
                - payer_name_in_narrative = "SAURASHTRA FREI" (best-effort
                  extraction of any payer-like text)

Fields to extract:

- payment_mode: One of NEFT, RTGS, IMPS, UPI, IFT, or OTHER. Look at the FIRST \
segment of the narrative (before any slash or space). If the first segment is exactly \
"NEFT", "RTGS", "IMPS", "UPI", or "IFT" (case-insensitive), use that mode — even if \
the rest of the narrative looks unusual. Use OTHER only when the first segment does \
not match any of the five known modes.

- bank_utr: The Unique Transaction Reference. For NEFT/RTGS/IMPS, typically a \
bank-prefixed alphanumeric in segment 2 (like "BARBS25363480437" or "IOBAN25342547617"). \
For IFT it might be a cheque-document number like "CB0106096477". For UPI it's the \
reference after P2A like "714193529342". If no clear UTR exists in the narrative, return null.

- payer_name_in_narrative: The name of the entity that sent the payment. Look for \
multi-word capitalized text in the middle of the narrative. Examples: "KNK SHIP MANAGEMENT", \
"MOANA IMPEX", "INDUS TOWERS LIMITED". May be truncated (e.g., "M/S J B BODA INSURANCE SURVE"). \
DO NOT confuse this with the payer's BANK (which is a separate field). For IFT format, \
this is null. If you see something that looks like a payment purpose or non-name (like \
"AAOCGAAOCG" which is a code), still return it but mention it in the notes field.

- payer_bank: The name of the bank the payment came from. Examples: "BANK OF BARODA", \
"ICICI BANK LIMITED", "HDFC BANK", "STATE BANK OF INDIA", "INDIAN OVERSEAS BANK". \
Look for words ending in "BANK" or "BNK". For UPI, this might be a 4-letter handle \
(like "UTIB" for Axis Bank, "SBIN" for SBI). If the narrative doesn't include the \
bank name explicitly, return null.

- notes: Any additional metadata in the narrative that isn't covered by the other fields. \
Common examples: "WAGES" (sub-categorization), "ATTN/INB/B" (Indian banking flags), \
"NEFT TRANSFER" (descriptive label), "spt to nov rent" (purpose), IFT internal \
account references, IFT flags. Return null if no extra metadata is present.

Return your answer as a JSON object with exactly these keys:
{
  "payment_mode": "NEFT" | "RTGS" | "IMPS" | "UPI" | "IFT" | "OTHER",
  "bank_utr": <string or null>,
  "payer_name_in_narrative": <string or null>,
  "payer_bank": <string or null>,
  "notes": <string or null>
}

Return ONLY the JSON. No explanation, no surrounding text, no markdown code blocks.
"""


# ============================================================
# Helpers — date and amount parsing (deterministic)
# ============================================================

# Indian date formats: "29.12.2025", "01.12.2025"
_DATE_PATTERN = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def parse_indian_date(value: str) -> Optional[date]:
    """Parse 'DD.MM.YYYY' format to date."""
    if not value:
        return None
    m = _DATE_PATTERN.match(value.strip())
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_amount(value: str) -> Optional[Decimal]:
    """Parse Indian-format amount like '18,763.00' or '18763' to Decimal."""
    if not value:
        return None
    cleaned = value.replace(",", "").replace("\u00a0", "").strip()
    cleaned = cleaned.replace("₹", "").replace("$", "")
    if not cleaned or cleaned in {"-", "—", "–"}:
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


# ============================================================
# Header lookup (priority order honored)
# ============================================================

def _find_header_index(headers: list[str], candidates: list[str]) -> Optional[int]:
    """Find the index of the first header containing the FIRST matching candidate.

    Candidates are checked in priority order — the function tries the first
    candidate against all headers, then the second, etc. Returns the index
    of the matching header for the highest-priority candidate found.

    Example: candidates=["particulars", "chq no & bank gl"]
        - First tries "particulars" against all headers
        - If "particulars" doesn't match any header, tries "chq no & bank gl"
        - Returns None if no candidate matches any header
    """
    for candidate in candidates:
        candidate_lower = candidate.lower()
        for i, h in enumerate(headers):
            if candidate_lower in h:
                return i
    return None


# ============================================================
# Core extraction function
# ============================================================

def extract_one_bank_credit(
    tran_date_cell: str,
    narrative_cell: str,
    amount_cell: str,
) -> Optional[BankCreditLine]:
    """Extract one BankCreditLine from a single row of the bank credit table.

    Args:
        tran_date_cell: The transaction date cell (e.g., "29.12.2025")
        narrative_cell: The narrative cell from the 'Particulars' column
        amount_cell: The credit amount cell (e.g., "18,763" or "18763.00")

    Returns:
        A populated BankCreditLine, or None if amount cannot be parsed.
        Other fields may be None if the LLM cannot extract them confidently.
    """
    # Parse the deterministic fields ourselves
    tran_date = parse_indian_date(tran_date_cell)
    amount = parse_amount(amount_cell)

    if amount is None:
        # Without an amount, we can't make a valid BankCreditLine
        return None

    # Ask the LLM to parse the narrative
    parsed = _parse_narrative_with_llm(narrative_cell)

    if "_error" in parsed:
        # Surface failures so they're not invisible
        print(
            f"[WARN] LLM extraction failed for narrative {narrative_cell[:60]!r}: "
            f"{parsed['_error']}",
            file=sys.stderr,
        )
        # Continue with what we have — at least amount is set
        parsed = {}

    return BankCreditLine(
        tran_date=tran_date,
        narrative_raw=narrative_cell,
        payment_mode=_coerce_payment_mode(parsed.get("payment_mode")),
        bank_utr=parsed.get("bank_utr"),
        payer_name_in_narrative=parsed.get("payer_name_in_narrative"),
        payer_bank=parsed.get("payer_bank"),
        notes=parsed.get("notes"),
        amount=amount,
    )


def _coerce_payment_mode(value: Optional[str]) -> Optional[PaymentMode]:
    """Coerce LLM string output to PaymentMode enum, falling back to OTHER."""
    if not value:
        return None
    try:
        return PaymentMode(value.upper())
    except ValueError:
        return PaymentMode.OTHER


def _parse_narrative_with_llm(narrative: str) -> dict:
    """Send narrative to LLM, parse JSON response, return dict.

    On error, returns a dict with '_error' key explaining what went wrong.
    Caller can inspect this for debugging without losing the failure mode.
    """
    if not narrative or not narrative.strip():
        return {"_error": "empty narrative"}

    user_message = f"Narrative to parse:\n{narrative}"
    full_prompt = f"{PROMPT}\n\n{user_message}"

    try:
        response = _llm.invoke(full_prompt)
        content = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        return {"_error": f"LLM call failed: {type(e).__name__}: {e}"}

    if not content or not content.strip():
        return {"_error": "LLM returned empty content"}

    cleaned = content.strip()
    if cleaned.startswith("```"):
        # Remove ```json or ``` opening, and trailing ```
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return {
            "_error": f"JSON parse failed: {e}. Raw content: {cleaned[:200]!r}"
        }

    if not isinstance(parsed, dict):
        return {"_error": f"LLM returned non-dict: {type(parsed)}"}

    return parsed


# ============================================================
# Batch helper — extract all rows from a bank credit table
# ============================================================

def extract_bank_credits_from_table(table: dict) -> list[BankCreditLine]:
    """Extract all bank credits from a table found by find_bank_credit_table().

    Domain knowledge encoded:
    - Narrative column is always 'Particulars' (consistent across all observed formats)
    - Amount column is 'Credit Amount' (NEFT 3-col) or 'Collection Amt' (IFT 5-col)
    - Date column is 'Tran Date' (NEFT 3-col) or 'Collection Date' (IFT 5-col)

    For 3-column tables without recognizable headers, falls back to positional
    NEFT format (date in col 0, narrative in col 1, amount in col 2).
    """
    if not table or not table.get("data_rows"):
        return []

    headers = [h.lower() for h in (table.get("header_row") or [])]
    data_rows = table["data_rows"]

    # Narrative column: always 'Particulars'. No fallback — it's domain-stable.
    narrative_idx = _find_header_index(headers, ["particulars"])

    # Amount column: differs between NEFT and IFT formats. Priority order:
    # try 'credit amount' first (most common), then 'collection amt'.
    amount_idx = _find_header_index(headers, ["credit amount", "collection amt"])

    # Date column: same pattern as amount.
    date_idx = _find_header_index(headers, ["tran date", "collection date"])

    # Positional fallback: if we found no headers but it's a 3-column table,
    # assume NEFT format. Only used when keyword-based lookup fails completely.
    col_count = table.get("col_count", 0)
    if narrative_idx is None and col_count == 3:
        date_idx = 0
        narrative_idx = 1
        amount_idx = 2

    if narrative_idx is None or amount_idx is None:
        # Cannot reliably identify the right columns
        return []

    credits = []
    for row in data_rows:
        # Defensive bounds checks
        if narrative_idx >= len(row) or amount_idx >= len(row):
            continue

        date_cell = (
            row[date_idx]
            if date_idx is not None and date_idx < len(row)
            else ""
        )
        narrative_cell = row[narrative_idx]
        amount_cell = row[amount_idx]

        credit = extract_one_bank_credit(date_cell, narrative_cell, amount_cell)
        if credit:
            credits.append(credit)

    return credits