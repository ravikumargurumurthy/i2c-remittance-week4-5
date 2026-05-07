# I2C Remittance Extraction Agent

The email-extraction layer of an I2C cash application system for Adani
Ports & SEZ. Reads remittance emails from the corporate mailbox, extracts
structured `RemittanceExtraction` records consumed by the downstream
matching agent.

## Status

Project 1, Day 0 of 7. Foundation in place:

- Email source abstraction (file-based for dev, API-based for prod)
- Pydantic schemas covering 4 email kinds × 6 payment modes
- HTML table extraction tool for Outlook-generated email bodies
- Smoke test passing against 10 real sample emails

Next: Day 1 builds the triage agent (classifies email_kind only).

## Design pillars

- **Real-data driven.** Schema and tools designed against 10 actual Adani
  O2C-GCC emails covering full bookings, partial bookings, on-account
  payments, and non-remittance noise.
- **Source abstraction.** Same agent code runs against local files (dev)
  or live email API (prod). One protocol, two implementations.
- **LLM where it earns its place.** HTML parsing is deterministic
  (BeautifulSoup). LLM handles column-name variation, table-purpose
  classification, and reconciliation reasoning — tasks where rules fail.

## Quick start

```bash
git clone https://github.com/<your-username>/i2c-remittance-week4-5.git
cd i2c-remittance-week4-5

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env  # fill in API keys

# Add your real samples to the gitignored folder
mkdir -p data/sample_emails_REAL
# (copy your real emails here)

python smoke_test.py  # verifies the foundation
```

## What's in here

- `email_source.py` — Protocol-based abstraction with FileBasedEmailSource
  and APIBasedEmailSource
- `schemas.py` — RemittanceExtraction, BankCreditLine, InvoiceAllocation,
  EmailKind, PaymentMode
- `html_tools.py` — BeautifulSoup-based HTML table extraction
- `smoke_test.py` — runs all 10 samples through the foundation
- `INTEGRATION.md` — design doc covering the agent boundary
- `data/sample_emails_REAL/` — real client emails (gitignored)

## Sensitivity

The 10 sample emails contain real Adani internal data — employee names,
customer names, transaction details. They live in `data/sample_emails_REAL/`
which is gitignored. **Never commit them.** A future task: generate
synthetic equivalents preserving structure for the public eval suite.

## What's next

- Day 1 (~3 hrs): Triage agent (10/10 evals on email_kind classification)
- Day 2 (~3 hrs): Bank credit extraction (9/10 evals)
- Day 3 (~3 hrs): Invoice allocation extraction (6/10 evals)
- Day 4 (~3 hrs): Edge cases (UPI, IFT, partial bookings)
- Day 5 (~3 hrs): Wire to Week 3 data layer (customer master lookup)
- Day 6 (~3 hrs): Streamlit HITL UI
- Day 7 (~3 hrs): Polish, FINDINGS, commit


## Scope: body-first extraction

This agent extracts remittance data from email body HTML. In the 10 real
sample emails, 9 contain all data in the body and 1 (a non-remittance) has
attachments. The body-first scope covers the dominant case in our sample
data.

**Attachment handling is planned (Project 1 Day 8-9 extension), not a
Day 1-7 deliverable.** When `hasAttachments=true` AND body HTML lacks
recognizable tables, the agent routes the email as
`email_kind=needs_attachment_parsing` — a deferred classification that
honestly signals "I'd handle this if I could parse the attachment yet."

This pattern mirrors Week 3's `awaiting_remittance` band: rather than
guess incorrectly, the agent acknowledges its current limitations and
defers to a later pipeline stage.

When attachment handling is added, these emails will be re-classified
into one of the four primary kinds based on attachment content.

## Project 1 status

Days 0-4 ✅ shipped. Agent produces complete RemittanceExtraction per email.

### Pipeline architecture

[Email API] → [Triage] → [Bank Credit Extract] → [Allocation Extract]
→ [Reconcile] → [Assemble] → [RemittanceExtraction]
→ [Project 2: Matching Agent (future)]

10/10 single-run, 10/10 with 5/5 multi-run threshold.

Coverage:
- 5 email kinds
- 6 payment modes
- 6 allocation column conventions
- 5 payment intent types
- 4 reconciliation outcomes
- 3 routing bands
- N×N cardinality
- Negative amounts (credit memos)
- Absent columns (template variations)

### What this agent does NOT do

This is the **remittance extraction** agent. It produces structured
RemittanceExtraction objects from emails. It does NOT match against the
open ledger or bank statements — that's Project 2's deliverable
(FULLY_MATCHED, SHORT_PAYMENT, OVER_PAYMENT, INCORRECT_INVOICE
classifications).

## HITL UI

Day 6 added a Streamlit interface for reviewing extractions, accepting or
rejecting routing decisions, and seeing agent reasoning.

### Run

```bash
# 1. Generate the extraction cache (one-time, regenerates on demand)
python cache_extractions.py

# 2. Run the Streamlit app
streamlit run app.py

# 3. Open http://localhost:8501 in your browser
```

### Three views

- **📥 Inbox:** all extractions sorted by routing band (HITL first), with
  filter by routing decision, key fields visible at a glance, and a "View →"
  button to drill into details.

- **📄 Detail:** full RemittanceExtraction for one email — triage result,
  bank credits, allocations, reconciliation status, master resolution,
  agent reasoning notes, and accept/reject buttons. Email body rendered
  side-by-side for context.

- **📊 Summary:** aggregate stats — counts by routing band, average
  confidence per band, resolution stats, accepted/rejected/pending review.

### Workflow

For HITL_REVIEW emails, a reviewer can:
1. Open the email in the Detail view
2. See the agent's full reasoning + extracted data side-by-side with the
   raw email body
3. Add notes (optional) explaining the decision
4. Click Accept (✓) or Reject (✗)
5. Decision is persisted to `data/actions.json` (gitignored, contains
   reviewer notes)

Accepted decisions would advance to Project 2's matching agent in
production. Rejected decisions would route to a separate exception
queue for follow-up.
