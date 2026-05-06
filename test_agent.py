# test_agent.py
"""
Pytest harness for the remittance extraction agent.

Day 1 assertions: email_kind, payment_intent (when expected)
Day 2 assertions: bank_credits count + per-row field checks

Multi-run methodology: each case can run N times via EVAL_RUNS env var.
"""

import os
from decimal import Decimal

import pytest

from agent import process_email
from email_source import get_email_source
from eval_data import EVAL_SET


# ---- Configuration ----
EVAL_RUNS = int(os.getenv("EVAL_RUNS", "1"))
PASS_THRESHOLD = int(os.getenv("PASS_THRESHOLD", str(max(1, EVAL_RUNS))))


def _check_one_run(case, result):
    """Validate one run against expected. Returns (passed, reason)."""
    if result.get("error"):
        return False, f"Agent raised error: {result['error']}"

    triage = result.get("triage")
    if not triage:
        return False, "Triage result missing"

    expected = case["expected"]

    # ---- email_kind ----
    if triage.email_kind.value != expected["email_kind"]:
        return False, (
            f"email_kind: got {triage.email_kind.value!r}, "
            f"expected {expected['email_kind']!r}"
        )

    # ---- payment_intent (optional, only checked when expected_signals present) ----
    expected_signals = case.get("expected_signals", {})
    for signal_name, signal_value in expected_signals.items():
        actual = triage.detected_signals.get(signal_name)
        if actual != signal_value:
            return False, (
                f"signal {signal_name}: got {actual!r}, "
                f"expected {signal_value!r}"
            )

    # ---- bank_credits ----
    expected_credits = case.get("expected_bank_credits", {})
    if expected_credits:
        passed, reason = _check_bank_credits(result["bank_credits"], expected_credits)
        if not passed:
            return False, reason

    return True, None


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
        return True, None  # count check was sufficient

    if len(actual_credits) < len(expected_rows):
        return False, (
            f"Expected {len(expected_rows)} rows of details, "
            f"got only {len(actual_credits)} bank credits"
        )

    # For multi-row tables (like email 09 with 3 NEFT credits), order doesn't
    # strictly matter — check that for each expected row, AT LEAST ONE actual
    # row matches. Use a simple greedy match.
    unmatched_actual = list(actual_credits)
    for expected_row in expected_rows:
        match_idx = None
        for i, actual in enumerate(unmatched_actual):
            if _row_matches(actual, expected_row):
                match_idx = i
                break

        if match_idx is None:
            return False, (
                f"No bank credit matches expected row {expected_row}. "
                f"Got: {[_credit_summary(c) for c in actual_credits]}"
            )
        unmatched_actual.pop(match_idx)

    return True, None


def _row_matches(actual, expected: dict) -> bool:
    """Check if one actual BankCreditLine matches an expected dict."""
    # payment_mode (exact OR _in set)
    if "payment_mode" in expected:
        if not actual.payment_mode or actual.payment_mode.value != expected["payment_mode"]:
            return False
    elif "payment_mode_in" in expected:
        if not actual.payment_mode or actual.payment_mode.value not in expected["payment_mode_in"]:
            return False

    # bank_utr (exact OR existence check)
    if "bank_utr" in expected:
        if actual.bank_utr != expected["bank_utr"]:
            return False
    elif expected.get("bank_utr_is_set"):
        if not actual.bank_utr:
            return False

    # payer_name_contains (substring check)
    if "payer_name_contains" in expected:
        actual_name = (actual.payer_name_in_narrative or "").upper()
        if expected["payer_name_contains"].upper() not in actual_name:
            return False

    # amount (exact match to 2 decimal places)
    if "amount" in expected:
        expected_amount = Decimal(expected["amount"])
        if actual.amount != expected_amount:
            return False

    return True


def _credit_summary(credit) -> str:
    """Short summary of a BankCreditLine for error messages."""
    return (
        f"{credit.payment_mode}/{credit.bank_utr}/"
        f"{(credit.payer_name_in_narrative or '?')[:20]}/{credit.amount}"
    )


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
        })

    pass_count = sum(1 for r in runs if r["passed"])
    print(f"\n{case['id']}: {pass_count}/{EVAL_RUNS} passed")
    for r in runs:
        status = "✓" if r["passed"] else "✗"
        if r["passed"]:
            print(f"  {status} run {r['run']}: kind={r['kind']} credits={r['credits_count']}")
        else:
            print(f"  {status} run {r['run']}: {r['reason']}")

    assert pass_count >= PASS_THRESHOLD, (
        f"Only {pass_count}/{EVAL_RUNS} runs passed (threshold: {PASS_THRESHOLD})"
    )