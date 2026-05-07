# Engineering FINDINGS — I2C Remittance Extraction Agent

A working notebook of lessons captured while building this agent. Organized
by theme rather than by day — most lessons recurred across multiple days
in different forms.

---

## Architecture

### Why each project boundary exists

The full I2C cash app system spans three projects:

1. **Project 1 (this repo):** Remittance Extraction — emails → structured RemittanceExtraction
2. **Project 2 (planned):** Matching Agent — RemittanceExtraction + bank statements + open ledger → match decisions (FULLY_MATCHED, SHORT_PAYMENT, etc.)
3. **Project 3 (planned):** Orchestrator + GL posting + exception triage

The separation reflects production cash app patterns. Each project has different
data sources, different cadence (event-driven vs batch), and different failure
modes. Mixing them into one mega-agent would balloon timeline and produce
code that's harder to reason about.

### Why some stages use rules and others use LLMs

Five-stage pipeline:
- **Triage (Day 1):** rule-based. Table-finder helpers + simple boolean logic.
- **Bank credit extraction (Day 2):** LLM, per-row. Narratives vary too much for regex.
- **Allocation extraction (Day 3):** LLM, batched per-table. Column-name reasoning is per-table work.
- **Reconciliation (Day 4):** rule-based. Pure arithmetic + classification.
- **Resolution (Day 5):** rule-based. Pure DB lookups.
- **Confidence + routing (Day 4):** rule-based. Structured field weights.

The LLM earns its place where rules fail (narrative parsing variability,
column-name interpretation). It doesn't earn its place where rules work
(triage with clean boolean signals, arithmetic, DB lookups, weighted scoring).

This is the principle: deterministic where possible, LLM where it earns its place.

### Source abstraction (Protocol pattern)

The email source is an abstraction with two implementations:
- `FileBasedEmailSource` for development and reproducible evals
- `APIBasedEmailSource` for production (Microsoft Graph passthrough)

Same Protocol interface, different backends. Agent code uses whichever is
configured via `EMAIL_SOURCE` env var. This pattern enables clean dev/prod
parity — same code, different sources.

---

## Prompt Engineering

### Real data exposes prompt weaknesses

Initial prompts that look complete in isolation often have invisible gaps
that only real data reveals. The first-pass prompt covered NEFT excellently
because every NEFT example was familiar. IFT was a single sentence with no
example — and it failed in production.

Generalizable principle: every payment mode / format / domain term in a
prompt should have at least one concrete example with annotated fields.
"Documentation by example" is more reliable than "documentation by
description" for LLM consumption, just as it is for humans.

### Per-row vs batched LLM calls

The right scope depends on where the per-call reasoning lives:
- Narrative parsing: per-row work (each narrative is independent) → per-row calls
- Column-name reasoning: per-table work (figure out columns once, apply to N rows) → batched calls

Doing column reasoning per-row would force the LLM to re-figure-out the
column meanings for every row — wasteful and prone to inconsistency. Doing
narrative parsing batched would tangle independent reasoning into one
prompt.

Match the call scope to the reasoning scope.

### Hypothetical complexity is a bug source

The Day 2 column-finder bug came from "defensive" code:
`_find_header_index(headers, ["particulars", "chq no & bank gl"])`.

The fallback was built for a hypothetical case that didn't exist in the
real data. Real data is consistent: narrative is always in 'Particulars'.
The defensive fallback caused a real bug because it picked the wrong column
when both candidates matched.

Lesson: write code for the domain you actually have, not the domain you
imagine you might have. Real data drives the schema; ask the domain expert
when in doubt.

---

## Debugging Methodology

### Diagnose input before tweaking prompt

When LLM extraction produces wrong output, the instinct is to tune the prompt.
But the input might be wrong. Always print what's being fed to the LLM as
the first diagnostic step before tweaking the prompt.

The IFT format debugging episode (Day 2): three rounds of prompt iteration
failed before realizing the LLM was being fed `'11528551'` (a cheque number)
instead of the actual narrative `'CB0106096477 IFT/CB0106096477/...'`.

