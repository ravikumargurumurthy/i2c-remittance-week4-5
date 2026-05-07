# I2C Remittance Extraction Agent

An LLM-based agent that converts unstructured payment notification emails into
structured RemittanceExtraction objects for downstream cash application matching.

Built for [Adani Ports & SEZ](https://www.adaniports.com) O2C-GCC team. Agent
processes real production emails via Microsoft Graph passthrough API, extracts
structured remittance data across six payment modes and six column-naming
conventions, validates against master tables, and routes to a Streamlit HITL
interface.

## At a glance

- **5 email kinds** (full_booking, partial_booking, on_account_only, non_remittance, needs_attachment_parsing)
- **6 payment modes** (NEFT, RTGS, IMPS, UPI, IFT, OTHER)
- **6 column-naming conventions** for invoice allocations
- **5 payment intent types** (advance, security_deposit, on_account, fifo_instruction, invoice_payment)
- **3 routing bands** (auto_apply, hitl_review, exception)
- **Multi-run stable** (50/50 across 10 real samples × 5 runs)
- **Real production data** (not synthetic toys)
- **Master data resolution** (customer + invoice references verified against canonical tables)

## Architecture
[Email API] ─┐
├─→ [Triage]   (rule-based, classifies email_kind + payment_intent)
│       ↓
│   [Extract Bank Credits]   (LLM, per-row, 6 payment modes)
│       ↓
│   [Extract Allocations]    (LLM, batched per-table, 6 column conventions)
│       ↓
│   [Reconcile]              (rule-based, computes diff and classifies)
│       ↓
│   [Assemble]               (combines all signals into RemittanceExtraction)
│       ↓
│   [Resolve]                (rule-based, validates against master tables)
│       ↓
│   [RemittanceExtraction] ──→ [Project 2: Matching Agent (planned)]
│
[SQL Gateway]┘

## Screenshots

### Inbox view
![Inbox](docs/screenshot_inbox.png)

All extractions sorted by routing band. HITL queue surfaces the cases needing
human review (rounding discrepancies, overpayments, weak narrative metadata,
deferred attachment processing).

### Detail view
![Detail](docs/screenshot_detail_moana.png)

Side-by-side view of extraction data and email body. Shows the agent's full
reasoning — triage classification, bank credits, allocations, reconciliation
status, master resolution, and confidence breakdown.

### Summary view
![Summary](docs/screenshot_summary.png)

Aggregate metrics across the extraction queue.

## Quick start

```bash
git clone https://github.com/<your-username>/i2c-remittance-week4-5.git
cd i2c-remittance-week4-5

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env  # fill in API keys (Azure OpenAI; SQL/Email APIs are dev-only)
```

### Run the public eval suite (synthetic data, no API keys needed except Azure)

```bash
EMAIL_SAMPLES_DIR=data/sample_emails_PUBLIC pytest test_public_eval.py -v -s
```

Expected: 8/8 passing on synthetic samples.

### Run the agent against synthetic samples

```bash
EMAIL_SAMPLES_DIR=data/sample_emails_PUBLIC python agent.py
```

### Launch the HITL UI

```bash
EMAIL_SAMPLES_DIR=data/sample_emails_PUBLIC python cache_extractions.py
streamlit run app.py
# Open http://localhost:8501
```

## Repository structure
schemas.py                  Pydantic models for the entire pipeline
email_source.py             Email source abstraction (file + API)
html_tools.py               Deterministic HTML parsing helpers
triage.py                   Rule-based email kind classification
extract_bank_credit.py      LLM-based bank credit extraction
extract_allocation.py       LLM-based allocation extraction (batched)
reconcile.py                Email-internal reconciliation
confidence.py               Rule-based confidence + routing
assemble.py                 Combines stages into final RemittanceExtraction
resolve.py                  Master data resolution against canonical tables
sql_client.py / db.py       SQL gateway client (from Week 3 bank importer)
agent.py                    LangGraph state machine wiring all stages
cache_extractions.py        Pre-computes extractions for the UI
app.py                      Streamlit HITL interface
eval_data.py                Real-data eval cases (uses gitignored samples)
eval_data_public.py         Public eval cases (uses synthetic samples)
test_agent.py               Pytest harness for real evals
test_public_eval.py         Pytest harness for public evals
data/sample_emails_REAL/    Real client emails (gitignored)
data/sample_emails_PUBLIC/  Synthetic samples (committed)
docs/screenshot_*.png       UI screenshots for README
INTEGRATION.md              System integration design
FINDINGS.md                 Engineering lessons captured during development

## Engineering principles

This codebase reflects three principles I think matter for production agents:

### Deterministic where possible, LLM where it earns its place

Triage, reconciliation, confidence scoring, and master resolution are all
rule-based. Only narrative parsing (Day 2) and column-name reasoning
(Day 3) use LLM calls. This isn't because the LLM CAN'T do triage; it's
because rules handle it more reliably and faster, and the LLM adds value
elsewhere where rules genuinely fail.

### Real data drives schema, not hypothetical defenses

Initial code had a "defensive" fallback for finding the narrative column
(`["particulars", "chq no & bank gl"]`). The fallback was for a hypothetical
case that didn't exist in real data, and it caused a real bug when the
fallback hit before the primary. After fixing, the code is simpler and
correct: narrative is always in `Particulars`. Real data drove the schema.

### Multi-run stability is the production target

Single-run pytest gives false confidence on LLM agents. Production targets
must be multi-run. The current pipeline maintains 50/50 stability across
10 cases × 5 runs.

## What this agent does NOT do

This is the **remittance extraction** agent. It does NOT match against
the open ledger or bank statements — that's Project 2's deliverable
(FULLY_MATCHED, SHORT_PAYMENT, OVER_PAYMENT classifications).

The boundary is deliberate: Project 1 produces structured input; Project 2
consumes it for matching; Project 3 handles GL posting and exception
triage.

## See also

- [INTEGRATION.md](INTEGRATION.md) — System integration design
- [FINDINGS.md](FINDINGS.md) — Engineering lessons captured during development