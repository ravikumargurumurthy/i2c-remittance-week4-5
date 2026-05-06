# eval_data.py
"""
Eval cases for Project 1 — extended for Day 2 bank credit extraction.

Day 1 assertions: email_kind, optionally detected_signals.payment_intent
Day 2 assertions: bank_credits (count, payment_mode, key fields)
"""

EVAL_SET = [
    {
        "id": "ev_01_knk_full_booking",
        "description": "KNK Ship Management — bank credit + 2-row allocation with TDS",
        "filename_keyword": "email_01_payment_booking_mahek_20251230",
        "expected": {
            "email_kind": "full_booking",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    "payment_mode": "NEFT",
                    "bank_utr": "BARBS25363480437",
                    "payer_name_contains": "KNK",
                    "amount": "18763.00",
                },
            ],
        },
    },
    {
        "id": "ev_02_moana_full_booking",
        "description": "MOANA IMPEX — 5-row allocation with mixed RV/AB/SA doc types",
        "filename_keyword": "email_02_payment_booking_mahek_20251229_1047",
        "expected": {
            "email_kind": "full_booking",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    "payment_mode": "NEFT",
                    "bank_utr": "IN42536058930570",
                    "payer_name_contains": "MOANA",
                    "amount": "182094.30",
                },
            ],
        },
    },
    {
        "id": "ev_03_mpsez_full_booking_ift",
        "description": "MPSEZ Utilities — IFT/cheque-style narrative",
        "filename_keyword": "email_03_payment_booking_mahek_20251229_0429",
        "expected": {
            "email_kind": "full_booking",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    "payment_mode": "IFT",
                    "bank_utr_is_set": True,  # exact UTR varies; just verify present
                    "amount": "122699.00",
                },
            ],
        },
    },
    {
        "id": "ev_04_lpg_needs_attachment",
        "description": "LPG line crossing — no bank table; skipped extraction",
        "filename_keyword": "email_04_lpg_line_crossing",
        "expected": {
            "email_kind": "needs_attachment_parsing",
        },
        "expected_bank_credits": {
            "count": 0,
        },
    },
    {
        "id": "ev_05_on_account_4000000321",
        "description": "On A/C – 4000000321 — bank credit only",
        "filename_keyword": "email_05_payment_booking_mahek_20251223_1444",
        "expected": {
            "email_kind": "on_account_only",
        },
        "expected_signals": {
            "payment_intent": "on_account",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    "payment_mode": "NEFT",
                    "bank_utr": "SBIN325353232476",
                    "amount": "98410.00",
                },
            ],
        },
    },
    {
        "id": "ev_06_vinayak_partial_booking",
        "description": "VINAYAK FOOD ZONE — UPI payment, FIFO instruction",
        "filename_keyword": "email_06_vinayak_ledger_mahek",
        "expected": {
            "email_kind": "partial_booking",
        },
        "expected_signals": {
            "payment_intent": "fifo_instruction",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    "payment_mode": "UPI",
                    "amount": "40000.00",
                },
            ],
        },
    },
    {
        "id": "ev_07_jboda_full_booking",
        "description": "J B BODA — 4-row allocation with NO TDS column",
        "filename_keyword": "email_07_payment_booking_mahek_20251209",
        "expected": {
            "email_kind": "full_booking",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    "payment_mode": "NEFT",
                    "bank_utr": "IOBAN25342547617",
                    "payer_name_contains": "BODA",
                    "amount": "42610.00",
                },
            ],
        },
    },
    {
        "id": "ev_08_saurashtra_full_booking",
        "description": "SAURASHTRA FREIGHT — non-standard narrative, single allocation",
        "filename_keyword": "email_08_payment_details_790038",
        "expected": {
            "email_kind": "full_booking",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    # Non-standard format — payment_mode could be NEFT or OTHER;
                    # accept either via _in
                    "payment_mode_in": ["NEFT", "OTHER"],
                    "amount": "790038.00",
                },
            ],
        },
    },
    {
        "id": "ev_09_yesbank_full_booking_n_to_n",
        "description": "YES BANK — 3 bank credits (N→N cardinality)",
        "filename_keyword": "email_09_payment_advice_apsez",
        "expected": {
            "email_kind": "full_booking",
        },
        "expected_bank_credits": {
            "count": 3,
            "rows": [
                # Each row should be NEFT, payer related to YES BANK, amount 12192
                {"payment_mode": "NEFT", "amount": "12192.00"},
                {"payment_mode": "NEFT", "amount": "12192.00"},
                {"payment_mode": "NEFT", "amount": "12192.00"},
            ],
        },
    },
    {
        "id": "ev_10_indus_on_account",
        "description": "INDUS TOWERS On A/C – 13139",
        "filename_keyword": "email_10_payment_booking_mahek_20251227",
        "expected": {
            "email_kind": "on_account_only",
        },
        "expected_signals": {
            "payment_intent": "on_account",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    "payment_mode": "NEFT",
                    "bank_utr": "HDFCH00695444377",
                    "payer_name_contains": "INDUS",
                    "amount": "7840.80",
                },
            ],
        },
    },
]