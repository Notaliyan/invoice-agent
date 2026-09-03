# Document Intelligence Agent

Upload a business document (invoice, receipt, purchase order). A tool-calling
agent reads it, checks its own arithmetic in Python, and returns validated
structured JSON.

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

## API

### `POST /process`

`multipart/form-data` with a `file` field. Returns:

```jsonc
{
  "document_id": "1f0c...",
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

### `GET /health`

Liveness plus whether an API key is configured.

### Errors

| Status | When |
|---|---|
| 400 | Empty upload |
| 413 | Over `MAX_UPLOAD_BYTES` |
| 422 | Not a PDF, no extractable text (scanned image), over the page limit, or the agent ran out of steps — the response includes the partial `trace` so you can see where it gave up |

---

## Configuration

All via `.env` (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | — | Required at request time, not at import time. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Cheap, and the validation loop covers for it. Bump to `gpt-4.1` or `gpt-4o` for messy multi-page documents. |
| `AGENT_MAX_STEPS` | `12` | Model round-trips before giving up. |
| `AGENT_TEMPERATURE` | `0.0` | Extraction should be deterministic. |
| `MAX_UPLOAD_BYTES` | `10485760` | 10 MB. |
| `MAX_PDF_PAGES` | `25` | |
| `PAGE_PREVIEW_CHARS` | `2500` | How much of page 1 the model sees before it must use tools. |

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
    prompts.py        system prompt and nudges
tests/                65 tests, all offline (the model is scripted)
samples/              PDF generator: two clean invoices, one with broken math
```

## Testing approach

Every test runs without an API key. `tests/conftest.py` has a `FakeOpenAI` that
plays back a scripted sequence of tool calls, so the agent tests assert on
behaviour rather than on model output: that tool results are fed back into the
next request, that a rejected submission causes another turn, that an unknown
tool does not crash the run, that exhausting the step budget raises with a
usable trace.

---

## Roadmap

- [x] **Phase 1** — Core agent: PDF upload, tool-calling, validated structured output
- [ ] **Phase 2** — SQL persistence + queryable analytics
- [ ] **Phase 3** — Langfuse tracing and eval pipeline
- [ ] **Phase 4** — n8n webhook trigger
- [ ] **Phase 5** — Dashboard
- [ ] **Phase 6** — Docker Compose + GitHub Actions CI
