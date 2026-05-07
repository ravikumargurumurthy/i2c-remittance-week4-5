# eval_data.py
"""
Eval cases for Project 1 — extended for Day 3 invoice allocation extraction.

Day 1 assertions: email_kind, optionally detected_signals.payment_intent
Day 2 assertions: bank_credits (count, payment_mode, key fields)
Day 3 assertions: invoice_allocations (count + per-row exact values)
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
        "expected_allocations": {
            "count": 2,
            "rows": [
                {
                    "customer_reference": "761",
                    "invoice_number": "192400005397",
                    "document_type": "RV",
                    "gross_amount": "18644.00",
                    "tds_amount": "1580.00",
                    "net_amount": "17064.00",
                },
                {
                    "customer_reference": "761",
                    "invoice_number": "192400005398",
                    "document_type": "RV",
                    "gross_amount": "1699.00",
                    "tds_amount_is_none": True,
                    "net_amount": "1699.00",
                },
            ],
        },
        "expected_assembly": {
            "extraction_status": "clean",
            "reconciliation_diff": "0.00",
            "routing": "auto_apply",
            "confidence_min": 0.95,
        },
        "expected_resolution": {
        "ran_successfully": True,
        "customer_lookup_attempted": True,
        "invoices_total_matches_allocations": True,
    },
    },
    {
        "id": "ev_02_moana_full_booking",
        "description": "MOANA IMPEX — 5-row allocation with negative amounts (AB/SA doc types)",
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
        "expected_allocations": {
        "count": 5,
        "expect_negative_amounts": True,
        "rows": [
            {
                "customer_reference": "16736",
                "invoice_number": "692400000256",
                "document_type": "AB",
                "gross_amount": "-6475.00",
                "tds_amount": "-647.50",
                "net_amount": "-5827.50",
            },
            {
                "customer_reference": "16736",
                "invoice_number": "192400004511",
                "document_type": "RV",
                "gross_amount": "72000.00",
                "tds_amount": "7200.00",
                "net_amount": "64800.00",
            },
            {
                "customer_reference": "16736",
                "invoice_number": "192400004812",
                "document_type": "RV",
                "gross_amount": "72000.00",
                "tds_amount": "7200.00",
                "net_amount": "64800.00",
            },
            {
                "customer_reference": "16736",
                "invoice_number": "192400005149",
                "document_type": "RV",
                "gross_amount": "72000.00",
                "tds_amount": "7200.00",
                "net_amount": "64800.00",
            },
            {
                "customer_reference": "16736",
                "invoice_number": "2024-2025",  # SA fiscal year reference
                "document_type": "SA",
                "gross_amount": "-7198.00",
                "tds_amount": "0.00",
                "net_amount": "-7198.00",
            },
        ],
    },
    "expected_assembly": {
            "extraction_status": "access_payment",
            "reconciliation_diff": "719.80",
            "routing": "hitl_review",
            "confidence_min": 0.85,
        },
    "expected_resolution": {
        "ran_successfully": True,
        "customer_lookup_attempted": True,
        "invoices_total_matches_allocations": True,
        },   
    },
    {
        "id": "ev_03_mpsez_full_booking_ift",
        "description": "MPSEZ Utilities — IFT format, 1-row allocation",
        "filename_keyword": "email_03_payment_booking_mahek_20251229_0429",
        "expected": {
            "email_kind": "full_booking",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    "payment_mode": "IFT",
                    "bank_utr_is_set": True,
                    "amount": "122699.00",
                },
            ],
        },
        "expected_allocations": {
            "count": 1,
            "rows": [
                {
                    "customer_reference": "8000000497",
                    "invoice_number": "192400000120",
                    "document_type": "RV",
                    "gross_amount": "136333",
                    "tds_amount": "13633.3",
                    "net_amount": "122699.7",
                },
            ],
        },
        "expected_assembly": {
            "extraction_status": "rounding_diff",
            "reconciliation_diff": "-0.70",
            "routing": "hitl_review",
            "confidence_min": 0.75,
        },
        "expected_resolution": {
        "ran_successfully": True,
        "customer_lookup_attempted": True,
        "invoices_total_matches_allocations": True,
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
        "expected_allocations": {
            "count": 0,
        },
        "expected_assembly": {
            "extraction_status": "deferred",
            "routing_in": ["hitl_review", "exception"],
            "confidence_min": 0.70,
        },
        "expected_resolution": {
            "ran_successfully": True,
            "skipped_due_to_email_kind": True,
        },
    },
    {
        "id": "ev_05_on_account_4000000321",
        "description": "On A/C – 4000000321 — bank credit only, no allocations",
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
        "expected_allocations": {
            "count": 0,
        },
        "expected_assembly": {
            "extraction_status": "not_applicable",
            "routing": "auto_apply",
            "confidence_min": 0.93,
        },
        "expected_resolution": {
        "ran_successfully": True,
        "invoices_total_matches_allocations": True,
        },   
    },
    {
        "id": "ev_06_vinayak_partial_booking",
        "description": "VINAYAK FOOD ZONE — UPI payment, FIFO instruction, no allocations",
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
        "expected_allocations": {
            "count": 0,
        },
        "expected_assembly": {
            "extraction_status": "not_applicable",
            "routing": "auto_apply",
            "confidence_min": 0.93,
        },
        "expected_resolution": {
        "ran_successfully": True,
        "invoices_total_matches_allocations": True,
        },   
    },
    {
        "id": "ev_07_jboda_full_booking",
        "description": "J B BODA — 4-row allocation with NO TDS or Net Amount columns",
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
        "expected_allocations": {
            "count": 4,
            "all_rows_have_doc_type_none": True,  # Doc Type column absent
            "all_rows_have_tds_none": True,        # TDS column absent
            "all_rows_have_net_none": True,        # Net Amount column absent
        },
        "expected_assembly": {
            "extraction_status": "clean",
            "reconciliation_diff": "0.00",
            "routing": "auto_apply",
            "confidence_min": 0.95,
        },
        "expected_resolution": {
        "ran_successfully": True,
        "customer_lookup_attempted": True,
        "invoices_total_matches_allocations": True,
        },   
    },
    {
        "id": "ev_08_saurashtra_full_booking",
        "description": "SAURASHTRA FREIGHT — non-standard narrative, 1-row allocation",
        "filename_keyword": "email_08_payment_details_790038",
        "expected": {
            "email_kind": "full_booking",
        },
        "expected_bank_credits": {
            "count": 1,
            "rows": [
                {
                    "payment_mode_in": ["NEFT", "OTHER"],
                    "amount": "790038.00",
                },
            ],
        },
        "expected_allocations": {
            "count": 1,
            "rows": [
                {
                    "customer_reference": "5357",
                    "invoice_number": "192400004327",
                    "gross_amount": "863190.00",     # ← changed
                    "tds_amount": "73152.00",         # ← added
                    "net_amount": "790038.00",        # ← added (this was the bank credit)
                },
            ],
        },
        "expected_assembly": {
            "extraction_status": "clean",
            "reconciliation_diff": "0.00",
            "routing_in": ["hitl_review", "auto_apply"],
            # SAURASHTRA confidence is 0.79 due to weak narrative;
            # tolerate either routing depending on how the formula
            # treats the OTHER payment_mode case
            "confidence_min": 0.75,
        },
        "expected_resolution": {
        "ran_successfully": True,
        "customer_lookup_attempted": True,
        "invoices_total_matches_allocations": True,
        },   
    },
    {
        "id": "ev_09_yesbank_full_booking_n_to_n",
        "description": "YES BANK — 3 bank credits + 3 invoices (N→N cardinality)",
        "filename_keyword": "email_09_payment_advice_apsez",
        "expected": {
            "email_kind": "full_booking",
        },
        "expected_bank_credits": {
            "count": 3,
            "rows": [
                {"payment_mode": "NEFT", "amount": "12192.00"},
                {"payment_mode": "NEFT", "amount": "12192.00"},
                {"payment_mode": "NEFT", "amount": "12192.00"},
            ],
        },
        "expected_allocations": {
            "count": 3,
            "all_rows_have_customer_reference_set": True,
            "all_rows_have_invoice_number_set": True,
            "all_rows_have_doc_type_set": True,  # YES BANK template has Doc Type column
        },
        "expected_assembly": {
            "extraction_status_in": ["clean", "rounding_diff"],
            # YES BANK shows -1.00 diff (allocations 36577 vs bank 36576)
            # which falls under rounding_diff for 3 rows; either is acceptable
            "routing": "auto_apply",
            "confidence_min": 0.95,
        },
        "expected_resolution": {
        "ran_successfully": True,
        "customer_lookup_attempted": True,
        "invoices_total_matches_allocations": True,
        },   
    },
    {
        "id": "ev_10_indus_on_account",
        "description": "INDUS TOWERS On A/C – 13139 — no allocations",
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
        "expected_allocations": {
            "count": 0,
        },
        "expected_assembly": {
            "extraction_status": "not_applicable",
            "routing": "auto_apply",
            "confidence_min": 0.93,
        },
        "expected_resolution": {
        "ran_successfully": True,
        "invoices_total_matches_allocations": True,
        },   
    },
]