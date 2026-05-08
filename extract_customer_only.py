# extract_customer_only.py
"""
Extract customer info from a customer-only table.

Some emails (like VINAYAK's FIFO instruction) contain a small table
listing the customer number and name without any allocation rows:

    | Customer | Customer name      |
    | 17884    | VINAYAK FOOD ZONE  |

This module parses that table and returns a CustomerOnlyEntry for each row.
Pure deterministic parsing — no LLM needed.

Used by partial_booking and on_account_only emails as an alternative
source of customer reference + name when allocations are absent.
"""

import re
from typing import Optional

from schemas import CustomerOnlyEntry


# Header keywords the table is recognized by
CUSTOMER_NUMBER_KEYWORDS = {"customer", "customer number", "customer no", "cust no"}
CUSTOMER_NAME_KEYWORDS = {"customer name", "name"}


def extract_customer_entries_from_table(table: dict) -> list[CustomerOnlyEntry]:
    """Parse a customer-only table dict into structured entries.

    Args:
        table: dict with keys 'header_row' (list[str]) and 'data_rows'
               (list[list[str]]).

    Returns:
        list of CustomerOnlyEntry, one per data row that has a non-empty
        customer number. Empty rows are skipped silently.
    """
    header_row = table.get("header_row") or []
    data_rows = table.get("data_rows") or []

    if not header_row or not data_rows:
        return []

    # Identify which column has the customer number and which has the name
    cust_num_idx = _find_column_index(header_row, CUSTOMER_NUMBER_KEYWORDS)
    cust_name_idx = _find_column_index(header_row, CUSTOMER_NAME_KEYWORDS)

    if cust_num_idx is None and cust_name_idx is None:
        # Header doesn't match expected pattern; can't parse
        return []

    # If only one column matches, fall back to positional assumption
    # (column 0 = number, column 1 = name)
    if cust_num_idx is None:
        cust_num_idx = 0
    if cust_name_idx is None:
        cust_name_idx = 1

    entries = []
    for row in data_rows:
        entry = _parse_one_row(row, cust_num_idx, cust_name_idx)
        if entry:
            entries.append(entry)

    return entries


# ============================================================
# Helpers
# ============================================================

def _find_column_index(
    header_row: list[str],
    keywords: set[str],
) -> Optional[int]:
    """Find the index of the first column whose header matches any keyword.

    Case-insensitive, trimmed comparison.
    """
    for i, header in enumerate(header_row):
        if not header:
            continue
        header_lower = header.strip().lower()
        if header_lower in keywords:
            return i
    return None


def _parse_one_row(
    row: list[str],
    cust_num_idx: int,
    cust_name_idx: int,
) -> Optional[CustomerOnlyEntry]:
    """Build a CustomerOnlyEntry from one data row, or None if invalid."""
    if cust_num_idx >= len(row):
        return None

    customer_number_raw = (row[cust_num_idx] or "").strip()
    if not customer_number_raw:
        return None

    # Validate looks like a customer number (digits, possibly with leading zeros)
    customer_number = _normalize_customer_number(customer_number_raw)
    if not customer_number:
        return None

    customer_name = None
    if cust_name_idx < len(row):
        name_raw = (row[cust_name_idx] or "").strip()
        if name_raw:
            customer_name = name_raw

    return CustomerOnlyEntry(
        customer_number=customer_number,
        customer_name=customer_name,
    )


def _normalize_customer_number(value: str) -> Optional[str]:
    """Strip whitespace; validate looks like a customer number.

    Accepts purely-digit values. Rejects empty or alphabetic strings
    (which would indicate a malformed row).
    """
    cleaned = value.strip()
    if not cleaned:
        return None

    # Customer numbers in Adani are all-digit (4 to 12 chars typically)
    if not re.fullmatch(r"\d{3,15}", cleaned):
        return None

    return cleaned
