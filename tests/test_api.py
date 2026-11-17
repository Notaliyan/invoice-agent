"""HTTP surface. The agent is stubbed - we are testing wiring, not extraction."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent.runner import AgentError, AgentResult
from app.schemas import AgentTrace, ExtractionResult, ValidationCheck, ValidationReport
from tests.conftest import VALID_SUBMISSION


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def _stub_result() -> AgentResult:
    return AgentResult(
        extraction=ExtractionResult.model_validate(VALID_SUBMISSION),
        validation=ValidationReport(
            passed=True,
            checks=[ValidationCheck(name="total_is_positive", passed=True, detail="ok")],
        ),
        trace=AgentTrace(model="fake-model", steps=3, stop_reason="submitted"),
    )


@pytest.fixture
def stub_agent(monkeypatch):
    """Replace ExtractionAgent with something that returns a canned result."""

    def install(behaviour):
        class Stub:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, doc):
                return behaviour(doc)

        monkeypatch.setattr(main, "ExtractionAgent", Stub)

    return install


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "model" in body


def test_index_serves_the_upload_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Document Intelligence Agent" in response.text


def test_process_text_returns_the_full_envelope(client, stub_agent):
    stub_agent(lambda doc: _stub_result())
    response = client.post("/process/text", json={"text": "RECHNUNG ...", "filename": "x.txt"})
    assert response.status_code == 200

    body = response.json()
    assert body["extraction"]["total"] == 4967.06
    assert body["validation"]["passed"] is True
    assert body["trace"]["stop_reason"] == "submitted"
    assert body["pages"] == 1
    assert len(body["document_id"]) == 32
    assert body["duration_ms"] >= 0


def test_process_text_rejects_empty_body(client):
    assert client.post("/process/text", json={"text": ""}).status_code == 422


def test_agent_failure_returns_422_with_the_trace(client, stub_agent):
    trace = AgentTrace(model="fake-model", steps=6, stop_reason="step_budget_exhausted")

    def fail(doc):
        raise AgentError("ran out of steps", trace=trace)

    stub_agent(fail)
    response = client.post("/process/text", json={"text": "hello"})
    assert response.status_code == 422

    body = response.json()
    assert body["error"] == "extraction_failed"
    assert body["trace"]["stop_reason"] == "step_budget_exhausted"


def test_upload_rejects_a_non_pdf(client):
    response = client.post(
        "/process", files={"file": ("notes.txt", b"just some text", "text/plain")}
    )
    assert response.status_code == 422
    assert "%PDF" in response.json()["detail"]


def test_upload_rejects_an_empty_file(client):
    response = client.post(
        "/process", files={"file": ("empty.pdf", b"", "application/pdf")}
    )
    assert response.status_code == 400


def test_upload_rejects_an_oversized_file(client, monkeypatch):
    monkeypatch.setattr(main.settings, "max_upload_bytes", 10)
    response = client.post(
        "/process", files={"file": ("big.pdf", b"%PDF-1.4" + b"x" * 100, "application/pdf")}
    )
    assert response.status_code == 413


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def _counts() -> dict[str, int]:
    from app import analytics, db

    with db.session_scope(main.settings.database_url) as session:
        return analytics.table_counts(session)


def test_a_processed_document_is_stored(client, stub_agent):
    stub_agent(lambda doc: _stub_result())
    body = client.post("/process/text", json={"text": "RECHNUNG ..."}).json()

    assert isinstance(body["run_id"], int)
    assert _counts() == {"documents": 1, "processing_runs": 1, "invoices": 1, "line_items": 3}


def test_the_same_document_keeps_its_id_across_uploads(client, stub_agent):
    stub_agent(lambda doc: _stub_result())
    first = client.post("/process/text", json={"text": "RECHNUNG ..."}).json()
    second = client.post("/process/text", json={"text": "RECHNUNG ..."}).json()
    other = client.post("/process/text", json={"text": "something else"}).json()

    assert first["document_id"] == second["document_id"] != other["document_id"]
    assert first["run_id"] != second["run_id"]
    assert _counts()["documents"] == 2


def test_a_failed_extraction_is_stored_as_a_failed_run(client, stub_agent):
    def fail(doc):
        raise AgentError(
            "Agent did not produce a validated extraction within 6 steps.",
            trace=AgentTrace(model="fake-model", steps=6, stop_reason="step_budget_exhausted"),
        )

    stub_agent(fail)
    assert client.post("/process/text", json={"text": "hello"}).status_code == 422
    assert _counts() == {"documents": 1, "processing_runs": 1, "invoices": 0, "line_items": 0}

    rows = client.get("/analytics/processing_performance").json()["rows"]
    assert (rows[0]["runs"], rows[0]["succeeded"]) == (1, 0)


def test_a_database_failure_does_not_lose_the_extraction(client, stub_agent, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    stub_agent(lambda doc: _stub_result())
    monkeypatch.setattr(main.db, "record_success", broken)
    response = client.post("/process/text", json={"text": "RECHNUNG ..."})

    assert response.status_code == 200
    body = response.json()
    assert body["extraction"]["total"] == 4967.06
    assert body["run_id"] is None
    assert len(body["document_id"]) == 32


def test_processing_works_with_storage_off(client, stub_agent, monkeypatch):
    stub_agent(lambda doc: _stub_result())
    monkeypatch.setattr(main.settings, "database_url", "")
    body = client.post("/process/text", json={"text": "RECHNUNG ..."}).json()
    assert body["run_id"] is None
    assert client.get("/health").json()["storage"] is False
