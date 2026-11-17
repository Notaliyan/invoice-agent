"""The analytics queries, written as SQL.

Hand-written on purpose: the ORM stores the data, but questions about it are
asked in SQL. Each query runs unchanged on SQLite and Postgres, which is why
months and days are cut out of the ISO text form of a date rather than with
strftime() or to_char().

Most queries read from `current_invoices`, not `invoices`, because one real
invoice can have several rows there and counting them all inflates the spend:

- the same file processed again (a re-upload, or every eval run over the golden
  set) adds a row per run, so only the latest extraction per document counts;
- the same invoice arriving as two different files (a scan and the original PDF)
  is two documents, so documents that share a vendor and invoice number are
  collapsed to the latest one. Documents with no invoice number are never merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

CURRENT_INVOICES = """\
WITH latest_per_document AS (
    SELECT MAX(id) AS id
    FROM invoices
    GROUP BY document_id
),
current_invoices AS (
    SELECT i.*
    FROM invoices AS i
    JOIN (
        SELECT MAX(i2.id) AS id
        FROM invoices AS i2
        JOIN latest_per_document AS l ON l.id = i2.id
        GROUP BY CASE
            WHEN i2.invoice_number IS NOT NULL
                THEN i2.vendor_name || '#' || i2.invoice_number
            ELSE 'document:' || CAST(i2.document_id AS TEXT)
        END
    ) AS latest ON latest.id = i.id
)
"""


@dataclass(frozen=True)
class Query:
    name: str
    description: str
    sql: str


QUERIES: dict[str, Query] = {}


def _register(name: str, description: str, sql: str) -> None:
    QUERIES[name] = Query(name=name, description=description, sql=sql.strip())


_register(
    "spend_by_vendor",
    "Total and average spend per vendor. Invoices and receipts only; grouped by "
    "currency because amounts in different currencies cannot be added.",
    CURRENT_INVOICES
    + """
SELECT
    vendor_name            AS vendor,
    currency,
    COUNT(*)               AS invoices,
    SUM(total)             AS total_spend,
    AVG(total)             AS average_invoice,
    MIN(issue_date)        AS first_invoice,
    MAX(issue_date)        AS last_invoice
FROM current_invoices
WHERE document_type IN ('invoice', 'receipt')
GROUP BY vendor_name, currency
ORDER BY total_spend DESC, vendor
LIMIT :limit
""",
)

_register(
    "monthly_invoice_volume",
    "Number of documents and their value per month of issue, per currency.",
    CURRENT_INVOICES
    + """
SELECT
    substr(CAST(issue_date AS TEXT), 1, 7)  AS month,
    currency,
    COUNT(*)                                AS invoices,
    SUM(total)                              AS total_value,
    SUM(COALESCE(tax_amount, 0))            AS total_tax
FROM current_invoices
WHERE issue_date IS NOT NULL
GROUP BY substr(CAST(issue_date AS TEXT), 1, 7), currency
ORDER BY month, currency
""",
)

_register(
    "processing_performance",
    "How long the agent takes and what it costs in steps and tokens, per model "
    "and prompt version. Timing and token averages cover only the runs that "
    "recorded them; durations include time spent waiting out provider rate limits.",
    """
SELECT
    model,
    prompt_version,
    COUNT(*)                                                  AS runs,
    SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END)     AS succeeded,
    COUNT(duration_ms)                                        AS timed_runs,
    AVG(duration_ms)                                          AS avg_duration_ms,
    MIN(duration_ms)                                          AS fastest_ms,
    MAX(duration_ms)                                          AS slowest_ms,
    AVG(steps)                                                AS avg_steps,
    AVG(CASE WHEN prompt_tokens + completion_tokens > 0
             THEN prompt_tokens + completion_tokens END)      AS avg_tokens
