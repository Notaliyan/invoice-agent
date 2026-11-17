"""A summary report built with pandas on top of the SQL data.

    python -m app.analytics.report                  # writes reports/summary.md
    python -m app.analytics.report --out other.md

The /analytics endpoint answers fixed questions in SQL. This is the other half:
pull the tables into DataFrames and reshape them - pivots, quantiles, a
correlation - the kind of thing that is awkward in SQL and one line in pandas.

pandas is only needed here; the API does not import it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from app import analytics, db
from app.analytics.queries import CURRENT_INVOICES
from app.config import get_settings

DEFAULT_OUT = Path("reports/summary.md")

INVOICES_SQL = (
    CURRENT_INVOICES
    + """
SELECT i.id, d.filename, i.document_type, i.vendor_name, i.customer_name,
       i.invoice_number, i.issue_date, i.due_date, i.currency,
       i.subtotal, i.tax_amount, i.tax_rate, i.total
FROM current_invoices AS i
JOIN documents AS d ON d.id = i.document_id
"""
)

RUNS_SQL = """
SELECT r.id, d.filename, r.source, r.status, r.model, r.prompt_version, r.steps,
       r.tool_calls, r.prompt_tokens, r.completion_tokens, r.duration_ms,
       r.validation_passed, r.confidence, r.field_accuracy,
       r.extraction_completeness, r.hallucination_rate, r.started_at
FROM processing_runs AS r
JOIN documents AS d ON d.id = r.document_id
"""


def load_frames(database_url: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The current invoices and every processing run, as DataFrames."""
    with db.get_engine(database_url).connect() as connection:
        invoices = pd.read_sql(INVOICES_SQL, connection, parse_dates=["issue_date", "due_date"])
        runs = pd.read_sql(RUNS_SQL, connection)
    # SQLite hands timestamps back with or without an offset depending on how
    # they were written; "mixed" reads both, and everything is stored as UTC.
    runs["started_at"] = pd.to_datetime(runs["started_at"], utc=True, format="mixed")
    for column in ("subtotal", "tax_amount", "total"):
        # Whole amounts come back as integers from SQLite; money is not one.
        invoices[column] = pd.to_numeric(invoices[column]).astype(float)
    return invoices, runs


def to_markdown(frame: pd.DataFrame, *, index: bool = False) -> str:
    """A small Markdown table writer, so the report needs no extra package."""
    if frame.empty:
        return "_No data._"
    if index:
        frame = frame.reset_index()

    def cell(value: object) -> str:
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return ""
        if isinstance(value, float):
            return f"{value:,.2f}"
        if isinstance(value, pd.Timestamp):
            return value.date().isoformat()
        return str(value).replace("|", "\\|")

    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    rule = "| " + " | ".join("---" for _ in frame.columns) + " |"
    rows = ["| " + " | ".join(cell(v) for v in row) + " |" for row in frame.to_numpy()]
    return "\n".join([header, rule, *rows])


def spend_by_vendor(invoices: pd.DataFrame) -> pd.DataFrame:
    billed = invoices[invoices["document_type"].isin(["invoice", "receipt"])]
    if billed.empty:
        return billed
    out = (
        billed.groupby(["vendor_name", "currency"])
        .agg(invoices=("id", "count"), total_spend=("total", "sum"), largest=("total", "max"))
        .reset_index()
    )
    # Share of spend only means something within one currency.
    out["share_of_currency_pct"] = (
        100 * out["total_spend"] / out.groupby("currency")["total_spend"].transform("sum")
    )
    return out.sort_values(["currency", "total_spend"], ascending=[True, False])


def monthly_spend(invoices: pd.DataFrame) -> pd.DataFrame:
    """Months down the side, one column per currency."""
    dated = invoices.dropna(subset=["issue_date"])
    if dated.empty:
        return dated
    return dated.assign(month=dated["issue_date"].dt.strftime("%Y-%m")).pivot_table(
        index="month", columns="currency", values="total", aggfunc="sum", fill_value=0.0
    )


def payment_terms(invoices: pd.DataFrame) -> pd.DataFrame:
    """Days between issue and due date, where both are printed."""
    both = invoices.dropna(subset=["issue_date", "due_date"])
    if both.empty:
        return both
    days = (both["due_date"] - both["issue_date"]).dt.days
    return (
        both.assign(days_to_pay=days)
        .groupby("currency")["days_to_pay"]
        .agg(invoices="count", shortest="min", median="median", longest="max")
        .reset_index()
    )


