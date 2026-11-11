"""The agent loop, driven by a scripted fake model.

These tests assert on behaviour we actually care about: that the loop feeds tool
results back, that a rejection causes another turn, and that we never return an
unvalidated result.
"""

from __future__ import annotations

import pytest

from app.agent.runner import AgentError, ExtractionAgent
from tests.conftest import (
    VALID_SUBMISSION,
    VALID_VALIDATION_ARGS,
    FakeOpenAI,
    prose,
    tool_call,
    turn,
)


def test_happy_path_produces_a_validated_result(invoice_doc, settings):
    client = FakeOpenAI([
        turn(tool_call("a", "extract_invoice_fields", {"fields": ["amounts", "dates"]})),
        turn(tool_call("b", "classify_document_type",
                       {"proposed_type": "invoice", "evidence": "RECHNUNG header"})),
        turn(tool_call("c", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("d", "submit_extraction", VALID_SUBMISSION)),
    ])
    result = ExtractionAgent(client=client, settings=settings).run(invoice_doc)

    assert result.extraction.total == 4967.06
    assert result.extraction.currency == "EUR"
    assert result.validation.passed is True
    assert result.trace.stop_reason == "submitted"
    assert [c.tool for c in result.trace.tool_calls] == [
        "extract_invoice_fields",
        "classify_document_type",
        "validate_amounts",
        "submit_extraction",
    ]
    assert result.trace.prompt_tokens == 400  # 4 calls x 100


def test_tool_results_are_fed_back_to_the_model(invoice_doc, settings):
    client = FakeOpenAI([
        turn(tool_call("a", "search_document", {"query": "Gesamtbetrag"})),
        turn(tool_call("b", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("c", "submit_extraction", VALID_SUBMISSION)),
    ])
    ExtractionAgent(client=client, settings=settings).run(invoice_doc)

    # The second request must contain the tool result from the first turn.
    second = client.completions.requests[1]["messages"]
    tool_messages = [m for m in second if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert "4.967,06" in tool_messages[0]["content"]


def test_rejected_submission_is_retried(invoice_doc, settings):
    """Submitting before validating must bounce, and the loop must continue."""
    client = FakeOpenAI([
        turn(tool_call("a", "submit_extraction", VALID_SUBMISSION)),   # too early
        turn(tool_call("b", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("c", "submit_extraction", VALID_SUBMISSION)),   # now fine
    ])
    result = ExtractionAgent(client=client, settings=settings).run(invoice_doc)

    assert result.trace.steps == 3
    first_submit = result.trace.tool_calls[0]
    assert first_submit.ok is False
    assert "validate_amounts" in first_submit.result_summary


def test_schema_failure_is_reported_back_and_recoverable(invoice_doc, settings):
    bad = {**VALID_SUBMISSION, "currency": "euros"}
    client = FakeOpenAI([
        turn(tool_call("a", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("b", "submit_extraction", bad)),
        turn(tool_call("c", "submit_extraction", VALID_SUBMISSION)),
    ])
    result = ExtractionAgent(client=client, settings=settings).run(invoice_doc)

    assert result.extraction.currency == "EUR"
    third = client.completions.requests[2]["messages"]
    last_tool_message = [m for m in third if m["role"] == "tool"][-1]
    assert "ISO 4217" in last_tool_message["content"]


def test_prose_reply_is_nudged_not_accepted(invoice_doc, settings):
    client = FakeOpenAI([
        prose("Sure! The total appears to be 4967.06 EUR."),
        turn(tool_call("a", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("b", "submit_extraction", VALID_SUBMISSION)),
    ])
    result = ExtractionAgent(client=client, settings=settings).run(invoice_doc)

    assert result.trace.steps == 3
    second = client.completions.requests[1]["messages"]
    assert any("instead of calling a tool" in str(m.get("content")) for m in second)


def test_unknown_tool_does_not_crash_the_run(invoice_doc, settings):
    client = FakeOpenAI([
        turn(tool_call("a", "delete_production_database", {})),
        turn(tool_call("b", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("c", "submit_extraction", VALID_SUBMISSION)),
    ])
    result = ExtractionAgent(client=client, settings=settings).run(invoice_doc)
    assert result.trace.tool_calls[0].ok is False
    assert "unknown tool" in result.trace.tool_calls[0].result_summary


def test_malformed_arguments_are_reported_not_raised(invoice_doc, settings):
    broken = tool_call("a", "search_document", {})
    broken.function.arguments = "{not json"
    client = FakeOpenAI([
        turn(broken),
        turn(tool_call("b", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("c", "submit_extraction", VALID_SUBMISSION)),
    ])
    result = ExtractionAgent(client=client, settings=settings).run(invoice_doc)
    assert "not valid JSON" in result.trace.tool_calls[0].result_summary


def test_parallel_tool_calls_in_one_turn(invoice_doc, settings):
    client = FakeOpenAI([
        turn(
            tool_call("a", "validate_amounts", VALID_VALIDATION_ARGS),
            tool_call("b", "submit_extraction", VALID_SUBMISSION),
        ),
    ])
    result = ExtractionAgent(client=client, settings=settings).run(invoice_doc)
    assert result.trace.steps == 1
    assert result.extraction.total == 4967.06


def test_step_budget_exhaustion_raises_with_a_trace(invoice_doc, settings):
    settings.agent_max_steps = 3
    client = FakeOpenAI([
        turn(tool_call(str(i), "search_document", {"query": "x"})) for i in range(3)
    ])
    with pytest.raises(AgentError) as excinfo:
        ExtractionAgent(client=client, settings=settings).run(invoice_doc)

    trace = excinfo.value.trace
    assert trace is not None
    assert trace.stop_reason == "step_budget_exhausted"
    assert trace.steps == 3


def test_final_step_prompt_is_injected(invoice_doc, settings):
    settings.agent_max_steps = 2
    client = FakeOpenAI([
        turn(tool_call("a", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("b", "submit_extraction", VALID_SUBMISSION)),
    ])
    ExtractionAgent(client=client, settings=settings).run(invoice_doc)
    second = client.completions.requests[1]["messages"]
    assert any("final step" in str(m.get("content")) for m in second)


def test_preview_is_truncated_so_tools_are_necessary(invoice_doc, settings):
    settings.page_preview_chars = 120
    client = FakeOpenAI([
        turn(tool_call("a", "validate_amounts", VALID_VALIDATION_ARGS)),
        turn(tool_call("b", "submit_extraction", VALID_SUBMISSION)),
    ])
    ExtractionAgent(client=client, settings=settings).run(invoice_doc)
    user_message = client.completions.requests[0]["messages"][1]["content"]
    assert "truncated at 120 characters" in user_message
    assert "Gesamtbetrag" not in user_message


def test_missing_api_key_fails_fast(settings):
    settings.openai_api_key = ""
    with pytest.raises(AgentError):
        ExtractionAgent(settings=settings)


def test_provider_extras_on_tool_calls_are_echoed_back(invoice_doc, settings):
    # Gemini signs each tool call and rejects the next request if the
    # signature is not sent back with it.
    signed = tool_call("a", "validate_amounts", VALID_VALIDATION_ARGS)
    signed.extra_content = {"google": {"thought_signature": "sig-123"}}
    client = FakeOpenAI([
        turn(signed),
        turn(tool_call("b", "submit_extraction", VALID_SUBMISSION)),
    ])
    ExtractionAgent(client=client, settings=settings).run(invoice_doc)

    assistant = next(
        m for m in client.completions.requests[1]["messages"] if m["role"] == "assistant"
    )
    assert assistant["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "sig-123"}
    }
