"""SQLAlchemy models: what the agent extracted, and how each run went.

    documents ──< processing_runs ──(0..1) invoices ──< line_items

A document is the thing that was uploaded, identified by a hash of its text, so
uploading the same file twice gives one document with two runs. Every run is
recorded, including the ones that failed; a run that succeeded has exactly one
invoice row holding what it extracted.

"invoices" holds every document type the agent handles (receipts and purchase
orders too) - `document_type` says which.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


def _money() -> Numeric:
    # NUMERIC in the database so sums are exact in Postgres; floats in Python
    # because that is what the extraction schema speaks.
    return Numeric(14, 2, asdecimal=False)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The id handed out by the API. Stable across re-uploads of the same file.
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    page_count: Mapped[int] = mapped_column(Integer)
    char_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    runs: Mapped[list[ProcessingRun]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    invoices: Mapped[list[Invoice]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)

    # "api" for uploads, "eval" for golden-dataset runs.
    source: Mapped[str] = mapped_column(String(16), default="api", index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # succeeded | failed
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)

    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    steps: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Null for runs imported from before timing was recorded.
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    validation_passed: Mapped[bool | None] = mapped_column(Boolean)
    confidence: Mapped[float | None] = mapped_column(Float)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64))

    # Only eval runs have a label to score against, so only they have these.
    eval_run_name: Mapped[str | None] = mapped_column(String(128), index=True)
    field_accuracy: Mapped[float | None] = mapped_column(Float)
    extraction_completeness: Mapped[float | None] = mapped_column(Float)
    hallucination_rate: Mapped[float | None] = mapped_column(Float)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

    document: Mapped[Document] = relationship(back_populates="runs")
    invoice: Mapped[Invoice | None] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("processing_runs.id"), unique=True)

    document_type: Mapped[str] = mapped_column(String(32), index=True)
    vendor_name: Mapped[str] = mapped_column(String(255), index=True)
    vendor_tax_id: Mapped[str | None] = mapped_column(String(64))
    vendor_address: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(String(255))
    invoice_number: Mapped[str | None] = mapped_column(String(128))
    issue_date: Mapped[date | None] = mapped_column(Date, index=True)
    due_date: Mapped[date | None] = mapped_column(Date)

    currency: Mapped[str] = mapped_column(String(3))
    subtotal: Mapped[float | None] = mapped_column(_money())
    tax_amount: Mapped[float | None] = mapped_column(_money())
    tax_rate: Mapped[float | None] = mapped_column(Float)
    total: Mapped[float] = mapped_column(_money())
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped[Document] = relationship(back_populates="invoices")
    run: Mapped[ProcessingRun] = relationship(back_populates="invoice")
    line_items: Mapped[list[LineItem]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="LineItem.position"
    )


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[float | None] = mapped_column(Float)
    unit_price: Mapped[float | None] = mapped_column(_money())
    amount: Mapped[float] = mapped_column(_money())

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")
