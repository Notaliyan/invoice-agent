# Document Intelligence Agent

[![CI](https://github.com/Notaliyan/invoice-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Notaliyan/invoice-agent/actions/workflows/ci.yml)

Upload a business document (invoice, receipt, purchase order). A tool-calling
agent reads it, checks its own arithmetic in Python, and returns validated
structured JSON. Every run is stored in a SQL database, `/analytics` answers
questions about the stored data, and a Next.js dashboard shows both.

![The dashboard's upload page after extracting an invoice](docs/screenshots/dashboard-upload.png)

The point of this project is the *architecture*, not the extraction quality.
This is **agent → tool-calling → validated structured output**, which is a
different shape from retrieval-augmented generation: nothing is embedded,
nothing is retrieved by similarity. The model plans, calls tools, reads the
results, and corrects itself.

---

## Quick start

```bash
python -m venv .venv && .venv/Scripts/activate    # Windows
pip install -r requirements-dev.txt
cp .env.example .env                              # then paste your OPENAI_API_KEY
python samples/make_sample_invoices.py            # writes three test PDFs
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> for an upload form, or <http://127.0.0.1:8000/docs>
for the OpenAPI UI.

```bash
curl -s -F "file=@samples/clean_invoice_eur.pdf" http://127.0.0.1:8000/process
```

Run the tests (no API key or network needed — the model is faked):

```bash
pytest
```

Run the eval against the golden dataset (live — calls OpenAI, see
[Tracing and evaluation](#tracing-and-evaluation)):

```bash
python -m evals.run_eval
```

See what has been stored (see [Storage and analytics](#storage-and-analytics)):

```bash
curl -s http://127.0.0.1:8000/analytics
python -m app.analytics.report                    # pandas summary -> reports/summary.md
```

---

## How the agent works

The model never sees the whole document up front. It gets page 1 truncated to
2500 characters and a page count, then has to use tools to read the rest. That
is deliberate: it forces genuine tool use instead of one-shot pattern matching
on a document that happened to fit in the prompt.

```
PDF ──► pdfplumber ──► PdfDocument (pages, lines with page/line numbers)
                              │
                              ▼
        ┌──────────── agent loop (app/agent/runner.py) ────────────┐
        │                                                          │
        │   model ──► tool call ──► Python executes ──► result ──┐  │
        │     ▲                                                  │  │
        │     └──────────────────────────────────────────────────┘  │
        │                                                          │
        └────────────── exits when submit_extraction is accepted ───┘
                              │
                              ▼
                  ExtractionResult (Pydantic) + ValidationReport + AgentTrace
```

### The tools

| Tool | What it actually does |
|---|---|
| `get_page_text` | Returns one page in full. |
| `search_document` | String/regex search, returns page + line numbers. |
| `extract_invoice_fields` | Regex-harvests candidate amounts, dates, IDs, parties, currency. High recall, low precision — the model picks the winners. |
| `classify_document_type` | Scores the text against keyword sets and tells the model whether it agrees with its guess. |
| `validate_amounts` | Real arithmetic in Python: line-item math, sum-to-subtotal, subtotal + tax = total, tax rate vs tax amount. |
| `submit_extraction` | Terminal. Validates the payload against the Pydantic schema. |

### The design rule

**The tools do the arithmetic; the model does the judgement.**

Models are unreliable at mental arithmetic and excellent at deciding which of
five candidate strings is the vendor name. So every number is checked in Python,
and the check is not optional:

- `submit_extraction` is **rejected** unless `validate_amounts` ran first.
- It is **rejected again** if the submitted total drifts from the validated one.
- A Pydantic failure comes back as a per-field list, not a stack trace, so the
  model can fix exactly what is wrong and resubmit.

That closed loop is what makes the output trustworthy, and it is what shows up
in the trace as self-correction. `samples/broken_invoice.pdf` exists specifically
to demonstrate it — its stated total is €45.00 more than subtotal + tax, and
`validate_amounts` catches it every time.

### Some things that turned out to matter

- **Date ambiguity is not resolvable by regex.** `03/04/2024` is two different
  dates depending on the continent. The harvester returns *both* ISO readings and
  flags `ambiguous: true`; the model picks using context (a German address and a
  "MwSt" line imply day-first) and records the assumption in `notes`.
- **Whitespace is not a thousands separator.** PDF tables are space-aligned, so
  a row like `12   184,50   2.214,00` will happily parse as a single number
  `12184.50` if you allow it. Two correct numbers plus some noise beats one
  plausible-looking wrong number. There is a regression test for this.
- **A tool crash must not kill the run.** Unknown tools, malformed JSON
  arguments, and exceptions inside tools all come back to the model as ordinary
  error results so it can recover.

---

## Tracing and evaluation

"It seems to work" is not a quality bar. This part answers two questions with
numbers: *what did the agent actually do on this document?* and *did my last
prompt change make it better or worse?*

### Tracing

Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env` and every
document becomes one Langfuse trace:

```
invoice-extraction        agent        document stats in, extraction out
  llm-step-1              generation   full messages in, tool calls out, tokens, cost
  extract_invoice_fields  tool         arguments in, result out
  llm-step-2              generation
  validate_amounts        tool
  llm-step-3              generation
  submit_extraction       tool         WARNING level if it was rejected
```

Each trace is tagged with the prompt version and model, and gets three scores
that need no ground truth, so production traffic is scored too:
`extraction_succeeded`, `validation_passed` and `model_confidence`. The trace id
is returned in the API response as `trace.langfuse_trace_id`.

With the keys unset nothing is sent and the agent behaves exactly as before —
tracing sits behind one small seam (`app/observability.py`) and the loop has a
single code path either way.

### The golden dataset

`evals/golden/` holds 12 documents, each a `.txt` (what the agent sees) and a
`.json` (the correct extraction). They are chosen to break things, not to
flatter the agent:

| Case | What it probes |
|---|---|
| `01_de_eur_standard`, `02_us_usd_sales_tax`, `04_uk_gbp_vat` | Baselines: EU vs US number formats, day-first vs month-first dates, month-name dates |
| `03_de_eur_broken_total` | The document's own arithmetic is wrong — report it, don't fix it |
| `05_us_receipt` | A receipt, with cash-tendered / change-due decoys |
| `06_es_purchase_order` | Party roles on a PO; a delivery date that is not a due date |
| `07_nl_reverse_charge` | Tax explicitly zero (not missing) |
| `08_us_multipage` | Totals only on page 2 — the agent must use its reading tools |
| `09_fr_eur_tva` | Space as thousands separator (`1 260,00`) |
| `10_uk_minimal_missing_fields` | Five fields genuinely absent — the hallucination probe |
| `11_ch_chf_apostrophe` | `6'170.00`, 8.1% VAT |
| `12_us_deposit_decoys` | Deposit, balance due and a PO reference next to the real total |

The documents are synthetic and I wrote the labels alongside them. To keep
wrong labels out, `tests/test_golden_dataset.py` machine-checks every label
against its own document: it must validate against the schema, every labelled
value must literally appear in the text, and the arithmetic must pass the same
`validate_amounts` gate the agent faces. Add a real invoice by dropping in a
new `.txt` + `.json` pair; the labelling conventions are at the top of
`evals/dataset.py`.

### The metrics

All three are deterministic comparisons in `evals/scoring.py` — extraction has
exact right answers, so there is no LLM judge to drift or disagree with itself.

| Metric | Question it answers |
|---|---|
| `field_accuracy` | Of the scored fields, how many are right? (A field the document doesn't state is right only if it came back null.) |
| `extraction_completeness` | Of the fields the document *does* state, how many did the agent return anything for? Separates "left blank" from "got wrong". |
| `hallucination_rate` | Of the values returned, how many were made up — wrong **and** either absent from the document text, or filled into a field the document doesn't have? Lower is better. |

A wrong value that does appear in the document (the subtotal reported as the
total) is a misread: it costs accuracy but not hallucination. Keeping those
apart matters because the fixes differ — a misread is a prompt or tool problem,
an invention is a trust problem.

### Running it

```bash
python -m evals.run_eval                       # all 12 documents
python -m evals.run_eval --case multipage      # one case while iterating
python -m evals.run_eval --fail-on-regression  # exit 1 if worse than last run
pytest evals                                   # same run, as a pass/fail gate
```

With Langfuse configured, the run syncs the cases to a dataset called
`invoice-golden` and records an experiment run on it: one trace per document
with the three scores attached (the score comment lists every wrong field), and
`avg_*` scores on the run. Open **Datasets → invoice-golden** to compare runs
side by side.

### Tracking a prompt change

1. Edit a prompt in `app/agent/prompts.py` and bump `PROMPT_VERSION`.
2. `python -m evals.run_eval`

Runs are named `<prompt version>-<model>-<timestamp>`, so in Langfuse they line
up as `v1-…`, `v2-…` and the score columns show the difference per document.
The same numbers are appended to `evals/results/history.jsonl`, which is
committed — quality over time lives in git next to the change that caused it —
and the CLI prints the delta against the previous run:

```
field_accuracy           0.989   (-0.006 vs v2-gemini-3.5-flash-lite-20261116-233135)
extraction_completeness  1.000   (+0.000 vs v2-gemini-3.5-flash-lite-20261116-233135)
hallucination_rate       0.012   (+0.000 vs v2-gemini-3.5-flash-lite-20261116-233135)
```

A hash of the prompt text is stored with every run as well, so an edit without
a version bump still shows up as a different fingerprint.

The prompt history so far, on `gemini-3.5-flash-lite` (the full record is in
`history.jsonl`, and the version notes at the top of `prompts.py`):

| Prompt | What changed | Field accuracy | Hallucination rate |
|---|---|---|---|
| v1 | First version | 0.978 | 0.035 |
| v2 | Report what is printed, not what can be inferred | 0.994 | 0.012 |
| v3 | A document that does not add up keeps its printed figures | 0.989 | 0.012 |

v3's average is a little lower but within run-to-run noise; what it fixed is
the one failure that matters most for this agent. Under v2, a CI run replaced a
deliberately wrong printed total with subtotal + tax, because the prompt said
both "a failed check means your numbers are wrong" and "do not fix the
document". v3 removes the contradiction and keeps the printed total.

---

## Storage and analytics

Every call to `/process` is written to a database: SQLite by default (a file at
`data/invoice_agent.db`, nothing to install), or Postgres by setting
`DATABASE_URL`. `docker compose up` runs the API against a Postgres service.
Tables are created on first use.

With the stack running, the data is one command away in plain SQL:

```bash
docker compose exec db psql -U invoice -d invoice_agent -c "SELECT prompt_version, COUNT(*) AS runs, ROUND(AVG(field_accuracy)::numeric, 3) AS accuracy FROM processing_runs WHERE source = 'eval' GROUP BY prompt_version ORDER BY prompt_version"
```

### Schema

```
documents ──< processing_runs ──(0..1) invoices ──< line_items
```

| Table | One row per | Holds |
|---|---|---|
| `documents` | uploaded file, identified by a hash of its text | filename, page and character counts |
| `processing_runs` | attempt to extract a document, including failures | status, model, prompt version, steps, tokens, duration, validation result, confidence, Langfuse trace id; eval runs also carry their accuracy scores |
| `invoices` | successful run | vendor, customer, number, dates, currency, subtotal, tax, total |
| `line_items` | line on an invoice | description, quantity, unit price, amount |

Uploading the same file twice gives one document with two runs, and the API
returns the same `document_id` both times. Failed runs are stored too; without
them the success rate would always read 100%.

`invoices` holds every document type the agent handles, receipts and purchase
orders included; `document_type` tells them apart.

### The queries

Six hand-written SQL queries in [`app/analytics/queries.py`](app/analytics/queries.py).
The ORM writes the data; the questions are asked in SQL.

| Query | Answers |
|---|---|
| `spend_by_vendor` | Total and average spend per vendor and currency |
| `monthly_invoice_volume` | Documents and value per month of issue |
| `processing_performance` | Average, fastest and slowest run; steps and tokens; per model and prompt version |
| `extraction_quality_over_time` | Success rate, validation pass rate, and eval accuracy per day and prompt version |
| `top_line_items` | What the money is spent on |
| `arithmetic_exceptions` | Documents whose line items, subtotal, tax and total disagree |

Two things the queries have to get right:

- **One invoice, many rows.** Re-processing a file adds an invoice row per run,
  and the same invoice can arrive as two different files. Summing `invoices`
  directly would double the spend, so the spend queries read from a
  `current_invoices` CTE: the latest extraction per document, then one row per
  vendor and invoice number. Documents without an invoice number are never merged.
- **Currencies are not added together.** Every money query groups by currency.

Accuracy needs a known right answer, which uploads do not have. So
`extraction_quality_over_time` takes accuracy from eval runs: `python -m
evals.run_eval` stores each golden case as a processing run with its scores.
Validation pass rate and confidence are reported for every run.

### `GET /analytics`

Returns every query with its description, the SQL that ran, and the rows:

```jsonc
{
  "generated_at": "2026-11-16T23:38:00+00:00",
  "tables": { "documents": 15, "processing_runs": 39, "invoices": 39, "line_items": 119 },
  "queries": {
    "spend_by_vendor": {
      "description": "Total and average spend per vendor. ...",
      "sql": "WITH latest_per_document AS ( ... ",
      "rows": [
        { "vendor": "Polder Data B.V.", "currency": "EUR", "invoices": 1,
          "total_spend": 7260, "average_invoice": 7260.0,
          "first_invoice": "2024-08-22", "last_invoice": "2024-08-22" }
      ]
    }
    // ... five more
  }
}
```

`GET /analytics/{name}` returns one query. `?limit=` caps the ranked queries
(default 20).

### The pandas report

```bash
python -m app.analytics.report          # writes reports/summary.md
```

Reads the same tables into DataFrames and does what is awkward in SQL: a
month-by-currency pivot, each vendor's share of spend, payment-term medians,
p90 processing time, the documents the agent gets wrong most often, and
whether the model's stated confidence tracks its accuracy.

[`reports/summary.md`](reports/summary.md) is a committed example, generated
from the golden dataset and the three sample PDFs.

### Loading data

```bash
python -m evals.run_eval --import-history   # past eval runs from evals/results/
python -m evals.run_eval --concurrency 1    # a fresh run, stored with timing
```

`--no-store` runs the eval without writing to the database.

### Limits

- The same queries run on SQLite and Postgres 16; CI runs the whole test
  suite against both. No other database has been tried.
- No migrations. A change to the models means recreating the database or
  adding Alembic.
- Durations include time spent waiting out provider rate limits.

---

## Dashboard

A Next.js dashboard in [`dashboard/`](dashboard/), on the same stack as
ModulSearch: Next.js 16, React 19, TypeScript and Tailwind 4. It talks to the
API from the browser and has two pages.

```bash
docker compose up -d --build        # then open http://localhost:3000
```

### Upload

Drop a PDF on the page. The agent's steps appear one by one as they happen,
then the extracted fields: parties, dates, amounts, line items, and each
arithmetic check with its numbers.

The live steps come from `POST /process/stream`, which sends Server-Sent
Events while the agent works. The browser reads the stream with `fetch`, since
`EventSource` cannot upload a file. The fields themselves arrive together at
the end, because the agent submits them in one validated call; what is live is
the trail of tool calls leading up to it.

A document whose numbers do not add up is still extracted, and flagged:

![An invoice whose total does not match, flagged for review](docs/screenshots/dashboard-needs-review.png)

### Analytics

Charts over `GET /analytics`, so every figure on the page is the result of one
of the SQL queries above.

![The analytics page](docs/screenshots/dashboard-analytics.png)

| Chart | Form | Why |
|---|---|---|
| Headline numbers | Stat tiles | A single value is a number, not a one-bar chart |
| Processing volume | Columns per day | Counts over time, from a zero baseline |
| Extraction accuracy, hallucination rate | Two line charts | Two measures on different scales get a chart each, never two y-axes on one |
| Spend by vendor | Ranked horizontal bars | Comparing parts: bars on one baseline read more exactly than slices |
| Invoice value by month | Columns, with empty months kept | A month with no invoices is a gap, not a skipped label |
| Needs review | Table | Rows to act on, not a shape |

Choices worth knowing about:

- **One currency at a time.** A selector above the spend charts scopes both;
  amounts in different currencies are never added.
- **The accuracy axis does not start at zero**, and the chart says so. From
  zero, 97.8% and 99.4% would be the same line.
- **One colour.** Every chart plots a single series, so they share one blue,
  checked for contrast against the card background. Amber and green are kept
  for status, and always come with an icon and a word.
- **Nothing is hover-only.** Each chart has a "View as table" twin, every mark
  can be reached with the keyboard, and the readout on hover repeats what the
  table holds.
- The charts are plain SVG and HTML, with no chart library.

### Working on it

With Node 22 installed:

```bash
cd dashboard && npm install && npm run dev
```

`NEXT_PUBLIC_API_URL` is the API's address as the browser sees it (default
`http://localhost:8000`). It is compiled into the bundle, so in Docker it is a
build argument. The API only accepts browser calls from the origins in
`CORS_ORIGINS`.

### Limits

- No tests of its own beyond lint, type-check and build in CI. It was checked
  by driving it in a headless browser: an upload on each page state, hover and
  keyboard readouts, and a phone-width layout.
- No login. Anyone who can open the page can upload documents and read the
  analytics.
- Dark theme only.

---

## Workflow automation (n8n)

An [n8n](https://n8n.io) workflow puts the agent behind two automatic triggers
and routes what comes back. n8n runs self-hosted, as a second service in
`docker-compose.yml` next to the API.

```mermaid
flowchart LR
    W[Webhook<br/>POST /webhook/invoice] --> X
    F[New file in<br/>n8n/files/inbox] --> R[Read file] --> X
    X[InvoiceAgent API<br/>POST /process] --> S[Summarise result]
    X -. stores the run .-> DB[(SQL)]
    S --> Q{Needs review?}
    Q -- failed or does not add up --> N1[Notify: needs review]
    Q -- clean --> N2[Notify: processed]
    N1 --> Z[Return result]
    N2 --> Z
```

| Step | Node | What it does |
|---|---|---|
| Trigger | Webhook | Accepts a document as a multipart upload in a field named `file` |
| Trigger | Local File Trigger + Read/Write Files | Fires when a file appears in `n8n/files/inbox` and loads it |
| Call the agent | HTTP Request | Posts the document to `/process`. A 422 from the agent is kept as data, not treated as a crash |
| Store | (the API) | `/process` writes the run, invoice and line items to SQL and returns a `run_id` |
| Process the result | Edit Fields | Reduces the response to vendor, number, amount, and whether it needs review |
| Route | If | Sends failed or non-validating documents down a different branch |
| Notify | HTTP Request | Posts `{"text": "..."}` to `NOTIFY_WEBHOOK_URL` |

The workflow is in [`n8n/workflows/invoice-intake.json`](n8n/workflows/invoice-intake.json).

![The Invoice intake workflow in the n8n editor](docs/screenshots/workflow.png)

*The workflow in the n8n editor: two triggers on the left, the agent call, then
the split into "needs review" and "processed".*

### Running it

Needs [Docker Desktop](https://www.docker.com/products/docker-desktop/) and a
filled-in `.env`.

```bash
docker compose up -d --build
```

Load the workflow and publish it. Both are command-line steps, so the workflow
runs before anyone has opened the n8n editor:

```bash
docker compose exec -u node n8n n8n import:workflow --input=/workflows/invoice-intake.json
```

```bash
docker compose exec -u node n8n n8n publish:workflow --id=invoiceIntake001
```

n8n picks the published workflow up within about 15 seconds. To look at it in
the editor, open <http://localhost:5678> and create the local owner account
n8n asks for on first visit.

### What a run looks like

Trigger it by webhook. The call returns when the workflow has finished, with
the summary the workflow built:

```bash
curl -s -F "file=@samples/clean_invoice_eur.pdf" http://localhost:5678/webhook/invoice
```

```json
{"extracted":true,"needs_review":false,"filename":"clean_invoice_eur.pdf",
 "vendor":"Nordlicht Systeme GmbH","invoice_number":"NLS-2024-0417",
 "amount":"4967.06 EUR","run_id":"40","document_id":"f26bfee0...","problem":""}
```

Or drop a file into the watched folder. This sample has a total that does not
match its subtotal and tax:

```bash
cp samples/broken_invoice.pdf n8n/files/inbox/
```

A file the agent cannot read takes the same review branch:

```bash
curl -s -F "file=@README.md" http://localhost:5678/webhook/invoice
```

Then read what was announced:

```bash
curl -s http://localhost:8000/notifications
```

Those three runs produced these notifications, one per route:

```text
Processed: invoice NLS-2024-0417 from Nordlicht Systeme GmbH, 4967.06 EUR. Stored as run 40.
Needs review: broken_invoice.pdf from Halden Werkzeug GmbH, 1398.63 EUR. The numbers on the document do not add up.
Needs review: document. Extraction failed (HTTP 422): File does not look like a PDF (missing %PDF header).
```

In n8n's Executions view, the green path shows the route each document took.

A clean invoice sent to the webhook, announced as processed (42 seconds, nearly
all of it the model):

![Execution of a clean invoice, routed to Notify: processed](docs/screenshots/execution-processed.png)

An invoice whose total does not add up, dropped into the inbox folder and
flagged for review:

![Execution of a broken invoice from the folder trigger, routed to Notify: needs review](docs/screenshots/execution-needs-review.png)

The stored runs show up in the analytics like any other:

```bash
curl -s http://localhost:8000/analytics/processing_performance
```

### Notifications

The workflow announces every result by posting `{"text": "..."}` to
`NOTIFY_WEBHOOK_URL`. That is the body a Slack incoming webhook accepts, so
setting the variable in `.env` to a Slack webhook URL sends the messages to a
channel.

Left unset, it points at the API's own `GET/POST /notifications`: an
in-memory inbox holding the last 50 messages, there so the whole chain can be
shown working without an outside account. It is a demo aid, not a message store.

### Choices made in the compose file

- **Pinned n8n version.** A workflow file records the version of every node it
  uses; an unpinned image can change them underneath it.
- **The folder trigger is re-enabled.** n8n 2.0 switches off Local File Trigger
  and Execute Command by default. `NODES_EXCLUDE` turns only the first back on,
  and `N8N_RESTRICT_FILE_ACCESS_TO` limits file nodes to the mounted `/files`.
- **Polling for the folder watch.** File-change events do not cross from a
  Windows host into a Linux container, so the trigger polls.
- **Postgres in the stack.** The API container stores its runs in the `db`
  service, not in the SQLite file that `uvicorn` on the host uses, so the two
  do not share data.

### Limits

- Run on Docker Desktop for Windows with n8n 2.47.7 only.
- The webhook has no authentication. That is acceptable on localhost and not
  anywhere else; n8n's Webhook node supports header and basic auth.
- A file stays in the inbox after it is processed. Restarting n8n does not
  process it again, and the API recognises a repeated document, but nothing
  moves or deletes it.
- The webhook caller waits for the whole extraction, 10 to 60 seconds on a
  free-tier model.

---

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and
pull request, in three jobs.

| Job | What it does |
|---|---|
| **Lint and tests** | `ruff`, then the offline test suite twice: on SQLite, and on a Postgres 16 service container |
| **Dashboard lint and build** | ESLint, the TypeScript check and a production build of the Next.js dashboard |
| **Docker build and golden eval** | Builds the image, starts it with Postgres through `docker compose`, smoke-tests `/health`, `/analytics` and `/notifications`, then runs the agent on the 12 golden documents inside that image |

The eval step is the quality gate:

```bash
docker compose run --rm api python -m evals.run_eval --concurrency 1 --no-record --gate --fail-on-regression
```

| Exit code | Meaning | CI |
|---|---|---|
| 0 | Quality is at or above the bar and no worse than the last recorded run | passes |
| 1 | A threshold in [`evals/thresholds.py`](evals/thresholds.py) was missed, or a metric regressed against `evals/results/history.jsonl` | fails |
| 2 | The model provider failed (rate limit, outage, bad key), so quality could not be measured | fails |

The thresholds: field accuracy and completeness at least 0.95, hallucination
rate at most 0.03, and no single document below 0.85 accuracy or above 0.20
hallucination. The full per-document report is printed in the job log and on
the run's summary page.

So a prompt change that makes extraction worse fails CI on its branch, before
it is merged.

### Setting it up

The eval makes real model calls and needs one repository secret:
**Settings → Secrets and variables → Actions → New repository secret**, named
`OPENAI_API_KEY`. Without it, the eval step is skipped with a notice and the
rest of CI still runs.

The model settings default to the ones the committed baseline was measured
with (`gemini-3.5-flash-lite` through Gemini's OpenAI-compatible endpoint). To
change them, add repository variables `OPENAI_MODEL`, `OPENAI_BASE_URL` and
`OPENAI_MAX_RETRIES`.

### Limits

- Each run makes about 50 model calls. On a free tier with a daily cap, enough
  pushes in a day will end in exit code 2.
- The gate compares against a baseline measured on one model. Change the model
  and the baseline has to be re-recorded with `python -m evals.run_eval`.
- A model is not perfectly repeatable. The thresholds leave a margin and the
  regression check tolerates 0.02, but a rare unlucky run can still fail.

---

## API

### `POST /process`

`multipart/form-data` with a `file` field. Returns:

```jsonc
{
  "document_id": "1f0c...",   // same id every time this file is uploaded
  "run_id": 37,               // row in processing_runs; null if storage is off
  "filename": "clean_invoice_eur.pdf",
  "pages": 1,
  "extraction": {
    "document_type": "invoice",
    "vendor": { "name": "Nordlicht Systeme GmbH", "tax_id": "DE812345678" },
    "customer": { "name": "Meridian Analytics AG" },
    "invoice_number": "NLS-2024-0417",
    "issue_date": "2024-03-14",
    "due_date": "2024-04-13",
    "currency": "EUR",
    "line_items": [
      { "description": "Sensor gateway SG-40", "quantity": 12,
        "unit_price": 184.5, "amount": 2214.0 }
    ],
    "subtotal": 4174.0,
    "tax_amount": 793.06,
    "tax_rate": 19.0,
    "total": 4967.06,
    "confidence": 0.93,
    "notes": null
  },
  "validation": {
    "passed": true,
    "checks": [
      { "name": "subtotal_plus_tax_equals_total", "passed": true,
        "detail": "4174.0 + 793.06 = 4967.06, stated total is 4967.06 (difference 0.0)" }
    ]
  },
  "trace": {
    "model": "gpt-4o-mini",
    "prompt_version": "v1",
    "langfuse_trace_id": "9ee5...",   // null when tracing is off
    "steps": 4,
    "stop_reason": "submitted",
    "tool_calls": [
      { "step": 1, "tool": "extract_invoice_fields", "ok": true,
        "result_summary": "harvested {...} from 18 lines", "duration_ms": 3 }
    ],
    "prompt_tokens": 6210,
    "completion_tokens": 480
  },
  "duration_ms": 8412
}
```

### `POST /process/text`

`{"text": "...", "filename": "pasted.txt"}` — same pipeline, no PDF needed.
Useful for testing and for documents you have already extracted.

### `POST /process/stream`

The same upload as `/process`, answered as Server-Sent Events so a UI can show
progress:

| Event | Data |
|---|---|
| `started` | `{filename, pages, chars}`: the PDF was read |
| `progress` | `{type: "step", step, max_steps}` before each model call; `{type: "tool", step, tool, ok, summary, duration_ms}` after each tool call |
| `result` | The body `/process` returns |
| `error` | `{error, detail}`, and the partial `trace` when the agent gave up |

A bad upload is refused with a normal HTTP error before the stream starts.

### `GET /health`

Liveness plus whether an API key, tracing and storage are configured.

### `GET /analytics`, `GET /analytics/{name}`

SQL analytics over everything stored. See
[Storage and analytics](#storage-and-analytics).

### `POST /notifications`, `GET /notifications`

A demo inbox for the n8n workflow's notifications. See
[Notifications](#notifications).

### Errors

| Status | When |
|---|---|
| 400 | Empty upload |
| 413 | Over `MAX_UPLOAD_BYTES` |
| 404 | Unknown analytics query name |
| 503 | `/analytics` with storage turned off |
| 422 | Not a PDF, no extractable text (scanned image), over the page limit, or the agent ran out of steps — the response includes the partial `trace` so you can see where it gave up |

---

## Configuration

All via `.env` (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | — | Required at request time, not at import time. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Cheap, and the validation loop covers for it. Bump to `gpt-4.1` or `gpt-4o` for messy multi-page documents. |
| `OPENAI_BASE_URL` | empty | Empty means OpenAI. Set it to use any OpenAI-compatible provider (Gemini, Groq, a local server); `OPENAI_API_KEY` and `OPENAI_MODEL` are then that provider's key and model. |
| `OPENAI_MAX_RETRIES` | `2` | Retries on rate limits and transient errors, with backoff. Use `10` on free tiers with a per-minute request cap. |
| `AGENT_MAX_STEPS` | `12` | Model round-trips before giving up. |
| `AGENT_TEMPERATURE` | `0.0` | Extraction should be deterministic. |
| `MAX_UPLOAD_BYTES` | `10485760` | 10 MB. |
| `MAX_PDF_PAGES` | `25` | |
| `PAGE_PREVIEW_CHARS` | `2500` | How much of page 1 the model sees before it must use tools. |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Browser origins allowed to call the API: where the dashboard is served. |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | `docker compose` only: the API's address as the browser sees it, compiled into the dashboard. |
| `DATABASE_URL` | `sqlite:///./data/invoice_agent.db` | Where runs are stored. For Postgres: `postgresql+psycopg://user:pass@host/db`. Empty turns storage and `/analytics` off. `docker compose` sets it to the `db` service. |
| `POSTGRES_PASSWORD` | `invoice_dev` | `docker compose` only: password for the `db` service. |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | — | Both needed to turn tracing on. |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | EU cloud. Use `https://us.cloud.langfuse.com` or your self-hosted URL. |
| `LANGFUSE_ENVIRONMENT` | `development` | Set to `production` on the deployed service to keep its traces apart from eval runs. |

---

## Layout

```
app/
  main.py             FastAPI endpoints + the demo upload page
  config.py           pydantic-settings
  schemas.py          ExtractionResult, ValidationReport, AgentTrace
  pdf.py              pdfplumber -> PdfDocument (pages, lines, preview)
  agent/
    runner.py         the loop: ~60 lines, no framework
    tools.py          tool implementations + OpenAI tool specs
    prompts.py        system prompt and nudges, PROMPT_VERSION
  observability.py    Langfuse seam: no-op without keys
  db/
    models.py         SQLAlchemy models: documents, processing_runs, invoices, line_items
    session.py        engine + transaction scope (SQLite or Postgres)
    store.py          record_success / record_failure
  analytics/
    queries.py        the six SQL queries
    report.py         pandas summary report
evals/
  golden/             12 labelled documents (.txt + .json per case)
  dataset.py          loader + labelling conventions
  scoring.py          field accuracy, completeness, hallucination rate
  run_eval.py         runs the dataset, records to Langfuse + history.jsonl
  store.py            eval runs -> database, with their scores
  thresholds.py       the quality bar, shared by CI and `pytest evals`
  test_golden_eval.py the same run as a pytest quality gate (live)
tests/                232 tests, all offline (the model and Langfuse are faked)
samples/              PDF generator: two clean invoices, one with broken math
n8n/
  workflows/          invoice-intake.json, the n8n workflow
  files/inbox/        drop a PDF here and the workflow picks it up
dashboard/            Next.js dashboard: upload page and analytics page
  src/app/            the two pages
  src/components/     upload panel, live timeline, charts
  src/hooks/          useProcessStream: reads the SSE stream
Dockerfile            the API (and the eval runner) as a container
docker-compose.yml    api + db (Postgres) + dashboard + n8n
.github/workflows/    ci.yml: lint, tests on SQLite and Postgres, dashboard build, Docker build, golden eval
```

## Testing approach

Every test runs without an API key. `tests/conftest.py` has a `FakeOpenAI` that
plays back a scripted sequence of tool calls, so the agent tests assert on
behaviour rather than on model output: that tool results are fed back into the
next request, that a rejected submission causes another turn, that an unknown
tool does not crash the run, that exhausting the step budget raises with a
usable trace.

The eval tooling is tested the same way: the scorer has its own tests (a
subtly wrong metric makes every later run meaningless), the golden labels are
checked against their documents, and the eval runner is driven end to end by a
scripted model.

The SQL is tested against a small database with known answers, one temporary
SQLite file per test (or, with `TEST_DATABASE_URL` set, a Postgres database
that is wiped before each test; CI does both): each query has a test that states the rows it must
return, including the cases that are easy to get wrong (a re-processed
document, the same invoice in two files, a run with no recorded duration).
The API tests never touch the real database.

`pytest` stays free and offline; only `pytest evals` and
`python -m evals.run_eval` call OpenAI.

---

## Roadmap

- [x] **Phase 1** — Core agent: PDF upload, tool-calling, validated structured output
- [x] **Phase 2** — Langfuse tracing, golden dataset and eval pipeline
- [x] **Phase 3** — SQL storage, analytics queries, `/analytics` endpoint, pandas report
- [x] **Phase 4** — n8n workflow: webhook or new file → agent → route → notify
- [x] **Phase 5** — Postgres in Docker Compose, GitHub Actions CI with the golden eval as a quality gate
- [x] **Phase 6** — Next.js dashboard: live upload page and analytics charts
