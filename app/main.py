"""FastAPI surface.

Two ways in: upload a PDF at /process, or post raw text at /process/text (handy
for tests and for pasting a document without making a PDF first). Both run the
same agent and return the same shape.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.agent.runner import AgentError, ExtractionAgent
from app.config import get_settings
from app.pdf import PdfDocument, PdfExtractionError, extract_pdf
from app.schemas import ProcessResponse

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger("app")

app = FastAPI(
    title="Document Intelligence Agent",
    version="0.1.0",
    description=(
        "Upload a document; a tool-calling agent reads it, validates the "
        "arithmetic, and returns structured JSON."
    ),
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
    }


def _run(doc: PdfDocument, started: float) -> ProcessResponse:
    agent = ExtractionAgent(settings=settings)
    result = agent.run(doc)
    return ProcessResponse(
        document_id=uuid.uuid4().hex,
        filename=doc.filename,
        pages=doc.page_count,
        extraction=result.extraction,
        validation=result.validation,
        trace=result.trace,
        duration_ms=int((time.perf_counter() - started) * 1000),
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
