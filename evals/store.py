"""Eval runs in the database.

An eval run is the only time the agent's output can be checked against a known
answer, so these rows are what makes "extraction accuracy over time" a query
rather than a guess. Each golden case becomes one processing run, tagged
source='eval' and carrying its three scores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app import db
from app.schemas import AgentTrace, ExtractionResult
from evals.dataset import GoldenCase, load_cases


@dataclass
class Capture:
    """What a run knows that the scored output does not."""

    trace: AgentTrace | None
    duration_ms: int | None


def _store_case(
    session: Any,
    case: GoldenCase,
    *,
    run_name: str,
    model: str,
    extraction: dict[str, Any] | None,
    error: str | None,
    validation_passed: bool | None,
    scores: dict[str, float],
    capture: Capture,
    started_at: datetime | None = None,
) -> None:
    eval_scores = db.EvalScores(
        run_name=run_name,
        field_accuracy=scores["field_accuracy"],
        extraction_completeness=scores["extraction_completeness"],
        hallucination_rate=scores["hallucination_rate"],
    )
    if extraction is not None and capture.trace is not None:
        db.record_success(
            session,
            case.document(),
            ExtractionResult.model_validate(extraction),
            trace=capture.trace,
            validation_passed=validation_passed,
            duration_ms=capture.duration_ms,
            source="eval",
            scores=eval_scores,
            started_at=started_at,
        )
    else:
        db.record_failure(
            session,
            case.document(),
            error=error or "no extraction produced",
            model=model,
            trace=capture.trace,
            duration_ms=capture.duration_ms,
            source="eval",
            scores=eval_scores,
            started_at=started_at,
        )


def store_report(
    database_url: str, report: Any, cases: list[GoldenCase], captures: dict[str, Capture]
) -> int:
    """Store one eval run. Cases the provider failed on are skipped: they say
    nothing about the agent. Returns the number of runs written."""
    by_id = {case.id: case for case in cases}
    stored = 0
    with db.session_scope(database_url) as session:
        for result in report.cases:
            if result.crashed:
                continue
            _store_case(
                session,
                by_id[result.case_id],
                run_name=report.run_name,
                model=report.model,
                extraction=result.extraction,
                error=result.error,
                validation_passed=result.validation_passed,
                scores={
                    "field_accuracy": result.score.field_accuracy,
                    "extraction_completeness": result.score.extraction_completeness,
                    "hallucination_rate": result.score.hallucination_rate,
                },
                capture=captures.get(result.case_id, Capture(trace=None, duration_ms=None)),
            )
            stored += 1
    return stored


def import_history(database_url: str, results_dir: Path) -> dict[str, int]:
    """Load past eval runs from their detail files (evals/results/<run>.json).

    Only runs listed in history.jsonl are imported, which leaves out incomplete
    ones. Runs already in the database are skipped, so this is safe to repeat.
    Timing and token counts were not saved in those files and stay empty.
    """
    history = results_dir / "history.jsonl"
    if not history.exists():
        return {}
    recorded = [
        json.loads(line)["run_name"]
        for line in history.read_text(encoding="utf-8").splitlines()
        if line
    ]
    by_id = {case.id: case for case in load_cases()}
    imported: dict[str, int] = {}

    with db.session_scope(database_url) as session:
        for run_name in recorded:
            detail_path = results_dir / f"{run_name}.json"
            if not detail_path.exists():
                continue
            already = session.scalar(
                select(db.ProcessingRun.id)
                .where(db.ProcessingRun.eval_run_name == run_name)
                .limit(1)
            )
            if already is not None:
                continue

            detail = json.loads(detail_path.read_text(encoding="utf-8"))
            started_at = datetime.fromisoformat(detail["timestamp"])
            count = 0
            for entry in detail["cases"]:
                case = by_id.get(entry["case_id"])
                if case is None:  # a golden case that has since been removed
                    continue
                produced = entry["extraction"] is not None
                trace = AgentTrace(
                    model=detail["model"],
                    prompt_version=detail["prompt_version"],
                    langfuse_trace_id=entry.get("trace_id"),
                    steps=entry.get("steps") or 0,
                    stop_reason="submitted" if produced else "step_budget_exhausted",
                )
                _store_case(
                    session,
                    case,
                    run_name=run_name,
                    model=detail["model"],
                    extraction=entry["extraction"],
                    error=entry.get("error"),
                    validation_passed=entry.get("validation_passed"),
                    scores=entry["score"],
                    capture=Capture(trace=trace, duration_ms=None),
                    started_at=started_at,
                )
                count += 1
            imported[run_name] = count
    return imported
