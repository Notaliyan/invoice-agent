"""FastAPI surface.

Two ways in: upload a PDF at /process, or post raw text at /process/text (handy
for tests and for pasting a document without making a PDF first). Both run the
same agent and return the same shape, and both store the run in the database.

/analytics answers questions about everything stored so far.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app import analytics, db
from app.agent.runner import AgentError, AgentResult, ExtractionAgent
from app.config import get_settings
from app.observability import flush as flush_langfuse
from app.pdf import PdfDocument, PdfExtractionError, extract_pdf
from app.schemas import ProcessResponse

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.database_url:
        try:
            db.get_engine(settings.database_url)  # creates the tables
        except Exception:
            logger.exception("database unavailable; runs will not be stored")
    yield
    # Langfuse exports in the background; don't drop the tail on shutdown.
    flush_langfuse(settings)


app = FastAPI(
    title="Document Intelligence Agent",
    version="0.4.0",
    description=(
        "Upload a document; a tool-calling agent reads it, validates the "
        "arithmetic, and returns structured JSON."
    ),
    lifespan=lifespan,
)


class TextRequest(BaseModel):
    text: str = Field(min_length=1)
    filename: str = "pasted.txt"


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "model": settings.openai_model,
        "openai_key_configured": bool(settings.openai_api_key),
        "langfuse_tracing": settings.langfuse_enabled,
        "storage": bool(settings.database_url),
    }


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _store_success(
    doc: PdfDocument, result: AgentResult, duration_ms: int
) -> tuple[str | None, int | None]:
    """Persist a finished run. Returns (document id, run id), or (None, None)
    when storage is off or failed: losing the row must not lose the answer the
    caller already paid a model for."""
    if not settings.database_url:
        return None, None
    try:
        with db.session_scope(settings.database_url) as session:
            run = db.record_success(
                session,
                doc,
                result.extraction,
                trace=result.trace,
                validation_passed=result.validation.passed,
                duration_ms=duration_ms,
            )
            return run.document.public_id, run.id
    except Exception:
        logger.exception("could not store the run for %s", doc.filename)
        return None, None


def _store_failure(doc: PdfDocument, exc: AgentError, duration_ms: int) -> None:
    if not settings.database_url:
        return
    try:
        with db.session_scope(settings.database_url) as session:
            db.record_failure(
                session,
                doc,
                error=str(exc),
                model=settings.openai_model,
                trace=exc.trace,
                duration_ms=duration_ms,
            )
    except Exception:
        logger.exception("could not store the failed run for %s", doc.filename)


def _run(doc: PdfDocument, started: float) -> ProcessResponse:
    agent = ExtractionAgent(settings=settings)
    try:
        result = agent.run(doc)
    except AgentError as exc:
        _store_failure(doc, exc, _elapsed_ms(started))
        raise

    duration_ms = _elapsed_ms(started)
    document_id, run_id = _store_success(doc, result, duration_ms)
    return ProcessResponse(
        document_id=document_id or uuid.uuid4().hex,
        run_id=run_id,
        filename=doc.filename,
        pages=doc.page_count,
        extraction=result.extraction,
        validation=result.validation,
        trace=result.trace,
        duration_ms=duration_ms,
    )


@app.post("/process", response_model=ProcessResponse)
async def process(file: UploadFile = File(...)) -> ProcessResponse:
    started = time.perf_counter()
    data = await file.read()

    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File is {len(data)} bytes; limit is {settings.max_upload_bytes}.",
        )

    filename = file.filename or "upload.pdf"
    try:
        doc = await run_in_threadpool(
            extract_pdf, data, filename, settings.max_pdf_pages
        )
    except PdfExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info("processing %s: %s pages, %s chars", filename, doc.page_count, doc.char_count)
    return await run_in_threadpool(_run, doc, started)


@app.post("/process/text", response_model=ProcessResponse)
async def process_text(request: TextRequest) -> ProcessResponse:
    started = time.perf_counter()
    doc = PdfDocument.from_text(request.text, filename=request.filename)
    return await run_in_threadpool(_run, doc, started)


def _analytics_session():
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="Storage is off: DATABASE_URL is empty.")
    return db.session_scope(settings.database_url)


def _query_payload(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    query = analytics.QUERIES[name]
    return {"description": query.description, "sql": query.sql, "rows": rows}


@app.get("/analytics")
def analytics_all(
    limit: int = Query(
        analytics.DEFAULT_LIMIT, ge=1, le=500, description="Row cap for ranked queries."
    ),
) -> dict[str, Any]:
    """Every analytics query, with the SQL that produced each result."""
    with _analytics_session() as session:
        return {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "tables": analytics.table_counts(session),
            "queries": {
                name: _query_payload(name, rows)
                for name, rows in analytics.run_all(session, limit=limit).items()
            },
        }


@app.get("/analytics/{name}")
def analytics_one(
    name: str,
    limit: int = Query(analytics.DEFAULT_LIMIT, ge=1, le=500),
) -> dict[str, Any]:
    """One analytics query by name."""
    if name not in analytics.QUERIES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown query {name!r}. Available: {sorted(analytics.QUERIES)}",
        )
    with _analytics_session() as session:
        rows = analytics.run_query(session, name, limit=limit)
        return {"name": name, **_query_payload(name, rows)}


# A stand-in for Slack. The n8n workflow announces each result by POSTing
# {"text": "..."} to NOTIFY_WEBHOOK_URL; pointed here, the whole stack can be
# demonstrated without an outside account. Held in memory, newest first, and
# gone on restart: this is a demo inbox, not a message store.
_notifications: deque[dict[str, Any]] = deque(maxlen=50)


@app.post("/notifications", status_code=202)
def receive_notification(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept a notification, in the shape a Slack incoming webhook takes."""
    _notifications.appendleft(
        {"received_at": datetime.now(UTC).isoformat(timespec="seconds"), "payload": payload}
    )
    logger.info("notification: %s", payload.get("text", payload))
    return {"received": True, "held": len(_notifications)}


