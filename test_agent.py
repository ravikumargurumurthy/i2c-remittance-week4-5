# test_agent.py
"""
Pytest harness for the remittance extraction agent.

Day 1 assertions: email_kind, payment_intent (when expected)
Day 2 assertions: bank_credits count + per-row field checks
Day 3 assertions: invoice_allocations count + per-row exact values
Day 4 assertions: extraction_status, reconciliation_diff, routing, confidence

Multi-run methodology: each case runs N times via EVAL_RUNS env var.
Default PASS_THRESHOLD = EVAL_RUNS (strict).

Usage:
    pytest test_agent.py -v -s
    EVAL_RUNS=5 PASS_THRESHOLD=5 pytest test_agent.py -v -s
    EVAL_RUNS=5 PASS_THRESHOLD=4 pytest test_agent.py -v -s   # 4/5 tolerance
"""

import os
import re
from decimal import Decimal

import pytest

from agent import process_email
from email_source import get_email_source
from eval_data import EVAL_SET


# ---- Configuration ----
EVAL_RUNS = int(os.getenv("EVAL_RUNS", "1"))
PASS_THRESHOLD = int(os.getenv("PASS_THRESHOLD", str(max(1, EVAL_RUNS))))


# ============================================================
# Per-run validation
# ============================================================

def _check_one_run(case, result):
    """Validate one run against expected. Returns (passed, reason)."""
    if result.get("error"):
        return False, f"Agent raised error: {result['error']}"

    triage = result.get("triage")
    if not triage:
        return False, "Triage result missing"

    expected = case["expected"]

    # ---- Day 1: email_kind ----
    if triage.email_kind.value != expected["email_kind"]:
        return False, (
            f"email_kind: got {triage.email_kind.value!r}, "
            f"expected {expected['email_kind']!r}"
        )

    # ---- Day 1.5: payment_intent (optional) ----
    expected_signals = case.get("expected_signals", {})
    for signal_name, signal_value in expected_signals.items():
        actual = triage.detected_signals.get(signal_name)
        if actual != signal_value:
            return False, (
                f"signal {signal_name}: got {actual!r}, "
                f"expected {signal_value!r}"
            )

    # ---- Day 2: bank_credits ----
    expected_credits = case.get("expected_bank_credits", {})
    if expected_credits:
        passed, reason = _check_bank_credits(
            result["bank_credits"], expected_credits
        )
        if not passed:
            return False, reason

    # ---- Day 3: invoice_allocations ----
    expected_allocs = case.get("expected_allocations", {})
    if expected_allocs:
        passed, reason = _check_allocations(
            result["invoice_allocations"], expected_allocs
        )
        if not passed:
            return False, reason

    # ---- Day 4: assembled extraction ----
    expected_assembly = case.get("expected_assembly", {})
    if expected_assembly:
        passed, reason = _check_assembly(
            result.get("extraction"), expected_assembly
        )
        if not passed:
            return False, reason

    return True, None


# ============================================================
# Bank credit assertions (Day 2)
# ============================================================

def _check_bank_credits(actual_credits, expected: dict) -> tuple[bool, str | None]:
    """Validate bank credits against expected count + per-row checks."""
    expected_count = expected.get("count")
    if expected_count is not None and len(actual_credits) != expected_count:
        return False, (
            f"bank_credits count: got {len(actual_credits)}, "
            f"expected {expected_count}"
        )

    expected_rows = expected.get("rows", [])
    if not expected_rows:
        return True, None

    if len(actual_credits) < len(expected_rows):
        return False, (
            f"Expected {len(expected_rows)} rows of details, "
            f"got only {len(actual_credits)} bank credits"
        )

    # Greedy match: for each expected row, find a matching actual row
    unmatched = list(actual_credits)
    for expected_row in expected_rows:
        match_idx = None
        for i, actual in enumerate(unmatched):
            if _credit_row_matches(actual, expected_row):
                match_idx = i
                break

        if match_idx is None:
            return False, (
                f"No bank credit matches expected row {expected_row}. "
                f"Got: {[_credit_summary(c) for c in actual_credits]}"
            )
        unmatched.pop(match_idx)

    return True, None


def _credit_row_matches(actual, expected: dict) -> bool:
    """Check if one BankCreditLine matches an expected dict."""
    if "payment_mode" in expected:
        if not actual.payment_mode or actual.payment_mode.value != expected["payment_mode"]:
            return False
    elif "payment_mode_in" in expected:
        if not actual.payment_mode or actual.payment_mode.value not in expected["payment_mode_in"]:
            return False

    if "bank_utr" in expected:
        if actual.bank_utr != expected["bank_utr"]:
            return False
    elif expected.get("bank_utr_is_set"):
        if not actual.bank_utr:
            return False

    if "payer_name_contains" in expected:
        actual_name = (actual.payer_name_in_narrative or "").upper()
        if expected["payer_name_contains"].upper() not in actual_name:
            return False

    if "amount" in expected:
        if actual.amount != Decimal(expected["amount"]):
            return False

    return True


