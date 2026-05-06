# extract_allocation.py
"""
LLM-based invoice allocation extraction.

Given an allocation table found by find_allocation_table(), parse all rows
into structured InvoiceAllocation objects. One LLM call per table (batched)
because column-name reasoning is per-table work and shouldn't be repeated.

Why batched (vs per-row like Day 2):
- Day 2's narrative parsing was per-row work (each narrative is independent)
- Day 3's table parsing has a per-table component (figure out what each
  column means) plus a per-row component (extract each row's values)
- Doing per-row would force the LLM to re-figure-out the column meanings
  N times — wasteful and harder to keep consistent across rows

Why LLM here:
- Six observed column-name variants for the same logical fields
- Three optional columns (Doc Type, TDS, Net Amount) sometimes absent
- Negative amounts (credit memos) must preserve sign
- Some templates use "Amount" for gross, others use "Invoice Amt", others "Amt"
- Customer ID column has 6+ possible names (Customer, Cust.No, Co Code, etc.)

Rule-based mapping would handle today's 6 templates. Tomorrow's 7th template
(with header "Cust ID" and an "Amt Due" column we haven't seen) breaks the
rules. The LLM handles unknown template variants by reasoning about meaning.
"""

import json
import os
import re
import sys
from decimal import Decimal
from typing import Optional

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI

from schemas import InvoiceAllocation

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

PROMPT = """You are parsing an invoice allocation table from an Indian B2B remittance email.

IMPORTANT — skip summary/footer rows:
Some tables include summary rows at the bottom like:
- "Total" with the sum of all allocations
- "Payment Received" with the bank credit total
- "Access Payment" or "Excess Payment" with the overpayment difference
- "Adjustment" or "Balance" rows

These are NOT real allocation rows. They typically have an EMPTY customer
reference column. DO NOT include them in your output. Only return rows where
the customer reference column has a value.

If you see N rows in the table but some are summary footers, return only
the actual allocation rows. The "rows" array in your response should
contain only true allocations, not summaries.

The email's analyst lists which invoices are being settled by a bank payment. Each row \
in the table represents one invoice being paid (or one credit memo offsetting a payment). \
Your task: extract structured fields from every row.

Templates vary widely. Different emails use different column names for the same logical \
fields. Reason about what each column MEANS, not what it's literally called.

Field mapping guidance:

- customer_reference: The customer's identifier as it appears in the table. Possible \
column names include: "Customer", "Cust.No", "Cust No", "Co Code", "Customer Code", \
"Customer Code", "Code", "Company Code". The value is typically a number (3 to 10 digits) \
like "761", "16736", "4000000420", or "12624".

- customer_name: The customer's name. Possible column names: "Customer name", \
"Customer Name", "Cust.Name", "Company Name". The value is text like "KNK SHIP MANAGEMENT", \
"MOANA IMPEX", "M/S J B BODA INSURANCE BROKERS".

- invoice_number: The invoice or document reference number. Possible column names: \
"Reference", "Invoice No", "Invoice No.", "Invoice Number". The value is typically a \
12-digit number like "192400005397" or "192400005241". May also be shorter (8-10 digits).

- document_type: The SAP document type. Possible column names: "Document Type", "Doc Type". \
Common values: "RV" (regular invoice), "DZ" (posted payment), "AB" (adjustment / credit \
memo), "SA" (statement adjustment). If this column is ABSENT from the table, return null \
for every row.

- gross_amount: The invoice amount before tax deductions. Possible column names: "Amount", \
"Amt", "Invoice Amt", "Invoice Amount", "Gross Amount". Values are Indian-format \
amounts like "18,644.00" or "182,094.30". CAN BE NEGATIVE for credit memos / adjustments \
(e.g., "-1,580.00") — preserve the sign exactly. Empty cells or "-" mean null, not zero.

- tds_amount: Tax Deducted at Source. Possible column names: "TDS", "TDS Amount". If this \
column is ABSENT from the table, return null for every row. If the column exists but a \
specific cell is empty or contains "-", return null for that row (NOT zero — null and zero \
mean different things downstream). Values are Indian-format numbers, never negative.

- net_amount: The final amount being applied to this invoice (gross minus TDS, plus or \
minus any adjustments). Possible column names: "Net Amount", "Net Amt", "Net". If this \
column is ABSENT from the table, return null for every row. Like gross_amount, can be \
negative for credit memos.

CRITICAL — preserving signs and nulls:
- Negative amounts (e.g., "-1,580.00") MUST be returned as negative. Do NOT strip the sign.
- Empty cells, dashes ("-"), or whitespace mean null. Do NOT convert to zero.
- Absent columns (the column header isn't in the table at all) mean null for every row.

You will be given the table headers and a list of rows. Return a JSON array where each \
element is one row's extracted values.

Return your answer as a JSON object with one key:
{
  "rows": [
    {
      "customer_reference": "<string or null>",
      "customer_name": "<string or null>",
      "invoice_number": "<string or null>",
      "document_type": "<string or null>",
      "gross_amount": "<string with optional sign or null>",
      "tds_amount": "<string or null>",
      "net_amount": "<string with optional sign or null>"
    },
    ...
  ]
}

Return amount fields as strings (preserving signs and decimal points) — not numbers. \
This avoids any precision loss in JSON parsing. Return ONLY the JSON. No explanation, \
no surrounding text, no markdown code blocks.
"""


