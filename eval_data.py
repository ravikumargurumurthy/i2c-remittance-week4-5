# eval_data.py
"""
Eval cases for Project 1 Day 1 — triage classification.

Each case asserts the EmailKind classification only. Day 2-3 evals will
extend these cases to assert on extracted bank credits and allocations.
"""

EVAL_SET = [
    {
        "id": "ev_01_knk_full_booking",
        "description": "KNK Ship Management — bank credit + 2-row allocation with TDS",
        "filename_keyword": "email_01_payment_booking_mahek_20251230",
        "expected": {
            "email_kind": "full_booking",
        },
    },
    {
        "id": "ev_02_moana_full_booking",
        "description": "MOANA IMPEX — 5-row allocation with mixed RV/AB/SA doc types and access payment",
        "filename_keyword": "email_02_payment_booking_mahek_20251229_1047",
        "expected": {
            "email_kind": "full_booking",
        },
    },
    {
        "id": "ev_03_mpsez_full_booking_ift",
        "description": "MPSEZ Utilities — IFT/cheque-style bank credit table; long customer number",
        "filename_keyword": "email_03_payment_booking_mahek_20251229_0429",
        "expected": {
            "email_kind": "full_booking",
        },
    },
    {
        "id": "ev_04_lpg_needs_attachment",
        "description": "LPG line crossing — has attachments, body is invoice-request thread",
        "filename_keyword": "email_04_lpg_line_crossing",
        "expected": {
            "email_kind": "needs_attachment_parsing",
        },
    },
    {
        "id": "ev_05_on_account_4000000321",
        "description": "On A/C – 4000000321 — bank credit only, no customer name in body",
        "filename_keyword": "email_05_payment_booking_mahek_20251223_1444",
        "expected": {
            "email_kind": "on_account_only",
        },
    },
    {
        "id": "ev_06_vinayak_partial_booking",
        "description": "VINAYAK FOOD ZONE — UPI payment, 2-col customer table, 'FIFO Basis' instruction",
        "filename_keyword": "email_06_vinayak_ledger_mahek",
        "expected": {
            "email_kind": "partial_booking",
        },
    },
    {
        "id": "ev_07_jboda_full_booking",
        "description": "J B BODA — 4-row allocation with NO TDS column variant",
        "filename_keyword": "email_07_payment_booking_mahek_20251209",
        "expected": {
            "email_kind": "full_booking",
        },
    },
    {
        "id": "ev_08_saurashtra_full_booking",
        "description": "SAURASHTRA FREIGHT — non-standard bank narrative, 1-row allocation",
        "filename_keyword": "email_08_payment_details_790038",
        "expected": {
            "email_kind": "full_booking",
        },
    },
    {
        "id": "ev_09_yesbank_full_booking_n_to_n",
        "description": "YES BANK — 3 bank credits + 3 invoices (N→N cardinality)",
        "filename_keyword": "email_09_payment_advice_apsez",
        "expected": {
            "email_kind": "full_booking",
        },
    },
    {
        "id": "ev_10_indus_on_account",
        "description": "INDUS TOWERS On A/C – 13139 — bank credit + short account ref",
        "filename_keyword": "email_10_payment_booking_mahek_20251227",
        "expected": {
            "email_kind": "on_account_only",
        },
    },
]