FROM processing_runs
GROUP BY model, prompt_version
ORDER BY model, prompt_version
""",
)

_register(
    "extraction_quality_over_time",
    "Quality per day (UTC) and prompt version. Accuracy, completeness and hallucination "
    "rate come from eval runs, the only runs with a label to score against; "
    "validation pass rate and confidence cover every run.",
    """
SELECT
    substr(CAST(started_at AS TEXT), 1, 10)                         AS day,
    prompt_version,
    model,
    COUNT(*)                                                        AS runs,
    AVG(CASE WHEN status = 'succeeded' THEN 1.0 ELSE 0.0 END)       AS success_rate,
    AVG(CASE WHEN validation_passed THEN 1.0 ELSE 0.0 END)          AS validation_pass_rate,
    AVG(confidence)                                                 AS avg_confidence,
    COUNT(field_accuracy)                                           AS scored_runs,
    AVG(field_accuracy)                                             AS avg_field_accuracy,
    AVG(extraction_completeness)                                    AS avg_completeness,
    AVG(hallucination_rate)                                         AS avg_hallucination_rate
FROM processing_runs
GROUP BY substr(CAST(started_at AS TEXT), 1, 10), prompt_version, model
ORDER BY day, prompt_version, model
""",
)

_register(
    "top_line_items",
    "What the money is spent on: line items ranked by total amount billed.",
    CURRENT_INVOICES
    + """
SELECT
    li.description,
    i.currency,
    COUNT(*)                    AS times_billed,
    COUNT(DISTINCT i.vendor_name) AS vendors,
    SUM(li.quantity)            AS total_quantity,
    SUM(li.amount)              AS total_amount
FROM line_items AS li
JOIN current_invoices AS i ON i.id = li.invoice_id
GROUP BY li.description, i.currency
ORDER BY total_amount DESC, li.description
LIMIT :limit
""",
)

_register(
    "arithmetic_exceptions",
    "Documents whose own numbers disagree: the line items do not add up to the "
    "subtotal, or subtotal plus tax is not the total. A gap of zero means that "
    "check passed. These need a person to look at them.",
    CURRENT_INVOICES
    + """
SELECT
    d.filename,
    i.vendor_name                                                         AS vendor,
    i.invoice_number,
    i.currency,
    i.subtotal,
    i.tax_amount,
    i.total,
    items.line_item_sum,
    items.line_item_sum
        - COALESCE(i.subtotal, i.total - COALESCE(i.tax_amount, 0))       AS line_item_gap,
    i.subtotal + COALESCE(i.tax_amount, 0) - i.total                      AS total_gap
FROM current_invoices AS i
JOIN documents AS d ON d.id = i.document_id
LEFT JOIN (
    SELECT invoice_id, SUM(amount) AS line_item_sum
    FROM line_items
    GROUP BY invoice_id
) AS items ON items.invoice_id = i.id
WHERE ABS(items.line_item_sum
          - COALESCE(i.subtotal, i.total - COALESCE(i.tax_amount, 0))) > 0.02
   OR ABS(i.subtotal + COALESCE(i.tax_amount, 0) - i.total) > 0.02
ORDER BY d.filename
""",
)

DEFAULT_LIMIT = 20


def _plain(value: Any) -> Any:
    """Make a database value JSON-friendly. Averages come back with as many
    digits as the database feels like; four decimals is plenty."""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def run_query(session: Session, name: str, *, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Run one named query and return its rows as dicts. Raises KeyError for an
    unknown name."""
    query = QUERIES[name]
    params = {"limit": limit} if ":limit" in query.sql else {}
    result = session.execute(text(query.sql), params)
    return [{key: _plain(value) for key, value in row.items()} for row in result.mappings()]


def run_all(session: Session, *, limit: int = DEFAULT_LIMIT) -> dict[str, list[dict[str, Any]]]:
    return {name: run_query(session, name, limit=limit) for name in QUERIES}


def table_counts(session: Session) -> dict[str, int]:
    return {
        table: session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        for table in ("documents", "processing_runs", "invoices", "line_items")
    }
