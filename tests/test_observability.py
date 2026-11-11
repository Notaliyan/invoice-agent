"""Tracing: off by default, and when on, every model call and tool call is
recorded. Langfuse is faked - these tests never touch the network."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app import observability
from app.agent.prompts import PROMPT_VERSION
from app.agent.runner import AgentError, ExtractionAgent
from app.config import Settings
from tests.conftest import (
    VALID_SUBMISSION,
    VALID_VALIDATION_ARGS,
    FakeOpenAI,
    prose,
    tool_call,
    turn,
)


class FakeObservation:
    def __init__(self, sink: FakeLangfuse, kwargs: dict[str, Any]) -> None:
        self.sink = sink
        self.data = dict(kwargs)
        self.trace_id = "trace-123"

    def update(self, **kwargs: Any) -> FakeObservation:
        self.data.update(kwargs)
        return self

    def score_trace(self, **kwargs: Any) -> None:
        self.sink.scores[kwargs["name"]] = kwargs["value"]


class FakeLangfuse:
    def __init__(self) -> None:
        self.observations: list[FakeObservation] = []
        self.scores: dict[str, Any] = {}
        self.flushed = False

    @contextmanager
    def start_as_current_observation(self, **kwargs: Any):
        obs = FakeObservation(self, kwargs)
        self.observations.append(obs)
        yield obs

    def flush(self) -> None:
        self.flushed = True

    def of_type(self, as_type: str) -> list[FakeObservation]:
        return [o for o in self.observations if o.data["as_type"] == as_type]


@pytest.fixture
def langfuse(monkeypatch, settings) -> FakeLangfuse:
    fake = FakeLangfuse()
    monkeypatch.setattr("app.agent.runner.get_langfuse", lambda _settings: fake)
    monkeypatch.setattr("app.agent.runner.propagate", lambda _client, **_: _null())
    return fake


@contextmanager
def _null():
    yield


HAPPY_SCRIPT = [
    turn(tool_call("a", "extract_invoice_fields", {"fields": ["amounts"]})),
    turn(tool_call("b", "validate_amounts", VALID_VALIDATION_ARGS)),
    turn(tool_call("c", "submit_extraction", VALID_SUBMISSION)),
]


def test_tracing_is_off_without_keys(settings):
    assert settings.langfuse_enabled is False
    assert observability.get_langfuse(settings) is None
    with observability.observation(None, name="x", as_type="span") as obs:
        assert obs.update(output=1) is obs
        assert obs.trace_id is None
        obs.score_trace(name="s", value=1)
    with observability.propagate(None, version="v"):
        pass
    observability.flush(settings)  # must be a no-op, not an error


def test_tracing_needs_both_keys():
    assert Settings(langfuse_public_key="pk", langfuse_secret_key="").langfuse_enabled is False
    assert Settings(langfuse_public_key="pk", langfuse_secret_key="sk").langfuse_enabled is True


def test_agent_runs_untraced_by_default(invoice_doc, settings):
    result = ExtractionAgent(client=FakeOpenAI(HAPPY_SCRIPT), settings=settings).run(invoice_doc)
    assert result.trace.langfuse_trace_id is None
    assert result.trace.prompt_version == PROMPT_VERSION


def test_every_model_call_becomes_a_generation(invoice_doc, settings, langfuse):
    ExtractionAgent(client=FakeOpenAI(HAPPY_SCRIPT), settings=settings).run(invoice_doc)

    generations = langfuse.of_type("generation")
    assert [g.data["name"] for g in generations] == ["llm-step-1", "llm-step-2", "llm-step-3"]
    for g in generations:
        assert g.data["model"] == "fake-model"
        assert g.data["usage_details"] == {"input": 100, "output": 20}
        assert g.data["input"][0]["role"] == "system"
    assert generations[0].data["output"]["tool_calls"][0]["name"] == "extract_invoice_fields"


def test_every_tool_call_becomes_a_tool_span(invoice_doc, settings, langfuse):
    ExtractionAgent(client=FakeOpenAI(HAPPY_SCRIPT), settings=settings).run(invoice_doc)

    tools = langfuse.of_type("tool")
    assert [t.data["name"] for t in tools] == [
        "extract_invoice_fields",
        "validate_amounts",
        "submit_extraction",
    ]
    assert tools[1].data["input"] == VALID_VALIDATION_ARGS
    assert '"passed": true' in tools[1].data["output"]
    assert all(t.data["level"] == "DEFAULT" for t in tools)


def test_root_observation_carries_result_and_scores(invoice_doc, settings, langfuse):
    result = ExtractionAgent(client=FakeOpenAI(HAPPY_SCRIPT), settings=settings).run(invoice_doc)

    (root,) = langfuse.of_type("agent")
    assert root.data["name"] == "invoice-extraction"
    assert root.data["input"]["filename"] == "nordlicht.pdf"
    assert root.data["output"]["total"] == 4967.06
    assert root.data["metadata"]["stop_reason"] == "submitted"
    assert langfuse.scores == {
        "extraction_succeeded": 1,
        "validation_passed": 1,
        "model_confidence": 0.93,
    }
    assert result.trace.langfuse_trace_id == "trace-123"


def test_failed_tool_call_is_marked_as_a_warning(invoice_doc, settings, langfuse):
    client = FakeOpenAI([
        turn(tool_call("a", "submit_extraction", VALID_SUBMISSION)),  # before validating
        turn(tool_call("b", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("c", "submit_extraction", VALID_SUBMISSION)),
    ])
    ExtractionAgent(client=client, settings=settings).run(invoice_doc)

    first = langfuse.of_type("tool")[0]
    assert first.data["level"] == "WARNING"
    assert "validate_amounts before submitting" in first.data["status_message"]


def test_failed_run_is_traced_as_an_error(invoice_doc, settings, langfuse):
    client = FakeOpenAI([prose("thinking...")] * settings.agent_max_steps)
    with pytest.raises(AgentError):
        ExtractionAgent(client=client, settings=settings).run(invoice_doc)

    (root,) = langfuse.of_type("agent")
    assert root.data["level"] == "ERROR"
    assert root.data["metadata"]["stop_reason"] == "step_budget_exhausted"
    assert langfuse.scores == {"extraction_succeeded": 0}
    assert len(langfuse.of_type("generation")) == settings.agent_max_steps