def processing_time(runs: pd.DataFrame) -> pd.DataFrame:
    timed = runs.dropna(subset=["duration_ms"])
    if timed.empty:
        return timed
    seconds = timed.assign(seconds=timed["duration_ms"] / 1000)
    return (
        seconds.groupby(["model", "prompt_version"])["seconds"]
        .agg(
            runs="count",
            mean="mean",
            median="median",
            p90=lambda s: s.quantile(0.9),
            slowest="max",
        )
        .reset_index()
    )


def accuracy_by_version(runs: pd.DataFrame) -> pd.DataFrame:
    scored = runs.dropna(subset=["field_accuracy"])
    if scored.empty:
        return scored
    return (
        scored.assign(day=scored["started_at"].dt.strftime("%Y-%m-%d"))
        .groupby(["day", "prompt_version", "model"])
        .agg(
            documents=("id", "count"),
            field_accuracy=("field_accuracy", "mean"),
            completeness=("extraction_completeness", "mean"),
            hallucination_rate=("hallucination_rate", "mean"),
            worst_document_accuracy=("field_accuracy", "min"),
        )
        .reset_index()
    )


def hardest_documents(runs: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """Documents the agent gets wrong most, averaged over every scored run."""
    scored = runs.dropna(subset=["field_accuracy"])
    if scored.empty:
        return scored
    out = (
        scored.groupby("filename")
        .agg(
            runs=("id", "count"),
            field_accuracy=("field_accuracy", "mean"),
            hallucination_rate=("hallucination_rate", "mean"),
        )
        .reset_index()
        .sort_values(["field_accuracy", "hallucination_rate"], ascending=[True, False])
    )
    return out[out["field_accuracy"] < 1.0].head(n)


def confidence_vs_accuracy(runs: pd.DataFrame) -> str:
    """Does the model's own confidence track how right it was?"""
    scored = runs.dropna(subset=["field_accuracy", "confidence"])
    if len(scored) < 3:
        return "Not enough scored runs to say."
    wrong = scored[scored["field_accuracy"] < 1.0]
    line = (
        f"{len(scored)} scored runs; {len(wrong)} had at least one wrong field. "
        f"Mean confidence was {scored['confidence'].mean():.2f} overall"
    )
    if not wrong.empty:
        line += f" and {wrong['confidence'].mean():.2f} on the runs with a mistake"
    line += "."
    if scored["confidence"].nunique() > 1 and scored["field_accuracy"].nunique() > 1:
        correlation = scored["confidence"].corr(scored["field_accuracy"])
        line += f" Correlation between confidence and accuracy: {correlation:.2f}."
    else:
        line += " Confidence or accuracy never varied, so there is no correlation to compute."
    return line


def build_report(database_url: str) -> str:
    invoices, runs = load_frames(database_url)
    with db.session_scope(database_url) as session:
        counts = analytics.table_counts(session)

    succeeded = int((runs["status"] == "succeeded").sum())
    sections = [
        "# Invoice data summary",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.",
        (
            f"{counts['documents']} documents, {counts['processing_runs']} processing runs "
            f"({succeeded} succeeded), {counts['invoices']} stored extractions, "
            f"{counts['line_items']} line items. Spend figures count each invoice once, "
            "using its latest extraction."
        ),
        "## Spend by vendor",
        to_markdown(spend_by_vendor(invoices)),
        "## Monthly spend by currency",
        to_markdown(monthly_spend(invoices), index=True),
        "## Payment terms (days from issue to due date)",
        to_markdown(payment_terms(invoices)),
        "## Processing time (seconds)",
        to_markdown(processing_time(runs)),
        "## Extraction accuracy by prompt version",
        "Eval runs only: these are the runs with a labelled answer to score against.",
        to_markdown(accuracy_by_version(runs)),
        "## Hardest documents",
        to_markdown(hardest_documents(runs)),
        "## Is the model's confidence informative?",
        confidence_vs_accuracy(runs),
    ]
    return "\n\n".join(sections) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stdout", action="store_true", help="print instead of writing a file")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL is empty")

    report = build_report(settings.database_url)
    if args.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
        print(report)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
