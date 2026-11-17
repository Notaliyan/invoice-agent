"""The pandas report: same database, reshaped."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pd = pytest.importorskip("pandas")

from app import db  # noqa: E402
from app.analytics import report  # noqa: E402
from app.pdf import PdfDocument  # noqa: E402
from tests.conftest import make_extraction, make_trace  # noqa: E402


def _store(session, name: str, *, duration_ms=1000, scores=None, **fields):
    doc = PdfDocument.from_text(f"document {name}", filename=f"{name}.pdf")
    return db.record_success(
        session, doc, make_extraction(invoice_number=f"INV-{name}", **fields),
        trace=make_trace(),
        validation_passed=True, duration_ms=duration_ms,
        source="eval" if scores else "api", scores=scores,
        started_at=datetime(2024, 5, 6, 10, 0, tzinfo=UTC),
    )


@pytest.fixture
def seeded(database_url) -> str:
    with db.session_scope(database_url) as session:
        _store(session, "a", vendor={"name": "Acme"}, total=100.0,
               issue_date="2024-03-05", due_date="2024-04-04", duration_ms=1000,
               scores=db.EvalScores("run", 1.0, 1.0, 0.0), confidence=0.9)
        _store(session, "b", vendor={"name": "Acme"}, total=300.0,
               issue_date="2024-03-20", due_date="2024-04-03", duration_ms=3000,
               scores=db.EvalScores("run", 0.8, 1.0, 0.2), confidence=0.6)
        _store(session, "c", vendor={"name": "Borealis"}, currency="USD", total=50.0,
               issue_date="2024-04-02", due_date=None, duration_ms=None,
               scores=db.EvalScores("run", 1.0, 1.0, 0.0), confidence=0.95)
    return database_url


def test_spend_by_vendor_computes_share_within_each_currency(seeded):
    invoices, _ = report.load_frames(seeded)
    spend = report.spend_by_vendor(invoices).set_index("vendor_name")

    assert spend.loc["Acme", "total_spend"] == 400.0
    assert spend.loc["Acme", "largest"] == 300.0
    # Each vendor is the only one in its currency here.
    assert spend["share_of_currency_pct"].tolist() == [100.0, 100.0]


def test_monthly_spend_pivots_currencies_into_columns(seeded):
    invoices, _ = report.load_frames(seeded)
    pivot = report.monthly_spend(invoices)

    assert list(pivot.columns) == ["EUR", "USD"]
    assert pivot.loc["2024-03", "EUR"] == 400.0
    assert pivot.loc["2024-04", "USD"] == 50.0
    assert pivot.loc["2024-04", "EUR"] == 0.0


def test_payment_terms_use_only_documents_with_both_dates(seeded):
    invoices, _ = report.load_frames(seeded)
    terms = report.payment_terms(invoices)

    assert terms["currency"].tolist() == ["EUR"]  # the USD receipt has no due date
    assert terms.loc[0, ["invoices", "shortest", "median", "longest"]].tolist() == [2, 14, 22, 30]


def test_processing_time_ignores_untimed_runs(seeded):
    _, runs = report.load_frames(seeded)
    timing = report.processing_time(runs)

    assert timing.loc[0, "runs"] == 2
    assert timing.loc[0, "mean"] == 2.0
    assert timing.loc[0, "slowest"] == 3.0


def test_accuracy_and_hardest_documents(seeded):
    _, runs = report.load_frames(seeded)

    accuracy = report.accuracy_by_version(runs)
    assert accuracy.loc[0, "day"] == "2024-05-06"
    assert accuracy.loc[0, "documents"] == 3
    assert accuracy.loc[0, "field_accuracy"] == pytest.approx(2.8 / 3)
    assert accuracy.loc[0, "worst_document_accuracy"] == 0.8

    assert report.hardest_documents(runs)["filename"].tolist() == ["b.pdf"]


def test_confidence_is_compared_with_accuracy(seeded):
    _, runs = report.load_frames(seeded)
    text = report.confidence_vs_accuracy(runs)

    assert "3 scored runs; 1 had at least one wrong field" in text
    assert "0.60 on the runs with a mistake" in text
    assert "Correlation between confidence and accuracy: 0.9" in text


def test_the_full_report_has_every_section(seeded):
    text = report.build_report(seeded)

    for heading in ("# Invoice data summary", "## Spend by vendor", "## Monthly spend",
                    "## Payment terms", "## Processing time", "## Extraction accuracy",
                    "## Hardest documents", "## Is the model's confidence informative?"):
        assert heading in text
    assert "3 documents, 3 processing runs (3 succeeded)" in text
    assert "| Acme | EUR | 2 | 400.00 | 300.00 | 100.00 |" in text


def test_an_empty_database_still_produces_a_report(database_url):
    text = report.build_report(database_url)
    assert "0 documents" in text
    assert "_No data._" in text
    assert "Not enough scored runs" in text


def test_cli_writes_the_report_file(seeded, tmp_path, monkeypatch, settings):
    monkeypatch.setattr(
        report, "get_settings", lambda: settings.model_copy(update={"database_url": seeded})
    )
    out = tmp_path / "nested" / "summary.md"
    assert report.main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Invoice data summary")
