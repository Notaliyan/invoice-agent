"""Storing runs: what lands in which table, and what does not get duplicated."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app import db
from app.pdf import PdfDocument
from tests.conftest import INVOICE_TEXT, make_extraction, make_trace


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_a_successful_run_fills_all_four_tables(database_url, invoice_doc):
    with db.session_scope(database_url) as session:
        run = db.record_success(
            session,
            invoice_doc,
            make_extraction(),
            trace=make_trace(langfuse_trace_id="abc123"),
            validation_passed=True,
            duration_ms=1840,
        )
        run_id = run.id

    with db.session_scope(database_url) as session:
        run = session.get(db.ProcessingRun, run_id)
        assert (run.status, run.source, run.model, run.prompt_version) == (
            "succeeded", "api", "fake-model", "v1",
        )
        assert (run.steps, run.prompt_tokens, run.completion_tokens) == (4, 400, 80)
        assert run.duration_ms == 1840
        assert run.validation_passed is True
        assert run.confidence == 0.93
        assert run.langfuse_trace_id == "abc123"
        assert run.field_accuracy is None  # only eval runs are scored

        document = run.document
        assert document.filename == "nordlicht.pdf"
        assert document.page_count == 1
        assert len(document.public_id) == 32

        invoice = run.invoice
        assert invoice.vendor_name == "Nordlicht Systeme GmbH"
        assert invoice.vendor_tax_id == "DE812345678"
        assert invoice.customer_name == "Meridian Analytics AG"
        assert invoice.issue_date == date(2024, 3, 14)
        assert invoice.currency == "EUR"
        assert invoice.total == 4967.06

        assert [(i.position, i.description, i.amount) for i in invoice.line_items] == [
            (1, "Sensor gateway SG-40", 2214.00),
            (2, "Installation", 1000.00),
            (3, "Wartungsvertrag 12 Monate", 960.00),
        ]


def test_the_same_document_twice_is_one_document_with_two_runs(database_url, invoice_doc):
    renamed = PdfDocument.from_text(INVOICE_TEXT, filename="copy-of-nordlicht.pdf")
    with db.session_scope(database_url) as session:
        for doc in (invoice_doc, renamed):
            db.record_success(
                session, doc, make_extraction(), trace=make_trace(),
                validation_passed=True, duration_ms=100,
            )

    with db.session_scope(database_url) as session:
        assert _count(session, db.Document) == 1
        assert _count(session, db.ProcessingRun) == 2
        assert _count(session, db.Invoice) == 2
        # Identity is the content, so the first filename is kept.
        assert session.scalar(select(db.Document.filename)) == "nordlicht.pdf"


def test_different_documents_are_stored_separately(database_url, invoice_doc, contract_doc):
    with db.session_scope(database_url) as session:
        first = db.record_success(
            session, invoice_doc, make_extraction(), trace=make_trace(),
            validation_passed=True, duration_ms=100,
        )
        second = db.record_success(
            session, contract_doc, make_extraction(document_type="contract"),
            trace=make_trace(), validation_passed=True, duration_ms=100,
        )
        assert first.document.public_id != second.document.public_id


def test_a_failed_run_is_recorded_without_an_invoice(database_url, invoice_doc):
    trace = make_trace(steps=6, stop_reason="step_budget_exhausted")
    with db.session_scope(database_url) as session:
        db.record_failure(
            session, invoice_doc, error="Agent did not produce a validated extraction.",
            model="fake-model", trace=trace, duration_ms=9000,
        )

    with db.session_scope(database_url) as session:
        run = session.scalar(select(db.ProcessingRun))
        assert run.status == "failed"
        assert run.stop_reason == "step_budget_exhausted"
        assert run.steps == 6
        assert "validated extraction" in run.error
        assert run.invoice is None
        assert _count(session, db.Invoice) == 0


def test_a_failure_before_any_trace_still_records_the_model(database_url, invoice_doc):
    with db.session_scope(database_url) as session:
        db.record_failure(session, invoice_doc, error="boom", model="some-model")

    with db.session_scope(database_url) as session:
        run = session.scalar(select(db.ProcessingRun))
        assert (run.model, run.steps, run.duration_ms) == ("some-model", 0, None)


def test_eval_scores_are_stored_on_the_run(database_url, invoice_doc):
    scores = db.EvalScores(
        run_name="v1-fake-model-20240101-000000",
        field_accuracy=0.93,
        extraction_completeness=1.0,
        hallucination_rate=0.07,
    )
    with db.session_scope(database_url) as session:
        db.record_success(
            session, invoice_doc, make_extraction(), trace=make_trace(),
            validation_passed=True, duration_ms=100, source="eval", scores=scores,
        )

    with db.session_scope(database_url) as session:
        run = session.scalar(select(db.ProcessingRun))
        assert run.source == "eval"
        assert run.eval_run_name == scores.run_name
        assert (run.field_accuracy, run.hallucination_rate) == (0.93, 0.07)


def test_an_error_rolls_the_whole_run_back(database_url, invoice_doc):
    with pytest.raises(RuntimeError):
        with db.session_scope(database_url) as session:
            db.record_success(
                session, invoice_doc, make_extraction(), trace=make_trace(),
                validation_passed=True, duration_ms=100,
            )
            raise RuntimeError("something else went wrong")

    with db.session_scope(database_url) as session:
        assert _count(session, db.Document) == 0
        assert _count(session, db.ProcessingRun) == 0
        assert _count(session, db.LineItem) == 0


def test_foreign_keys_are_enforced(database_url):
    # SQLite only checks them when asked; a line item must not outlive its invoice.
    with pytest.raises(IntegrityError):
        with db.session_scope(database_url) as session:
            session.execute(
                text(
                    "INSERT INTO line_items (invoice_id, position, description, amount) "
                    "VALUES (999, 1, 'orphan', 1.0)"
                )
            )


def test_deleting_a_document_removes_everything_under_it(database_url, invoice_doc):
    with db.session_scope(database_url) as session:
        run = db.record_success(
            session, invoice_doc, make_extraction(), trace=make_trace(),
            validation_passed=True, duration_ms=100,
        )
        document_id = run.document_id

    with db.session_scope(database_url) as session:
        session.delete(session.get(db.Document, document_id))

    with db.session_scope(database_url) as session:
        for model in (db.Document, db.ProcessingRun, db.Invoice, db.LineItem):
            assert _count(session, model) == 0


def test_an_in_memory_database_works(invoice_doc):
    url = "sqlite://"
    try:
        with db.session_scope(url) as session:
            db.record_success(
                session, invoice_doc, make_extraction(), trace=make_trace(),
                validation_passed=True, duration_ms=100,
            )
        with db.session_scope(url) as session:
            assert _count(session, db.Invoice) == 1
    finally:
        db.dispose_engine(url)
