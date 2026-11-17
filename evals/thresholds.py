"""The quality bar an eval run has to clear.

One place for the numbers, used by both gates: `python -m evals.run_eval --gate`
(what CI runs) and `pytest evals`.

Set just under the measured runs in evals/results/history.jsonl (prompt v2 on
gemini-3.5-flash-lite: accuracy 0.994, completeness 1.000, hallucination 0.012,
the same in three runs). The margins allow for run-to-run noise; re-measure
before tightening, and after switching model.
"""

from __future__ import annotations

from typing import Any

AGGREGATE_FLOORS = {
    "field_accuracy": 0.95,
    "extraction_completeness": 0.95,
}
MAX_HALLUCINATION_RATE = 0.03

# No single document may fall apart, even if the average looks fine. The worst
# measured case scores 0.93 accuracy and 0.14 hallucination.
PER_CASE_MIN_ACCURACY = 0.85
PER_CASE_MAX_HALLUCINATION = 0.20


def violations(report: Any) -> list[str]:
    """Every way an eval report misses the bar; empty means it passes."""
    out: list[str] = []
    for metric, floor in AGGREGATE_FLOORS.items():
        value = report.metrics[metric]
        if value < floor:
            out.append(f"{metric} {value:.3f} is below the floor of {floor:.2f}")

    rate = report.metrics["hallucination_rate"]
    if rate > MAX_HALLUCINATION_RATE:
        out.append(
            f"hallucination_rate {rate:.3f} is above the limit of {MAX_HALLUCINATION_RATE:.2f}"
        )

    for case in report.cases:
        if case.extraction is None:
            out.append(f"{case.case_id}: no extraction produced")
            continue
        if case.score.field_accuracy < PER_CASE_MIN_ACCURACY:
            out.append(
                f"{case.case_id}: accuracy {case.score.field_accuracy:.2f} is below "
                f"{PER_CASE_MIN_ACCURACY:.2f}"
            )
        if case.score.hallucination_rate > PER_CASE_MAX_HALLUCINATION:
            out.append(
                f"{case.case_id}: hallucination rate {case.score.hallucination_rate:.2f} "
                f"is above {PER_CASE_MAX_HALLUCINATION:.2f}"
            )
    return out
