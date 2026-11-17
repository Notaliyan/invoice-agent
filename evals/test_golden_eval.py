"""The eval as a pytest suite: a quality gate you can run by hand.

    pytest evals                 # live: calls the model, ~12 documents

This is NOT part of the default `pytest` run (testpaths = tests), because it
needs a key and makes ~50 model calls. It is skipped when OPENAI_API_KEY is
absent. CI runs the same gate through `python -m evals.run_eval --gate`.

The whole dataset is run once per session - recorded to Langfuse and to
evals/results/history.jsonl exactly like `python -m evals.run_eval` - and then
each test asserts on a slice of that one run. The numbers live in
evals/thresholds.py.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from evals.dataset import load_cases
from evals.run_eval import EvalReport, format_report, run_eval
from evals.thresholds import (
    AGGREGATE_FLOORS,
    MAX_HALLUCINATION_RATE,
    PER_CASE_MAX_HALLUCINATION,
    PER_CASE_MIN_ACCURACY,
)

pytestmark = pytest.mark.skipif(
    not get_settings().openai_api_key,
    reason="live eval: set OPENAI_API_KEY to run",
)

CASE_IDS = [case.id for case in load_cases()]


@pytest.fixture(scope="session")
def report() -> EvalReport:
    result = run_eval()
    print("\n" + format_report(result))
    return result


@pytest.mark.parametrize("metric", sorted(AGGREGATE_FLOORS))
def test_aggregate_quality(report: EvalReport, metric: str) -> None:
    assert report.metrics[metric] >= AGGREGATE_FLOORS[metric], format_report(report)


def test_aggregate_hallucination_rate(report: EvalReport) -> None:
    assert report.metrics["hallucination_rate"] <= MAX_HALLUCINATION_RATE, format_report(report)


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case(report: EvalReport, case_id: str) -> None:
    case = next(c for c in report.cases if c.case_id == case_id)
    assert case.extraction is not None, f"agent produced no extraction: {case.error}"
    assert case.score.field_accuracy >= PER_CASE_MIN_ACCURACY, case.score.summary()
    assert case.score.hallucination_rate <= PER_CASE_MAX_HALLUCINATION, case.score.summary()


def test_no_regression_against_previous_run(report: EvalReport) -> None:
    assert not report.regressions(), "\n".join(report.regressions())
