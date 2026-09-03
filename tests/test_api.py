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
