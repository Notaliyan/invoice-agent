"""The eval as a pytest suite: a quality gate you can put in CI.

    pytest evals                 # live: calls OpenAI, ~12 documents

This is NOT part of the default `pytest` run (testpaths = tests), because it
costs money and needs a key. It is skipped when OPENAI_API_KEY is absent.

The whole dataset is run once per session - recorded to Langfuse and to
evals/results/history.jsonl exactly like `python -m evals.run_eval` - and then
each test asserts on a slice of that one run.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from evals.dataset import load_cases
from evals.run_eval import EvalReport, format_report, run_eval

pytestmark = pytest.mark.skipif(
    not get_settings().openai_api_key,
    reason="live eval: set OPENAI_API_KEY to run",
)

# Starting floors, not measured ones: tighten them to just under your first
# real run so the gate catches regressions instead of merely catastrophes.
AGGREGATE_FLOORS = {
    "field_accuracy": 0.85,
    "extraction_completeness": 0.90,
}
MAX_HALLUCINATION_RATE = 0.05

# No single document may fall apart, even if the average looks fine.
PER_CASE_MIN_ACCURACY = 0.60
PER_CASE_MAX_HALLUCINATION = 0.20

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