Rule of thumb: if LLM output looks broken, print the input first.

### Silent failures are debugging poison

Day 2's `try/except: return {}` swallowed all LLM call errors silently.
The DNS resolution failure showed up as "every row has None for all fields"
— a uniform failure pattern that took diagnosis to trace.

Fix: rewrote `_parse_narrative_with_llm` to return `{"_error": "..."}` on
failures and added `[WARN]` print to stderr. Future failures are visible
immediately rather than appearing as mysterious empty fields.

Generalizable: make errors visible at the first observable surface (logs,
return value, raised exception). Even a one-line `[WARN]` saves hours of
mystery.

### Multi-run reveals what single-run hides

LLM agents have a distribution of behavior, not a mode. Single-run captures
the mode. Multi-run captures the distribution. Production agents need to
be evaluated on the distribution.

Example: MOANA case passed single-run consistently but failed 2/5 multi-run
because the LLM occasionally included summary footer rows as allocations.
Single-run gave false confidence; multi-run revealed the real reliability.

Production targets are multi-run with N/M threshold (e.g., 5/5 strict, or
4/5 with tolerance). Single-run testing is for development, not validation.

### "First match" semantics are subtle

Helper functions that take a list of "candidates" or "alternatives" need
to be explicit about whether they prioritize candidate order or position
order. Both are reasonable; mixing them causes subtle bugs.

The Day 2 `_find_header_index` bug: intended priority order
`["particulars", "chq no & bank gl"]`, implementation iterated headers
first and candidates second, so the FIRST header matching ANY candidate won.

Fix: invert the loop order. Document the behavior on the function. Write
a test that asserts priority order is honored.

---

## Eval Design

### Real data drives eval values, not assumptions

Initial evals had `gross_amount: "790038.00"` for SAURASHTRA based on
assumption that gross == bank credit. Real data showed gross was `863190.00`
with TDS deduction of `73152.00` netting to `790038.00`. The model was
extracting correctly; the eval was projecting wrong values.

Lesson: ground truth must come from actual data inspection, not from
projecting assumed values. When LLM extraction differs from eval expectation,
investigate the source data BEFORE assuming the model is wrong.

### Eval strictness should match downstream stakes

Triage evals are coarse (4 enum values).
Bank credit evals are medium (existence + exact amounts).
Allocation evals are strict (exact decimals, sign preservation, null/None).
Reconciliation evals assert exact diff in Decimal.

Calibrate the bar to what wrong answers actually cost. Wrong amounts at
extraction time = wrong matches at the next stage = wrong GL postings.
The strictness is justified by what's downstream.

### "Absent column means null, not zero"

Domain-critical distinction that's invisible from data shape alone. Schema,
prompt, and evals all need to encode it explicitly. Easy to get wrong
without thinking carefully about what each value MEANS in context.

---

## Confidence & Routing

### Confidence formulas need real-data calibration

Initial formula gave partial credit (0.20) for "no allocations expected"
and 0.7 for NOT_APPLICABLE reconciliation. Both double-counted against
patterns that legitimately don't have allocations. After running against
10 real samples, the distribution was wrong.

Bumped to full credit (0.30) and 1.0 respectively. After re-tuning,
distribution became operationally sensible.

Lesson: confidence formulas need calibration against real distributions,
not hypothetical scenarios. Initial weights are defensible in isolation;
running against real samples reveals double-counting and calibration drift.
Real-data calibration is a tuning step, not a tuning failure.

### Threshold boundaries need operational tolerance

Initial AUTO_APPLY threshold of 0.95 caused well-formed cases scoring
exactly 0.95 to route to HITL instead of auto_apply. Lowered to 0.94.
The operational meaning of "high confidence" doesn't materially change;
the simpler fix avoids floating-point boundary cases.

### Multi-signal routing reinforces decisions

MPSEZ has TWO independent signals routing it to HITL:
- extraction_status=rounding_diff (paise-level discrepancy)
- invoice_resolved=False (invoice not in master)

