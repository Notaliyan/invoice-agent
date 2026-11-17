"""Run the agent over the golden dataset and score it.

    python -m evals.run_eval                      # full run
    python -m evals.run_eval --case multipage     # only cases whose id matches
    python -m evals.run_eval --fail-on-regression # exit 1 if worse than last run
    python -m evals.run_eval --sync-only          # just upload the dataset

Needs OPENAI_API_KEY. With LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY also set,
the run is recorded in Langfuse as an experiment on the `invoice-golden`
dataset: one trace per document (every model call and tool call inside it),
three scores on each trace, and the averages on the run. Without them it runs
locally and prints the same numbers.

Either way a line is appended to evals/results/history.jsonl, so quality over
time is also in the repo, next to the prompt change that caused it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import OpenAIError

from app.agent.prompts import PROMPT_FINGERPRINT, PROMPT_VERSION
from app.agent.runner import AgentError, ExtractionAgent
from app.config import Settings, get_settings
from app.observability import get_langfuse
from evals.dataset import GoldenCase, load_cases
from evals.scoring import LOWER_IS_BETTER, METRICS, CaseScore, aggregate, score_case
from evals.store import Capture, import_history, store_report

DATASET_NAME = "invoice-golden"
EXPERIMENT_NAME = "invoice-extraction"
RESULTS_DIR = Path(__file__).parent / "results"
HISTORY_FILE = "history.jsonl"

# A metric has to move by more than this to count as a regression. At
# temperature 0 the agent is close to deterministic but not perfectly so.
DEFAULT_TOLERANCE = 0.02


@dataclass
class CaseResult:
    case_id: str
    score: CaseScore
    extraction: dict[str, Any] | None
    error: str | None = None
    validation_passed: bool | None = None
    steps: int = 0
    trace_id: str | None = None
    # True when the model provider failed (rate limit, outage, bad key) rather
    # than the agent. Says nothing about extraction quality.
    crashed: bool = False


@dataclass
class EvalReport:
    run_name: str
    timestamp: str
    model: str
    prompt_version: str
    prompt_fingerprint: str
    metrics: dict[str, float]
    cases: list[CaseResult] = field(default_factory=list)
    langfuse_url: str | None = None
    previous: dict[str, Any] | None = None

    @property
    def failed_cases(self) -> list[str]:
        return [c.case_id for c in self.cases if c.extraction is None]

    @property
    def crashed_cases(self) -> list[str]:
        return [c.case_id for c in self.cases if c.crashed]

    def history_entry(self) -> dict[str, Any]:
        return {
            "run_name": self.run_name,
            "timestamp": self.timestamp,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "prompt_fingerprint": self.prompt_fingerprint,
            "n_cases": len(self.cases),
            "failed_cases": self.failed_cases,
            "metrics": self.metrics,
            "per_case": {c.case_id: {m: getattr(c.score, m) for m in METRICS} for c in self.cases},
            "langfuse_url": self.langfuse_url,
        }

    def regressions(self, tolerance: float = DEFAULT_TOLERANCE) -> list[str]:
        """Metrics that got worse than the previous comparable run."""
        if not self.previous:
            return []
        out: list[str] = []
        for metric in METRICS:
            before = self.previous["metrics"].get(metric)
            if before is None:
                continue
            delta = self.metrics[metric] - before
            worse = delta > tolerance if metric in LOWER_IS_BETTER else delta < -tolerance
            if worse:
                out.append(
                    f"{metric}: {before:.3f} -> {self.metrics[metric]:.3f} "
                    f"(vs {self.previous['run_name']})"
                )
        return out


# ---------------------------------------------------------------------------
# running one case
# ---------------------------------------------------------------------------


def run_case(
    agent: ExtractionAgent,
    case: GoldenCase,
    captures: dict[str, Capture] | None = None,
) -> dict[str, Any]:
    """Run the agent on one golden document. Never raises for an agent failure:
    a failed extraction is a result to be scored, not a crash. A provider
    failure is reported too, but flagged so the run is not mistaken for a
    quality measurement."""
    started = time.perf_counter()

    def capture(trace: Any) -> None:
        # Kept out of the returned output, which is what Langfuse stores and
        # scores; the database wants the timing and the full trace as well.
        if captures is not None:
            captures[case.id] = Capture(
                trace=trace, duration_ms=int((time.perf_counter() - started) * 1000)
            )

    try:
        result = agent.run(case.document())
    except OpenAIError as exc:
        return {
            "extraction": None,
            "error": f"provider error: {type(exc).__name__}: {str(exc)[:200]}",
            "validation_passed": None,
            "steps": 0,
            "crashed": True,
        }
    except AgentError as exc:
        capture(exc.trace)
        return {
            "extraction": None,
            "error": str(exc),
            "validation_passed": None,
            "steps": exc.trace.steps if exc.trace else 0,
        }
    capture(result.trace)
    return {
        "extraction": result.extraction.model_dump(mode="json"),
        "error": None,
        "validation_passed": result.validation.passed,
        "steps": result.trace.steps,
    }


def _case_result(case: GoldenCase, output: dict[str, Any], trace_id: str | None) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        score=score_case(case.expected, output["extraction"], case.text, output["error"]),
        extraction=output["extraction"],
        error=output["error"],
        validation_passed=output["validation_passed"],
        steps=output["steps"],
        trace_id=trace_id,
        crashed=bool(output.get("crashed")),
    )


def _run_locally(
    agent: ExtractionAgent, cases: list[GoldenCase], captures: dict[str, Capture]
) -> list[CaseResult]:
    return [_case_result(case, run_case(agent, case, captures), None) for case in cases]


# ---------------------------------------------------------------------------
# Langfuse: dataset + experiment
# ---------------------------------------------------------------------------


def sync_dataset(langfuse: Any, cases: list[GoldenCase]) -> None:
    """Upsert the golden cases into the Langfuse dataset. Item ids are derived
    from case ids, so re-running updates items in place instead of duplicating."""
    langfuse.create_dataset(
        name=DATASET_NAME,
        description="Hand-labelled documents for the extraction agent (evals/golden).",
    )
    for case in cases:
        langfuse.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=f"{DATASET_NAME}-{case.id}",
            input={"filename": case.filename, "pages": case.pages},
            expected_output=case.expected,
            metadata={
                "case_id": case.id,
                "description": case.description,
                "tags": case.tags,
                "source": case.source,
            },
        )


def _case_id(item: Any) -> str | None:
    metadata = item.get("metadata") if isinstance(item, dict) else item.metadata
    return (metadata or {}).get("case_id")


def _run_with_langfuse(
    langfuse: Any,
    agent: ExtractionAgent,
    cases: list[GoldenCase],
    run_name: str,
    concurrency: int,
    captures: dict[str, Capture],
) -> tuple[list[CaseResult], str | None]:
    from langfuse import Evaluation

    by_id = {case.id: case for case in cases}
    sync_dataset(langfuse, cases)
    dataset = langfuse.get_dataset(DATASET_NAME)
    # The remote dataset may hold cases that were since deleted locally, or that
    # --case filtered out; only run the ones selected for this run.
    items = [item for item in dataset.items if _case_id(item) in by_id]

    def task(*, item: Any, **_: Any) -> dict[str, Any]:
        return run_case(agent, by_id[_case_id(item)], captures)

    def evaluator(
        *, output: Any, expected_output: Any, metadata: Any, **_: Any
    ) -> list[Evaluation]:
        case = by_id[(metadata or {})["case_id"]]
        score = score_case(expected_output, output["extraction"], case.text, output["error"])
        comment = score.summary()
        return [
            Evaluation(name=metric, value=getattr(score, metric), comment=comment)
            for metric in METRICS
        ]

    def run_evaluator(*, item_results: list[Any], **_: Any) -> list[Evaluation]:
        averages: list[Evaluation] = []
        for metric in METRICS:
            values = [e.value for r in item_results for e in r.evaluations if e.name == metric]
            if values:
                averages.append(
                    Evaluation(
                        name=f"avg_{metric}",
                        value=round(sum(values) / len(values), 4),
                        comment=f"mean over {len(values)} documents",
                    )
                )
        return averages

    result = langfuse.run_experiment(
        name=EXPERIMENT_NAME,
        run_name=run_name,
        description=(
            f"prompt {PROMPT_VERSION} ({PROMPT_FINGERPRINT}), {agent.settings.openai_model}"
        ),
        data=items,
        task=task,
        evaluators=[evaluator],
        run_evaluators=[run_evaluator],
        max_concurrency=concurrency,
        metadata={
            "prompt_version": PROMPT_VERSION,
            "prompt_fingerprint": PROMPT_FINGERPRINT,
            "model": agent.settings.openai_model,
        },
    )
    langfuse.flush()

    results: dict[str, CaseResult] = {}
    for item_result in result.item_results:
        case = by_id[_case_id(item_result.item)]
        results[case.id] = _case_result(case, item_result.output, item_result.trace_id)
    # Langfuse drops items whose task raised. Keep them visible as failures.
    for case in cases:
        if case.id not in results:
            output = {
                "extraction": None,
                "error": "task crashed; see the Langfuse trace / logs",
                "validation_passed": None,
                "steps": 0,
                "crashed": True,
            }
            results[case.id] = _case_result(case, output, None)

    return [results[case.id] for case in cases], result.experiment_url


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def load_history(results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    path = results_dir / HISTORY_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _previous_comparable(history: list[dict[str, Any]], n_cases: int) -> dict[str, Any] | None:
    """The most recent run over the same number of cases. A --case subset must
    not be compared with (or become the baseline for) a full run."""
    return next((h for h in reversed(history) if h.get("n_cases") == n_cases), None)


def _record(report: EvalReport, results_dir: Path) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    with (results_dir / HISTORY_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report.history_entry(), ensure_ascii=False) + "\n")
    detail = {
        **report.history_entry(),
        "cases": [
            {
                "case_id": c.case_id,
                "trace_id": c.trace_id,
                "steps": c.steps,
                "validation_passed": c.validation_passed,
                "error": c.error,
                "extraction": c.extraction,
                "score": c.score.as_dict(),
            }
            for c in report.cases
        ],
    }
    (results_dir / f"{report.run_name}.json").write_text(
        json.dumps(detail, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run_eval(
    *,
    settings: Settings | None = None,
    client: Any | None = None,
    cases: list[GoldenCase] | None = None,
    use_langfuse: bool = True,
    record: bool = True,
    results_dir: Path = RESULTS_DIR,
    concurrency: int = 4,
    database_url: str | None = None,
) -> EvalReport:
    """Run and score the golden dataset. `client` lets tests pass a fake OpenAI.
    With `database_url`, every case is also stored as a processing run."""
    settings = settings or get_settings()
    cases = cases if cases is not None else load_cases()
    agent = ExtractionAgent(client=client, settings=settings)

    now = datetime.now(UTC)
    run_name = f"{PROMPT_VERSION}-{settings.openai_model}-{now:%Y%m%d-%H%M%S}"

    langfuse = get_langfuse(settings) if use_langfuse else None
    captures: dict[str, Capture] = {}
    if langfuse is not None:
        results, url = _run_with_langfuse(
            langfuse, agent, cases, run_name, concurrency, captures
        )
    else:
        results, url = _run_locally(agent, cases, captures), None

    report = EvalReport(
        run_name=run_name,
        timestamp=now.isoformat(timespec="seconds"),
        model=settings.openai_model,
        prompt_version=PROMPT_VERSION,
        prompt_fingerprint=PROMPT_FINGERPRINT,
        metrics=aggregate([r.score for r in results]),
        cases=results,
        langfuse_url=url,
        previous=_previous_comparable(load_history(results_dir), len(cases)),
    )
    # An incomplete run would become the baseline every later run is compared
    # against, so it is never written to history.
    if record and not report.crashed_cases:
        _record(report, results_dir)
    if database_url:
        store_report(database_url, report, cases, captures)
    return report


def format_report(report: EvalReport, tolerance: float = DEFAULT_TOLERANCE) -> str:
    width = max(len(c.case_id) for c in report.cases)
    lines = [
        f"Run {report.run_name}",
        f"prompt {report.prompt_version} ({report.prompt_fingerprint}), model {report.model}, "
        f"{len(report.cases)} documents",
        "",
        f"{'case':<{width}}  accuracy  complete  halluc.  steps",
    ]
    for c in report.cases:
        lines.append(
            f"{c.case_id:<{width}}  {c.score.field_accuracy:>8.2f}  "
            f"{c.score.extraction_completeness:>8.2f}  {c.score.hallucination_rate:>7.2f}  "
            f"{c.steps:>5}"
        )
        if c.score.mistakes or c.error:
            lines.append(f"{'':<{width}}    {c.score.summary()}")

    lines.append("")
    for metric in METRICS:
        line = f"{metric:<24} {report.metrics[metric]:.3f}"
        if report.previous and metric in report.previous["metrics"]:
            delta = report.metrics[metric] - report.previous["metrics"][metric]
            line += f"   ({delta:+.3f} vs {report.previous['run_name']})"
        lines.append(line)
    if report.failed_cases:
        lines.append(f"no extraction produced for: {', '.join(report.failed_cases)}")
    if report.crashed_cases:
        lines.append(
            f"INCOMPLETE RUN, not recorded: the model provider failed on "
            f"{len(report.crashed_cases)} of {len(report.cases)} documents "
            "(rate limit, outage or bad key). The metrics above are not meaningful."
        )
    if report.langfuse_url:
        lines.append(f"Langfuse: {report.langfuse_url}")
    regressions = report.regressions(tolerance)
    if regressions:
        lines += ["", "REGRESSION:"] + [f"  {r}" for r in regressions]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--case", help="only run cases whose id contains this text")
    parser.add_argument("--no-langfuse", action="store_true", help="score locally only")
    parser.add_argument("--no-record", action="store_true", help="do not append to history")
    parser.add_argument("--sync-only", action="store_true", help="upload the dataset and exit")
    parser.add_argument("--no-store", action="store_true", help="do not write to the database")
    parser.add_argument(
        "--import-history",
        action="store_true",
        help="load past runs from evals/results into the database and exit",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit 1 if any metric is worse than the previous comparable run",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    cases = load_cases()
    if args.case:
        cases = [c for c in cases if args.case in c.id]
        if not cases:
            parser.error(f"no golden case id contains {args.case!r}")

    if args.sync_only:
        langfuse = get_langfuse(settings)
        if langfuse is None:
            parser.error("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set")
        sync_dataset(langfuse, cases)
        langfuse.flush()
        print(f"synced {len(cases)} items to Langfuse dataset {DATASET_NAME!r}")
        return 0

    if args.import_history:
        if not settings.database_url:
            parser.error("DATABASE_URL is empty")
        imported = import_history(settings.database_url, RESULTS_DIR)
        for run_name, count in imported.items():
            print(f"imported {count} runs from {run_name}")
        if not imported:
            print("nothing to import (no saved runs, or all already in the database)")
        return 0

    if not settings.openai_api_key:
        parser.error("OPENAI_API_KEY is not set (put it in .env)")
    if not settings.langfuse_enabled and not args.no_langfuse:
        print("Langfuse keys not set - scoring locally only.\n", file=sys.stderr)

    report = run_eval(
        settings=settings,
        cases=cases,
        use_langfuse=not args.no_langfuse,
        record=not args.no_record,
        concurrency=args.concurrency,
        database_url=None if args.no_store else settings.database_url or None,
    )
    print(format_report(report, args.tolerance))
    if report.crashed_cases:
        return 2
    return 1 if args.fail_on_regression and report.regressions(args.tolerance) else 0


if __name__ == "__main__":
    # Windows consoles default to a legacy code page; the report quotes
    # document text (umlauts, currency symbols).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
