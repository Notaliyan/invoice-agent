"""Pydantic contracts for everything that crosses a boundary.

`ExtractionResult` is the important one: it is both the API response shape and
the thing the agent's `submit_extraction` tool call is validated against, so a
hallucinated field or a bad date fails loudly instead of leaking to the caller.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

DocumentType = Literal["invoice", "receipt", "purchase_order", "contract", "other"]


class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float = Field(description="Line total, quantity * unit_price where both are known.")


class Party(BaseModel):
    name: str
    address: str | None = None
    tax_id: str | None = None
    email: str | None = None


class ExtractionResult(BaseModel):
    document_type: DocumentType
    vendor: Party
    customer: Party | None = None

    invoice_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None

    currency: str = Field(description="ISO 4217 code, e.g. EUR.")
    line_items: list[LineItem] = Field(default_factory=list)

    subtotal: float | None = None
    tax_amount: float | None = None
    tax_rate: float | None = Field(default=None, description="Percent, e.g. 19.0 for 19%.")
    total: float

    confidence: float = Field(ge=0.0, le=1.0)
    notes: str | None = None

    @field_validator("currency")
    @classmethod
    def _iso_4217(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError(
                f"currency must be a 3-letter ISO 4217 code (got {v!r}); "
                "convert symbols yourself, e.g. '$' -> 'USD'"
            )
        return v

    @field_validator("tax_rate")
    @classmethod
    def _rate_is_percent(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 <= v <= 100.0:
            raise ValueError(f"tax_rate is a percentage between 0 and 100 (got {v})")
        return v


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class ValidationReport(BaseModel):
    passed: bool
    checks: list[ValidationCheck] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    step: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    result_summary: str
    duration_ms: int


class AgentTrace(BaseModel):
    """What the agent actually did. The same steps are mirrored to Langfuse
    when tracing is configured; `langfuse_trace_id` links the two."""

    model: str
    prompt_version: str = ""
    langfuse_trace_id: str | None = None
    steps: int
    stop_reason: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ProcessResponse(BaseModel):
    # Stable per document: uploading the same file again returns the same id.
    document_id: str
    # Row in processing_runs; null when storage is off or the write failed.
    run_id: int | None = None
    filename: str
    pages: int
    extraction: ExtractionResult
    validation: ValidationReport
    trace: AgentTrace
    duration_ms: int


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