Both signals point to the same email via different mechanisms. This is
the production pattern: multiple corroborating signals reinforce routing
decisions rather than relying on any single check.

---

## Domain Patterns

### Forward-chain table duplication

Real Outlook emails preserve quoted tables in forward chains. Body often
contains the bank-credit table TWICE — the authoritative one written by
the analyst, plus a duplicate from the original quoted email.

Fix: each table-finder returns the FIRST matching table. The authoritative
table is at the top of the body; quoted forwards come after; "first
matching" naturally selects the right one.

### Footer rows are validation oracles

The MOANA email's footer rows include:
- Total: 181,374.50 (sum of allocations)
- Payment Received: 182,094.30 (bank credit)
- Access Payment: 719.80 (overpayment)

The analyst writes this for human readers. For the agent, it's a built-in
validation oracle: our computed reconciliation_diff (719.80) should match
the email's "Access Payment" value exactly. Free correctness check.

Domain experts encode validation oracles in their data even without
meaning to.

### Six column-name conventions for the same logical fields

Across just 6 full_booking samples, customer-ID column appears as:
Customer / Cust.No / Co Code / Customer Code / Code / Company Code.

Hardcoded mapping would have worked for today's 6 templates. Tomorrow's
7th template (with header "Cust ID" and "Amt Due") would have required
code changes. The LLM handles unseen variants by reasoning about meaning.

This is exactly where LLM earns its place over rules.

### Production-grade lookup APIs use composite keys

`get_invoice_by_number` requires both customer_number AND invoice_number.
Invoice numbers can collide across customers (different customers' invoice
192400005397 are different invoices), so requiring the customer number
prevents wrong-row returns.

Lesson: production-grade lookup APIs often require more parameters than
convenience-friendly single-arg functions. Composite keys are common.

---

## Tooling

### Self-contained projects beat shared libraries early on

Day 5 copied Week 3's `sql_client.py` and `db.py` rather than importing.
Each project stays self-contained as a portfolio repo. Schema duplication
is accepted because the duplicated schemas describe DB rows, not domain
logic.

Trade-off: schema drift is possible if Week 3 evolves and Project 1's
copy doesn't update. Acceptable for portfolio repos because each one
needs to be independently runnable.

In production setup, these schemas would live in a shared package.
Different optimization point.

### Function signature discovery > assumption

Three back-to-back signature mismatches when integrating Week 3's data
layer:
1. SqlClient (class) vs query_sql (function) — wrong import name
2. lookup_* vs get_* — wrong function name family
3. get_invoice_by_number(invoice_number) vs (customer_number, invoice_number) — wrong arg count

Each cost 30+ minutes of debugging. Fix in each case was the same: check
the actual signature before writing the consumer.

Internalized: 30 seconds of `grep "^def" db.py` would have prevented each
one. Assumed APIs are worse than discovered APIs.

---

## Streamlit UI

### Cache the agent output, not the agent

Page load latency dominates UX perception; LLM calls in the request path
destroy that. Day 6 caches extractions to JSON and reads from JSON in the
UI. Refresh button regenerates the cache.

Trade-off: UI shows snapshot data, not live extractions. Acceptable
because (a) extractions are deterministic across runs at this point,
(b) demos benefit from speed.

### File-backed action persistence beats in-memory state

Even a single JSON file (`actions.json`) makes the UI feel real rather
than a toy. The UI doesn't need a database; it needs to remember.

### Three-view structure for any review-and-decide UI

inbox → detail → summary. List + drill-in + aggregate. Standard shape
for any review-and-decide interface. Worth memorizing for future agent UIs.

---

## What this agent is NOT

This is the **remittance extraction** agent. It produces structured
RemittanceExtraction objects from emails. It does NOT match against the
open ledger or bank statements — that's Project 2's deliverable
(FULLY_MATCHED, SHORT_PAYMENT, OVER_PAYMENT, INCORRECT_INVOICE
classifications).

The boundary is deliberate. Project 1's job ends when the structured
extraction is produced and validated. Project 2's job begins by consuming
that extraction and comparing it against the open ledger and bank
statements to produce match decisions.