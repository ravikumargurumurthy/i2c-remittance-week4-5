# test_triage.py
"""
Pytest harness for triage classification with multi-run methodology.

EVAL_RUNS controls how many times each case runs; PASS_THRESHOLD controls
how many of those runs must pass for the case to be marked passing.

Usage:
    pytest test_triage.py -v                       # 1 run per case
    EVAL_RUNS=5 PASS_THRESHOLD=4 pytest test_triage.py -v -s
                                                   # 5 runs, need 4 to pass

Note: the triage classifier is currently rule-based and fully deterministic.
Multi-run will give identical results across runs. We still use the
methodology because Day 2-3 will add LLM-based extraction layers, and
keeping the test harness consistent is valuable.
"""

import os

import pytest

from email_source import get_email_source
from eval_data import EVAL_SET
from triage import classify_email

# ---- Configuration ----
EVAL_RUNS = int(os.getenv("EVAL_RUNS", "1"))
PASS_THRESHOLD = int(os.getenv("PASS_THRESHOLD", str(max(1, EVAL_RUNS))))


def _check_one_run(case, result):
    """Validate one classification against expected. Returns (passed, reason)."""
    expected = case["expected"]
    
    # Existing email_kind check...
    if "email_kind" in expected:
        if result.email_kind.value != expected["email_kind"]:
            return False, (
                f"email_kind: got {result.email_kind.value!r}, "
                f"expected {expected['email_kind']!r}"
            )
    
    # NEW: signal-level checks
    expected_signals = case.get("expected_signals", {})
    for signal_name, signal_value in expected_signals.items():
        actual = result.detected_signals.get(signal_name)
        if actual != signal_value:
            return False, (
                f"signal {signal_name}: got {actual!r}, expected {signal_value!r}"
            )
    
    return True, None


@pytest.mark.parametrize("case", EVAL_SET, ids=[c["id"] for c in EVAL_SET])
def test_triage(case):
    """Run one eval case EVAL_RUNS times."""
    src = get_email_source()
    mid = src.find_message_id_by_filename(case["filename_keyword"])
    assert mid is not None, (
        f"Could not find sample matching '{case['filename_keyword']}'. "
        f"Check that the file is in EMAIL_SAMPLES_DIR and the keyword matches."
    )

    runs = []
    for i in range(EVAL_RUNS):
        try:
            email = src.get_email(mid)
            result = classify_email(email)
            passed, reason = _check_one_run(case, result)
        except Exception as e:
            passed = False
            reason = f"raised {type(e).__name__}: {e}"
            result = None

        runs.append({
            "run": i + 1,
            "passed": passed,
            "reason": reason,
            "kind": result.email_kind.value if result else None,
        })

    pass_count = sum(1 for r in runs if r["passed"])
    print(f"\n{case['id']}: {pass_count}/{EVAL_RUNS} passed")
    for r in runs:
        status = "✓" if r["passed"] else "✗"
        if r["passed"]:
            print(f"  {status} run {r['run']}: kind={r['kind']}")
        else:
            print(f"  {status} run {r['run']}: {r['reason']}")

    assert pass_count >= PASS_THRESHOLD, (
        f"Only {pass_count}/{EVAL_RUNS} runs passed (threshold: {PASS_THRESHOLD}). "
        f"See per-run details above."
    )
