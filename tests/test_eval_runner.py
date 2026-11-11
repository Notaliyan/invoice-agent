"""The eval runner end to end, with a scripted model and no Langfuse."""

from __future__ import annotations

import json

import pytest

from evals.dataset import GoldenCase, load_cases
from evals.run_eval import format_report, load_history, main, run_eval
from tests.conftest import FakeOpenAI, prose, tool_call, turn


def perfect_script(case: GoldenCase) -> list:
    """A model that reads the label: validates, then submits the right answer."""
    expected = case.expected
    return [
        turn(tool_call("v", "validate_amounts", {
            "total": expected["total"],
            "subtotal": expected["subtotal"],
            "tax_amount": expected["tax_amount"],
            "tax_rate": expected["tax_rate"],
            "line_items": expected["line_items"],
        })),
        turn(tool_call("s", "submit_extraction", {**expected, "confidence": 0.9})),
    ]


@pytest.fixture
def cases() -> list[GoldenCase]:
    return load_cases()[:2]


def test_perfect_model_scores_perfectly_and_is_recorded(cases, settings, tmp_path):
    client = FakeOpenAI([step for case in cases for step in perfect_script(case)])
    report = run_eval(settings=settings, client=client, cases=cases, results_dir=tmp_path)

    assert report.metrics == {
        "field_accuracy": 1.0,
        "extraction_completeness": 1.0,
        "hallucination_rate": 0.0,
    }
    assert report.failed_cases == []
    assert report.langfuse_url is None

    (entry,) = load_history(tmp_path)
    assert entry["run_name"] == report.run_name
    assert entry["prompt_version"] == report.prompt_version
    assert set(entry["per_case"]) == {c.id for c in cases}
    detail = json.loads((tmp_path / f"{report.run_name}.json").read_text(encoding="utf-8"))
    assert detail["cases"][0]["extraction"]["total"] == cases[0].expected["total"]


def test_wrong_answers_show_up_in_scores_and_report(cases, settings, tmp_path):
    wrong = {**cases[0].expected, "invoice_number": "MADE-UP-1", "due_date": None}
    script = perfect_script(cases[0])[:1] + [
        turn(tool_call("s", "submit_extraction", {**wrong, "confidence": 0.9}))
    ]
    client = FakeOpenAI(script + perfect_script(cases[1]))
    report = run_eval(settings=settings, client=client, cases=cases, results_dir=tmp_path)

    first = report.cases[0].score
    assert first.field_accuracy < 1.0
    assert first.extraction_completeness < 1.0   # due date left blank
    assert first.hallucination_rate > 0.0        # invoice number invented
    assert report.cases[1].score.field_accuracy == 1.0
    text = format_report(report)
    assert "MADE-UP-1" in text and "[hallucinated]" in text


def test_agent_failure_is_scored_not_raised(cases, settings, tmp_path):
    client = FakeOpenAI(
        [prose("hmm")] * settings.agent_max_steps + perfect_script(cases[1])
    )
    report = run_eval(settings=settings, client=client, cases=cases, results_dir=tmp_path)

    assert report.failed_cases == [cases[0].id]
    assert report.metrics["field_accuracy"] == 0.5
    assert "no extraction produced" in format_report(report)


def test_regression_against_the_previous_run_is_detected(cases, settings, tmp_path):
    good = FakeOpenAI([step for case in cases for step in perfect_script(case)])
    first = run_eval(settings=settings, client=good, cases=cases, results_dir=tmp_path)
    assert first.previous is None and first.regressions() == []

    bad = FakeOpenAI([prose("hmm")] * settings.agent_max_steps + perfect_script(cases[1]))
    second = run_eval(settings=settings, client=bad, cases=cases, results_dir=tmp_path)

    assert second.previous["run_name"] == first.run_name
    regressed = " ".join(second.regressions())
    assert "field_accuracy" in regressed and "extraction_completeness" in regressed
    assert "REGRESSION" in format_report(second)


def test_subset_runs_are_not_compared_with_full_runs(cases, settings, tmp_path):
    full = FakeOpenAI([step for case in cases for step in perfect_script(case)])
    run_eval(settings=settings, client=full, cases=cases, results_dir=tmp_path)

    subset = FakeOpenAI([prose("hmm")] * settings.agent_max_steps)
    report = run_eval(settings=settings, client=subset, cases=cases[:1], results_dir=tmp_path)
    assert report.previous is None


def test_record_false_leaves_no_files(cases, settings, tmp_path):
    client = FakeOpenAI([step for case in cases for step in perfect_script(case)])
    run_eval(settings=settings, client=client, cases=cases, results_dir=tmp_path, record=False)
    assert list(tmp_path.iterdir()) == []


def test_cli_refuses_to_run_without_an_openai_key(monkeypatch, settings, capsys):
    keyless = settings.model_copy(update={"openai_api_key": ""})
    monkeypatch.setattr("evals.run_eval.get_settings", lambda: keyless)
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