# ============================================================
# Helpers — amount parsing (deterministic)
# ============================================================

def parse_amount(value: Optional[str]) -> Optional[Decimal]:
    """Parse Indian-format amount like '18,644.00' or '-1,580.00' to Decimal.

    Returns None for empty, dash, or unparseable values.
    Preserves negative sign.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in {"-", "—", "–", ""}:
        return None
    # Remove commas, currency symbols, non-breaking spaces
    cleaned = s.replace(",", "").replace("\u00a0", "").strip()
    cleaned = cleaned.replace("₹", "").replace("$", "")
    try:
        return Decimal(cleaned)
    except Exception:
        return None


# ============================================================
# Core extraction function
# ============================================================

def extract_allocations_from_table(table: dict) -> list[InvoiceAllocation]:
    """Extract all invoice allocations from a table found by find_allocation_table().

    One LLM call per table (batched). The LLM reasons about column names and
    extracts every row in a single response.

    Returns empty list if extraction fails — caller should check both the
    return value and stderr warnings to understand what happened.
    """
    if not table or not table.get("data_rows"):
        return []

    headers = table.get("header_row") or []
    data_rows = table.get("data_rows") or []

    if not headers:
        # No headers means we can't reason about columns
        print(
            f"[WARN] Allocation table has no headers; cannot extract",
            file=sys.stderr,
        )
        return []

    # Build the prompt input: headers + rows as structured text
    headers_str = " | ".join(headers)
    rows_str = "\n".join(
        f"  Row {i}: {row}"
        for i, row in enumerate(data_rows)
    )

    user_message = (
        f"Table headers (in column order):\n{headers_str}\n\n"
        f"Data rows (each list shows cell values in the same column order):\n{rows_str}"
    )

    parsed = _parse_table_with_llm(user_message)

    if "_error" in parsed:
        print(
            f"[WARN] Allocation extraction failed: {parsed['_error']}",
            file=sys.stderr,
        )
        return []

    rows = parsed.get("rows", [])
    if not isinstance(rows, list):
        print(
            f"[WARN] Allocation LLM returned 'rows' as non-list: {type(rows)}",
            file=sys.stderr,
        )
        return []

    # Build InvoiceAllocation objects
    allocations = []
    for i, row_dict in enumerate(rows):
        if not isinstance(row_dict, dict):
            print(
                f"[WARN] Allocation row {i} is not a dict: {type(row_dict)}",
                file=sys.stderr,
            )
            continue

        try:
            allocation = InvoiceAllocation(
                customer_reference=row_dict.get("customer_reference"),
                customer_name=row_dict.get("customer_name"),
                invoice_number=row_dict.get("invoice_number"),
                document_type=row_dict.get("document_type"),
                gross_amount=parse_amount(row_dict.get("gross_amount")),
                tds_amount=parse_amount(row_dict.get("tds_amount")),
                net_amount=parse_amount(row_dict.get("net_amount")),
            )
            allocations.append(allocation)
        except Exception as e:
            print(
                f"[WARN] Could not build InvoiceAllocation from row {i}: "
                f"{type(e).__name__}: {e}. Row: {row_dict!r}",
                file=sys.stderr,
            )
            continue

    return allocations


def _parse_table_with_llm(user_message: str) -> dict:
    """Send table to LLM, parse JSON response, return dict.

    On error, returns {'_error': '...'} so caller can surface failures.
    """
    full_prompt = f"{PROMPT}\n\n{user_message}"

    try:
        response = _llm.invoke(full_prompt)
        content = (
            response.content
            if hasattr(response, "content")
            else str(response)
        )
    except Exception as e:
        return {"_error": f"LLM call failed: {type(e).__name__}: {e}"}

    if not content or not content.strip():
        return {"_error": "LLM returned empty content"}

    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return {
            "_error": f"JSON parse failed: {e}. Raw content: {cleaned[:300]!r}"
        }

    if not isinstance(parsed, dict):
        return {"_error": f"LLM returned non-dict: {type(parsed)}"}

    return parsed