@app.get("/notifications")
def list_notifications() -> dict[str, Any]:
    """The most recent notifications, newest first."""
    return {"notifications": list(_notifications)}


@app.exception_handler(AgentError)
async def agent_error_handler(_, exc: AgentError) -> JSONResponse:
    """Surface the trace on failure - knowing where the agent gave up is the
    difference between a debuggable system and a black box."""
    logger.warning("agent failed: %s", exc)
    return JSONResponse(
        status_code=422,
        content={
            "error": "extraction_failed",
            "detail": str(exc),
            "trace": exc.trace.model_dump() if exc.trace else None,
        },
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    """A one-file upload page, so the deliverable is demoable without curl."""
    return """<!doctype html>
<meta charset="utf-8">
<title>Document Intelligence Agent</title>
<style>
  body { font: 15px/1.5 system-ui, sans-serif; max-width: 860px; margin: 3rem auto;
         padding: 0 1rem; color: #1a1a1a; }
  h1 { font-size: 1.4rem; }
  form { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; }
  button { padding: .5rem 1rem; font: inherit; cursor: pointer; }
  pre { background: #0f1115; color: #e6e6e6; padding: 1rem; border-radius: 8px;
        overflow: auto; max-height: 32rem; }
  .muted { color: #666; }
</style>
<h1>Document Intelligence Agent</h1>
<p class="muted">Upload an invoice, receipt, or purchase order (PDF).
Also available: <a href="/docs">/docs</a></p>
<form id="f">
  <input type="file" name="file" accept="application/pdf" required>
  <button type="submit">Process</button>
</form>
<pre id="out">Waiting for a document...</pre>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const out = document.getElementById('out');
  out.textContent = 'Processing... (the agent makes several model calls)';
  const res = await fetch('/process', { method: 'POST', body: new FormData(e.target) });
  out.textContent = JSON.stringify(await res.json(), null, 2);
});
</script>
"""