def _credit_summary(credit) -> str:
    return (
        f"{credit.payment_mode}/{credit.bank_utr}/"
        f"{(credit.payer_name_in_narrative or '?')[:20]}/{credit.amount}"
    )


# ============================================================
# Allocation assertions (Day 3)
# ============================================================

def _check_allocations(actual_allocs, expected: dict) -> tuple[bool, str | None]:
    """Validate invoice allocations against expected."""
    # Count check
    expected_count = expected.get("count")
    if expected_count is not None and len(actual_allocs) != expected_count:
        return False, (
            f"allocations count: got {len(actual_allocs)}, "
            f"expected {expected_count}"
        )

    # Negative-amount preservation check
    if expected.get("expect_negative_amounts"):
        has_negative = any(
            (a.gross_amount and a.gross_amount < 0)
            or (a.net_amount and a.net_amount < 0)
            for a in actual_allocs
        )
        if not has_negative:
            return False, (
                "Expected at least one allocation with negative amount "
                "(credit memo / adjustment), but found none. "
                "LLM may have stripped the sign."
            )

    # Aggregate property checks
    if expected.get("all_rows_have_doc_type_none"):
        bad = [a for a in actual_allocs if a.document_type is not None]
        if bad:
            return False, (
                f"Expected document_type None on all rows; "
                f"{len(bad)} rows had it set: "
                f"{[a.document_type for a in bad]}"
            )

    if expected.get("all_rows_have_tds_none"):
        bad = [a for a in actual_allocs if a.tds_amount is not None]
        if bad:
            return False, (
                f"Expected tds_amount None on all rows; "
                f"{len(bad)} rows had it set: "
                f"{[str(a.tds_amount) for a in bad]}"
            )

    if expected.get("all_rows_have_net_none"):
        bad = [a for a in actual_allocs if a.net_amount is not None]
        if bad:
            return False, (
                f"Expected net_amount None on all rows; "
                f"{len(bad)} rows had it set: "
                f"{[str(a.net_amount) for a in bad]}"
            )

    if expected.get("all_rows_have_customer_reference_set"):
        bad = [a for a in actual_allocs if not a.customer_reference]
        if bad:
            return False, f"Expected customer_reference on all rows; {len(bad)} missing"

    if expected.get("all_rows_have_invoice_number_set"):
        bad = [a for a in actual_allocs if not a.invoice_number]
        if bad:
            return False, f"Expected invoice_number on all rows; {len(bad)} missing"

    if expected.get("all_rows_have_doc_type_set"):
        bad = [a for a in actual_allocs if not a.document_type]
        if bad:
            return False, f"Expected document_type on all rows; {len(bad)} missing"

    # Per-row checks
    expected_rows = expected.get("rows", [])
    if not expected_rows:
        return True, None

    if len(actual_allocs) < len(expected_rows):
        return False, (
            f"Expected {len(expected_rows)} rows of details, "
            f"got only {len(actual_allocs)} allocations"
        )

    # Greedy match: for each expected row, find a matching actual row
    unmatched = list(actual_allocs)
    for expected_row in expected_rows:
        match_idx = None
        for i, actual in enumerate(unmatched):
            if _alloc_row_matches(actual, expected_row):
                match_idx = i
                break

        if match_idx is None:
            return False, (
                f"No allocation matches expected row {expected_row}. "
                f"Got: {[_alloc_summary(a) for a in actual_allocs]}"
            )
        unmatched.pop(match_idx)

    return True, None


def _alloc_row_matches(actual, expected: dict) -> bool:
    """Check if one InvoiceAllocation matches an expected dict."""
    if "customer_reference" in expected:
        if actual.customer_reference != expected["customer_reference"]:
            return False
    elif "customer_reference_pattern" in expected:
        if not actual.customer_reference:
            return False
        if not re.match(expected["customer_reference_pattern"], actual.customer_reference):
            return False

    if "invoice_number" in expected:
        if actual.invoice_number != expected["invoice_number"]:
            return False

    if "document_type" in expected:
        if actual.document_type != expected["document_type"]:
            return False
    elif expected.get("document_type_is_none"):
        if actual.document_type is not None:
            return False

    if "gross_amount" in expected:
        if actual.gross_amount != Decimal(expected["gross_amount"]):
            return False

    if "tds_amount" in expected:
        if actual.tds_amount != Decimal(expected["tds_amount"]):
            return False
    elif expected.get("tds_amount_is_none"):
        if actual.tds_amount is not None:
            return False

    if "net_amount" in expected:
        if actual.net_amount != Decimal(expected["net_amount"]):
            return False
    elif expected.get("net_amount_is_none"):
        if actual.net_amount is not None:
            return False

    return True


