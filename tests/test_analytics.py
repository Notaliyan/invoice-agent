"""The analytics SQL, run against a small database with known answers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import analytics, db, main
from app.pdf import PdfDocument
from tests.conftest import make_extraction, make_trace

ITEM = {"description": "Consulting", "quantity": 2, "unit_price": 50.0, "amount": 100.0}


def _doc(name: str) -> PdfDocument:
    return PdfDocument.from_text(f"document {name}", filename=f"{name}.pdf")


def _store(session, name: str, *, trace=None, duration_ms=1000, started_at=None,
           scores=None, source="api", **fields):
    defaults = {"line_items": [ITEM], "subtotal": 100.0, "invoice_number": f"INV-{name}"}
    extraction = make_extraction(**{**defaults, **fields})
    return db.record_success(
        session, _doc(name), extraction, trace=trace or make_trace(),
        validation_passed=True, duration_ms=duration_ms,
        source=source, scores=scores, started_at=started_at,
    )


@pytest.fixture
def seeded(database_url) -> str:
    """Four documents:

    a  Acme    EUR invoice  2024-03  total 100
    b  Acme    EUR invoice  2024-03  total 300
    c  Borealis USD receipt 2024-04  total 50   (no issue-to-due gap)
    d  Acme    EUR purchase order 2024-04 total 999 (not spend)
    """
    acme = {"name": "Acme GmbH"}
    with db.session_scope(database_url) as session:
        _store(session, "a", vendor=acme, total=100.0, issue_date="2024-03-05")
        _store(session, "b", vendor=acme, total=300.0, issue_date="2024-03-20",
               tax_amount=57.0)
        _store(session, "c", vendor={"name": "Borealis Inc"}, currency="USD",
               document_type="receipt", total=50.0, issue_date="2024-04-02")
        _store(session, "d", vendor=acme, document_type="purchase_order", total=999.0,
               issue_date="2024-04-09")
    return database_url


def _rows(url: str, name: str, **kwargs):
    with db.session_scope(url) as session:
        return analytics.run_query(session, name, **kwargs)


def test_spend_by_vendor_adds_up_and_leaves_out_purchase_orders(seeded):
    rows = _rows(seeded, "spend_by_vendor")
    assert rows == [
        {"vendor": "Acme GmbH", "currency": "EUR", "invoices": 2, "total_spend": 400.0,
         "average_invoice": 200.0, "first_invoice": "2024-03-05",
         "last_invoice": "2024-03-20"},
        {"vendor": "Borealis Inc", "currency": "USD", "invoices": 1, "total_spend": 50.0,
         "average_invoice": 50.0, "first_invoice": "2024-04-02",
         "last_invoice": "2024-04-02"},
    ]


def test_reprocessing_a_document_does_not_double_its_spend(seeded):
    # Document "a" again, and this time the agent reads the total as 120.
    with db.session_scope(seeded) as session:
        _store(session, "a", vendor={"name": "Acme GmbH"}, total=120.0,
               issue_date="2024-03-05")

    acme = _rows(seeded, "spend_by_vendor")[0]
    assert acme["invoices"] == 2          # still two documents
    assert acme["total_spend"] == 420.0   # 120 + 300: the latest reading wins


def test_monthly_volume_groups_by_month_and_currency(seeded):
    assert _rows(seeded, "monthly_invoice_volume") == [
        {"month": "2024-03", "currency": "EUR", "invoices": 2, "total_value": 400.0,
         "total_tax": 57.0 + 793.06},
        {"month": "2024-04", "currency": "EUR", "invoices": 1, "total_value": 999.0,
         "total_tax": 793.06},
        {"month": "2024-04", "currency": "USD", "invoices": 1, "total_value": 50.0,
         "total_tax": 793.06},
    ]


def test_undated_documents_are_left_out_of_monthly_volume(seeded):
    with db.session_scope(seeded) as session:
        _store(session, "undated", issue_date=None, total=5.0)
    assert sum(r["invoices"] for r in _rows(seeded, "monthly_invoice_volume")) == 4


def test_processing_performance_averages_per_model_and_prompt(database_url):
    with db.session_scope(database_url) as session:
        _store(session, "a", duration_ms=1000, trace=make_trace(steps=4))
        _store(session, "b", duration_ms=3000, trace=make_trace(steps=6))
        _store(session, "c", duration_ms=None, trace=make_trace(prompt_version="v2"))
        db.record_failure(
            session, _doc("e"), error="gave up", model="fake-model",
            trace=make_trace(steps=6, stop_reason="step_budget_exhausted"),
            duration_ms=5000,
        )

    v1, v2 = _rows(database_url, "processing_performance")
    assert (v1["model"], v1["prompt_version"]) == ("fake-model", "v1")
    assert (v1["runs"], v1["succeeded"], v1["timed_runs"]) == (3, 2, 3)
    assert v1["avg_duration_ms"] == 3000.0
    assert (v1["fastest_ms"], v1["slowest_ms"]) == (1000, 5000)
    assert v1["avg_steps"] == pytest.approx(16 / 3, abs=1e-4)
    assert v1["avg_tokens"] == 480.0

    # A run with no recorded duration counts as a run but not towards timing.
    assert (v2["runs"], v2["timed_runs"], v2["avg_duration_ms"]) == (1, 0, None)


def test_quality_over_time_separates_days_and_prompt_versions(database_url):
    def scores(accuracy: float, hallucination: float) -> db.EvalScores:
        return db.EvalScores("run", accuracy, 1.0, hallucination)

    monday = datetime(2024, 5, 6, 10, 0, tzinfo=UTC)
    tuesday = datetime(2024, 5, 7, 10, 0, tzinfo=UTC)
    with db.session_scope(database_url) as session:
        _store(session, "a", started_at=monday, source="eval", scores=scores(1.0, 0.0))
        _store(session, "b", started_at=monday, source="eval", scores=scores(0.8, 0.2))
        _store(session, "c", started_at=monday)  # an upload: no label, so no scores
        _store(session, "a", started_at=tuesday, source="eval", scores=scores(1.0, 0.0),
               trace=make_trace(prompt_version="v2"))
        db.record_failure(
            session, _doc("b"), error="gave up", model="fake-model", source="eval",
            trace=make_trace(prompt_version="v2"), scores=scores(0.0, 0.0),
            started_at=tuesday,
        )

    first, second = _rows(database_url, "extraction_quality_over_time")
    assert (first["day"], first["prompt_version"]) == ("2024-05-06", "v1")
    assert (first["runs"], first["scored_runs"]) == (3, 2)
    assert first["success_rate"] == 1.0
    assert first["avg_field_accuracy"] == pytest.approx(0.9)
    assert first["avg_hallucination_rate"] == pytest.approx(0.1)

    assert (second["day"], second["prompt_version"]) == ("2024-05-07", "v2")
    assert second["success_rate"] == 0.5
    assert second["validation_pass_rate"] == 0.5
    assert second["avg_field_accuracy"] == 0.5  # the failure scores zero and counts


def test_top_line_items_ranks_by_amount_within_a_currency(database_url):
    licence = {"description": "Licence", "quantity": 1, "unit_price": 900.0, "amount": 900.0}
    with db.session_scope(database_url) as session:
        _store(session, "a", vendor={"name": "Acme"}, line_items=[ITEM, licence])
        _store(session, "b", vendor={"name": "Borealis"}, line_items=[ITEM])

    rows = _rows(database_url, "top_line_items")
    assert rows == [
        {"description": "Licence", "currency": "EUR", "times_billed": 1, "vendors": 1,
         "total_quantity": 1.0, "total_amount": 900.0},
        {"description": "Consulting", "currency": "EUR", "times_billed": 2, "vendors": 2,
         "total_quantity": 4.0, "total_amount": 200.0},
    ]
    assert len(_rows(database_url, "top_line_items", limit=1)) == 1


def test_the_same_invoice_in_two_files_is_counted_once(database_url):
    # A scan and the original PDF: different text, same vendor and number.
    acme = {"name": "Acme GmbH"}
    with db.session_scope(database_url) as session:
        _store(session, "original", vendor=acme, invoice_number="INV-7", total=100.0)
        _store(session, "scan", vendor=acme, invoice_number="INV-7", total=100.0)
        _store(session, "other", vendor=acme, invoice_number="INV-8", total=40.0)
        assert analytics.table_counts(session)["documents"] == 3

    (row,) = _rows(database_url, "spend_by_vendor")
    assert (row["invoices"], row["total_spend"]) == (2, 140.0)


def test_documents_without_an_invoice_number_are_never_merged(database_url):
    shop = {"name": "Corner Shop"}
    with db.session_scope(database_url) as session:
        for name in ("monday", "tuesday"):
            _store(session, name, vendor=shop, invoice_number=None,
                   document_type="receipt", total=10.0)

    (row,) = _rows(database_url, "spend_by_vendor")
    assert (row["invoices"], row["total_spend"]) == (2, 20.0)


def test_arithmetic_exceptions_finds_only_the_documents_that_do_not_add_up(database_url):
    with db.session_scope(database_url) as session:
        # The line items sum to 100 in every case.
        _store(session, "adds-up", subtotal=100.0, tax_amount=19.0, total=119.0)
        _store(session, "rounding", subtotal=100.01, tax_amount=19.0, total=119.01)
        _store(session, "no-subtotal", subtotal=None, tax_amount=19.0, total=119.0)
        _store(session, "no-tax", subtotal=None, tax_amount=None, total=100.0)
        _store(session, "short-lines", subtotal=150.0, tax_amount=0.0, total=150.0)
        _store(session, "bad-total", subtotal=100.0, tax_amount=19.0, total=125.0)

    rows = _rows(database_url, "arithmetic_exceptions")
    assert [(r["filename"], r["line_item_gap"], r["total_gap"]) for r in rows] == [
        ("bad-total.pdf", 0.0, -6.0),
        ("short-lines.pdf", -50.0, 0.0),
    ]


def test_token_average_ignores_runs_that_recorded_none(database_url):
    with db.session_scope(database_url) as session:
        _store(session, "a")  # 480 tokens
        _store(session, "b", trace=make_trace(prompt_tokens=0, completion_tokens=0))

    (row,) = _rows(database_url, "processing_performance")
    assert (row["runs"], row["avg_tokens"]) == (2, 480.0)


def test_every_query_runs_on_an_empty_database(database_url):
    with db.session_scope(database_url) as session:
        results = analytics.run_all(session)
        assert set(results) == set(analytics.QUERIES)
        assert all(rows == [] for rows in results.values())
        assert analytics.table_counts(session) == {
            "documents": 0, "processing_runs": 0, "invoices": 0, "line_items": 0,
        }


def test_there_are_six_queries_and_each_explains_itself():
    assert len(analytics.QUERIES) == 6
    for query in analytics.QUERIES.values():
        assert query.description and query.sql.lstrip().upper().startswith(("SELECT", "WITH"))


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def test_analytics_endpoint_returns_every_query_with_its_sql(client, seeded):
    response = client.get("/analytics")
    assert response.status_code == 200

    body = response.json()
    assert body["tables"] == {
        "documents": 4, "processing_runs": 4, "invoices": 4, "line_items": 4,
    }
    assert set(body["queries"]) == set(analytics.QUERIES)
    spend = body["queries"]["spend_by_vendor"]
    assert spend["rows"][0]["total_spend"] == 400.0
    assert "GROUP BY vendor_name" in spend["sql"]
    assert spend["description"]


def test_analytics_endpoint_returns_one_query_by_name(client, seeded):
    body = client.get("/analytics/monthly_invoice_volume").json()
    assert body["name"] == "monthly_invoice_volume"
    assert [r["month"] for r in body["rows"]] == ["2024-03", "2024-04", "2024-04"]


def test_analytics_limit_is_applied_and_validated(client, seeded):
    assert len(client.get("/analytics/spend_by_vendor?limit=1").json()["rows"]) == 1
    assert client.get("/analytics/spend_by_vendor?limit=0").status_code == 422


def test_unknown_query_is_a_404_that_lists_the_real_ones(client):
    response = client.get("/analytics/drop_tables")
    assert response.status_code == 404
    assert "spend_by_vendor" in response.json()["detail"]


def test_analytics_is_unavailable_when_storage_is_off(client, monkeypatch):
    monkeypatch.setattr(main.settings, "database_url", "")
    assert client.get("/analytics").status_code == 503
    assert client.get("/analytics/spend_by_vendor").status_code == 503
