#	Filename keyword	email_kind	bank_credits	invoice_alloc	has_tds	has_neg	customer_id_format	narrative_format	notes
1	KNK	full_booking	1	2	yes	no	short (761)	NEFT slash	"WAGES" suffix in narrative; clean 1-1-N case
2	MOANA	full_booking	1	5	yes	yes (AB, SA)	short (16736)	NEFT slash	Has total + access payment 719.80; mix of RV/AB/SA doc types
3	MPSEZ UTILITIES	full_booking	1	1	yes	no	long (8000000497)	IFT (cheque/transfer, not NEFT)	Different table headers: "Co Code/Chq No & Bank GL Particulars/Collection Amt"; embedded forward chain has the same data
4	LPG line crossing	non_remittance	0	0	no	no	n/a	n/a	Vendor invoice request, not a remittance; hasAttachments: true (attachments not embedded in JSON, would need separate API call)
5	On A/C 4000000321	on_account_only	1	0	no	no	long (4000000321) — appears as "On A/C" reference	NEFT slash	"On A/C" indicates customer known but no invoice mapping yet
6	VINAYAK	partial_booking	1	0	no	no	short (17884)	UPI	Customer named, FIFO instruction, no invoice list — falls between full_booking and on_account_only
7	J B BODA	full_booking	1	4	no	no	long (4000000420)	NEFT slash	No TDS column at all — different template variant
8	SAURASHTRA	full_booking	1	1	yes	no	short (5357)	non-standard ("SAURASHTRA FREI/ 790038")	Bank narrative is truncated/non-standard format; column header has typo "text Amount"
9	YES BANK	full_booking	3	3	no	no	long (12624)	NEFT slash	N Bank → 1 Remit → N Invoices case; doc_type=DZ (posted payments); 3 credits sum ≈ 3 invoices sum
10	INDUS TOWERS	on_account_only	1	0	no	no	short (13139)	NEFT slash	"On A/C – 13139"; same shape as #05