def _alloc_summary(alloc) -> str:
    return (
        f"{alloc.customer_reference}/{alloc.invoice_number}/"
        f"{alloc.document_type}/{alloc.gross_amount}/"
        f"tds={alloc.tds_amount}/net={alloc.net_amount}"
    )


# ============================================================
# Assembly assertions (Day 4)
# ============================================================

def _check_assembly(extraction, expected: dict) -> tuple[bool, str | None]:
    """Validate the assembled RemittanceExtraction against expected fields."""
    if not extraction:
        return False, "expected_assembly defined but result has no 'extraction'"

    # extraction_status (exact OR _in list)
    if "extraction_status" in expected:
        if extraction.extraction_status.value != expected["extraction_status"]:
            return False, (
                f"extraction_status: got {extraction.extraction_status.value!r}, "
                f"expected {expected['extraction_status']!r}"
            )
    elif "extraction_status_in" in expected:
        if extraction.extraction_status.value not in expected["extraction_status_in"]:
            return False, (
                f"extraction_status: got {extraction.extraction_status.value!r}, "
                f"expected one of {expected['extraction_status_in']!r}"
            )

    # reconciliation_diff (exact)
    if "reconciliation_diff" in expected:
        expected_diff = Decimal(expected["reconciliation_diff"])
        if extraction.reconciliation_diff != expected_diff:
            return False, (
                f"reconciliation_diff: got {extraction.reconciliation_diff}, "
                f"expected {expected_diff}"
            )

    # routing (exact OR _in list)
    if "routing" in expected:
        if extraction.routing_decision.value != expected["routing"]:
            return False, (
                f"routing: got {extraction.routing_decision.value!r}, "
                f"expected {expected['routing']!r}"
            )
    elif "routing_in" in expected:
        if extraction.routing_decision.value not in expected["routing_in"]:
            return False, (
                f"routing: got {extraction.routing_decision.value!r}, "
                f"expected one of {expected['routing_in']!r}"
            )

    # confidence_min (lower-bound check)
    if "confidence_min" in expected:
        if extraction.confidence < expected["confidence_min"]:
            return False, (
                f"confidence: got {extraction.confidence:.3f}, "
                f"expected >= {expected['confidence_min']}"
            )

    return True, None


# ============================================================
# Test harness
# ============================================================

@pytest.mark.parametrize("case", EVAL_SET, ids=[c["id"] for c in EVAL_SET])
def test_extraction(case):
    """Run one eval case EVAL_RUNS times."""
    src = get_email_source()
    mid = src.find_message_id_by_filename(case["filename_keyword"])
    assert mid is not None, (
        f"Could not find sample matching '{case['filename_keyword']}'"
    )

    runs = []
    for i in range(EVAL_RUNS):
        try:
            email = src.get_email(mid)
            result = process_email(mid, email)
            passed, reason = _check_one_run(case, result)
        except Exception as e:
            passed = False
            reason = f"raised {type(e).__name__}: {e}"
            result = {}

        runs.append({
            "run": i + 1,
            "passed": passed,
            "reason": reason,
            "kind": result.get("triage").email_kind.value if result.get("triage") else None,
            "credits_count": len(result.get("bank_credits") or []),
            "allocs_count": len(result.get("invoice_allocations") or []),
            "status": (
                result.get("extraction").extraction_status.value
                if result.get("extraction")
                else None
            ),
            "routing": (
                result.get("extraction").routing_decision.value
                if result.get("extraction")
                else None
            ),
            "confidence": (
                f"{result.get('extraction').confidence:.3f}"
                if result.get("extraction")
                else None
            ),
        })

    pass_count = sum(1 for r in runs if r["passed"])
    print(f"\n{case['id']}: {pass_count}/{EVAL_RUNS} passed")
    for r in runs:
        status = "✓" if r["passed"] else "✗"
        if r["passed"]:
            print(
                f"  {status} run {r['run']}: kind={r['kind']} "
                f"credits={r['credits_count']} allocs={r['allocs_count']} "
                f"recon_status={r['status']} routing={r['routing']} "
                f"conf={r['confidence']}"
            )
        else:
            print(f"  {status} run {r['run']}: {r['reason']}")

    assert pass_count >= PASS_THRESHOLD, (
        f"Only {pass_count}/{EVAL_RUNS} runs passed (threshold: {PASS_THRESHOLD})"
    )