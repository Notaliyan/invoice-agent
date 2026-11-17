"""Writing agent results into the database.

One function per outcome. Both take a session and leave the commit to the
caller, so a run and everything it produced land in a single transaction.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, Invoice, LineItem, ProcessingRun
from app.pdf import PdfDocument
from app.schemas import AgentTrace, ExtractionResult


@dataclass(frozen=True)
class EvalScores:
    """Scores from the golden-dataset eval, stored on the run they describe."""

    run_name: str
    field_accuracy: float
    extraction_completeness: float
    hallucination_rate: float


def content_hash(doc: PdfDocument) -> str:
    return hashlib.sha256("\f".join(doc.pages).encode("utf-8")).hexdigest()


def get_or_create_document(session: Session, doc: PdfDocument) -> Document:
    digest = content_hash(doc)
    existing = session.scalar(select(Document).where(Document.content_hash == digest))
    if existing is not None:
        return existing
    document = Document(
        public_id=uuid.uuid4().hex,
        content_hash=digest,
        filename=doc.filename,
        page_count=doc.page_count,
        char_count=doc.char_count,
    )
    session.add(document)
    session.flush()
    return document


def _run(
    document: Document,
    *,
    status: str,
    trace: AgentTrace | None,
    model: str,
    duration_ms: int | None,
    source: str,
    scores: EvalScores | None,
    started_at: datetime | None,
) -> ProcessingRun:
    run = ProcessingRun(
        document=document,
        source=source,
        status=status,
        model=trace.model if trace else model,
        prompt_version=trace.prompt_version if trace else "",
        stop_reason=trace.stop_reason if trace else None,
        steps=trace.steps if trace else 0,
        tool_calls=len(trace.tool_calls) if trace else 0,
        prompt_tokens=trace.prompt_tokens if trace else 0,
        completion_tokens=trace.completion_tokens if trace else 0,
        langfuse_trace_id=trace.langfuse_trace_id if trace else None,
        duration_ms=duration_ms,
    )
    if started_at is not None:
        run.started_at = started_at
    if scores is not None:
        run.eval_run_name = scores.run_name
        run.field_accuracy = scores.field_accuracy
        run.extraction_completeness = scores.extraction_completeness
        run.hallucination_rate = scores.hallucination_rate
    return run


def record_success(
    session: Session,
    doc: PdfDocument,
    extraction: ExtractionResult,
    *,
    trace: AgentTrace,
    validation_passed: bool | None,
    duration_ms: int | None,
    source: str = "api",
    scores: EvalScores | None = None,
    started_at: datetime | None = None,
) -> ProcessingRun:
    """Store a finished extraction: the run, the invoice and its line items."""
    document = get_or_create_document(session, doc)
    run = _run(
        document,
        status="succeeded",
        trace=trace,
        model=trace.model,
        duration_ms=duration_ms,
        source=source,
        scores=scores,
        started_at=started_at,
    )
    run.validation_passed = validation_passed
    run.confidence = extraction.confidence
    run.invoice = Invoice(
        document=document,
        document_type=extraction.document_type,
        vendor_name=extraction.vendor.name,
        vendor_tax_id=extraction.vendor.tax_id,
        vendor_address=extraction.vendor.address,
        customer_name=extraction.customer.name if extraction.customer else None,
        invoice_number=extraction.invoice_number,
        issue_date=extraction.issue_date,
        due_date=extraction.due_date,
        currency=extraction.currency,
        subtotal=extraction.subtotal,
        tax_amount=extraction.tax_amount,
        tax_rate=extraction.tax_rate,
        total=extraction.total,
        notes=extraction.notes,
        line_items=[
            LineItem(
                position=position,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=item.amount,
            )
            for position, item in enumerate(extraction.line_items, start=1)
        ],
    )
    session.add(run)
    session.flush()
    return run


def record_failure(
    session: Session,
    doc: PdfDocument,
    *,
    error: str,
    model: str,
    trace: AgentTrace | None = None,
    duration_ms: int | None = None,
    source: str = "api",
    scores: EvalScores | None = None,
    started_at: datetime | None = None,
) -> ProcessingRun:
    """Store a run that produced no extraction. Failures are data too: without
    them the success rate is always 100%."""
    document = get_or_create_document(session, doc)
    run = _run(
        document,
        status="failed",
        trace=trace,
        model=model,
        duration_ms=duration_ms,
        source=source,
        scores=scores,
        started_at=started_at,
    )
    run.error = error[:2000]
    session.add(run)
    session.flush()
    return run
