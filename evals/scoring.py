"""Score one extraction against its golden label.

Three metrics, all in [0, 1], all deterministic (no LLM judge - the task has
exact right answers, so string and number comparison is the honest tool):

field_accuracy
    Of the scored fields, the fraction the agent got right. A field the document
    does not state is right only if the agent returned null for it.

extraction_completeness
    Of the fields the document *does* state, the fraction the agent returned
    anything for. This is recall of presence, independent of correctness: it
    separates "left it blank" from "got it wrong".

hallucination_rate   (lower is better)
    Of the values the agent returned, the fraction it made up. A value is
    hallucinated when it is wrong AND either
      - the label says the document does not state that field at all, or
      - the value appears nowhere in the document text.
    A wrong value that does appear in the document (picked the PO reference as
    the invoice number, say) is a misread: it costs accuracy, not this metric.

Scalar fields count once each. Each line item counts once, as a unit: it is
right if some extracted line matches its amount, quantity and unit price.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.agent.tools import parse_amount, parse_date_candidates

# field path -> how to compare it
SCALAR_FIELDS: dict[str, str] = {
    "document_type": "enum",
    "vendor.name": "name",
    "vendor.tax_id": "id",
    "customer.name": "name",
    "invoice_number": "id",
    "issue_date": "date",
    "due_date": "date",
    "currency": "enum",
    "subtotal": "number",
    "tax_amount": "number",
    "tax_rate": "number",
    "total": "number",
}

# Classifications the agent infers rather than copies ("$" -> USD). They are
# scored for accuracy but can never be "not in the document", so they stay out
# of the hallucination metric.
INFERRED_FIELDS = {"document_type", "currency"}

METRICS = ("field_accuracy", "extraction_completeness", "hallucination_rate")
LOWER_IS_BETTER = {"hallucination_rate"}

NUMBER_TOLERANCE = 0.011


@dataclass
class FieldResult:
    field: str
    expected: Any
    predicted: Any
    correct: bool
    hallucinated: bool = False


@dataclass
class CaseScore:
    field_accuracy: float
    extraction_completeness: float
    hallucination_rate: float
    fields: list[FieldResult] = field(default_factory=list)
    error: str | None = None

    @property
    def mistakes(self) -> list[FieldResult]:
        return [f for f in self.fields if not f.correct]

    def summary(self) -> str:
        """One line per mistake; this becomes the score comment in Langfuse."""
        if self.error:
            return f"agent failed: {self.error}"
        if not self.mistakes:
            return "all fields correct"
        return "; ".join(
            f"{m.field}: expected {m.expected!r}, got {m.predicted!r}"
            + (" [hallucinated]" if m.hallucinated else "")
            for m in self.mistakes
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# normalisation and comparison
# ---------------------------------------------------------------------------


def _alnum(value: Any) -> str:
    return "".join(ch for ch in str(value).casefold() if ch.isalnum())


def _name(value: Any) -> str:
    text = str(value).casefold().replace("&", " and ")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _matches(kind: str, expected: Any, predicted: Any) -> bool:
    if expected is None or predicted is None:
        return expected is None and predicted is None
    if kind == "number":
        a, b = _number(expected), _number(predicted)
        return a is not None and b is not None and abs(a - b) <= NUMBER_TOLERANCE
    if kind == "date":
        return str(expected)[:10] == str(predicted)[:10]
    if kind == "id":
        return _alnum(expected) == _alnum(predicted)
    if kind == "name":
        return _name(expected) == _name(predicted)
    return str(expected).strip().casefold() == str(predicted).strip().casefold()


# ---------------------------------------------------------------------------
# grounding: does a value appear in the document at all?
# ---------------------------------------------------------------------------

_NUMBER_TOKEN = re.compile(r"\d+(?:[.,'’]\d+)*")
_GROUPED_INT = re.compile(r"\d{1,3}(?:[.,]\d{3})+")
# French / SI style: 1 260,00 (plain, non-breaking or narrow space).
_SPACE_GROUPED = re.compile(r"(?<![\d.,])\d{1,3}(?:[   ]\d{3})+(?:[.,]\d{1,2})?(?![\d])")
_DATE_TOKEN = re.compile(
    r"\b("
    r"\d{4}[./-]\d{1,2}[./-]\d{1,2}"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
    r"|\d{1,2}\.?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"
    r")\b"
)


class DocumentIndex:
    """Everything in a document that a value could legitimately be copied from."""

    def __init__(self, text: str) -> None:
        self.alnum = _alnum(text)
        self.numbers: set[float] = set()
        self.dates: set[str] = set()

        for match in _NUMBER_TOKEN.finditer(text):
            token = match.group().replace("'", "").replace("’", "")
            value = parse_amount(token)
            if value is not None:
                self.numbers.add(value)
            # "1.234" is one-point-two or twelve hundred; both are "in the document".
            if _GROUPED_INT.fullmatch(token):
                self.numbers.add(float(re.sub(r"[.,]", "", token)))
        for match in _SPACE_GROUPED.finditer(text):
            value = parse_amount(re.sub(r"[   ]", "", match.group()))
            if value is not None:
                self.numbers.add(value)

        for match in _DATE_TOKEN.finditer(text):
            self.dates.update(parse_date_candidates(match.group()))

    def contains(self, kind: str, value: Any) -> bool:
        if kind == "number":
            number = _number(value)
            return number is not None and any(
                abs(number - n) <= NUMBER_TOLERANCE for n in self.numbers
            )
        if kind == "date":
            return str(value)[:10] in self.dates
        needle = _alnum(value)
        return bool(needle) and needle in self.alnum


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def _get(record: dict[str, Any] | None, path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _line_matches(expected: dict[str, Any], predicted: dict[str, Any]) -> bool:
    if not _matches("number", expected.get("amount"), predicted.get("amount")):
        return False
    for key in ("quantity", "unit_price"):
        if expected.get(key) is not None and not _matches(
            "number", expected.get(key), predicted.get(key)
        ):
            return False
    return True


def _ratio(numerator: int, denominator: int, empty: float) -> float:
    return round(numerator / denominator, 4) if denominator else empty


def score_case(
    expected: dict[str, Any],
    predicted: dict[str, Any] | None,
    document_text: str,
    error: str | None = None,
) -> CaseScore:
    """Compare `predicted` (an ExtractionResult as a dict, or None if the agent
    failed) with the golden `expected` label for the same document."""
    if predicted is None:
        return CaseScore(
            field_accuracy=0.0,
            extraction_completeness=0.0,
            hallucination_rate=0.0,
            error=error or "no extraction produced",
        )

    index = DocumentIndex(document_text)
    results: list[FieldResult] = []
    stated = present = returned = hallucinated = 0

    for path, kind in SCALAR_FIELDS.items():
        exp, pred = _get(expected, path), _get(predicted, path)
        correct = _matches(kind, exp, pred)
        made_up = (
            not correct
            and pred is not None
            and path not in INFERRED_FIELDS
            and (exp is None or not index.contains(kind, pred))
        )
        results.append(FieldResult(path, exp, pred, correct, made_up))

        if exp is not None:
            stated += 1
            present += pred is not None
        if pred is not None and path not in INFERRED_FIELDS:
            returned += 1
            hallucinated += made_up

    expected_lines = list(expected.get("line_items") or [])
    predicted_lines = list(predicted.get("line_items") or [])
    unmatched = list(range(len(predicted_lines)))

    for i, exp_line in enumerate(expected_lines):
        hit = next((j for j in unmatched if _line_matches(exp_line, predicted_lines[j])), None)
        if hit is not None:
            unmatched.remove(hit)
        results.append(
            FieldResult(
                f"line_items[{i}]",
                exp_line,
                predicted_lines[hit] if hit is not None else None,
                hit is not None,
            )
        )

    # Extracted lines that match nothing in the label: misread if the amount at
    # least appears in the document, invented if it does not.
    for n, j in enumerate(unmatched):
        line = predicted_lines[j]
        made_up = not index.contains("number", line.get("amount"))
        hallucinated += made_up
        results.append(FieldResult(f"line_items[unmatched {n}]", None, line, False, made_up))

    stated += len(expected_lines)
    present += min(len(expected_lines), len(predicted_lines))
    returned += len(predicted_lines)

    # Accuracy denominator: every scalar field, plus one per line item on
    # whichever side has more of them (so padding with junk lines costs).
    line_slots = max(len(expected_lines), len(predicted_lines))
    correct_scalars = sum(1 for r in results if r.correct and "line_items" not in r.field)
    correct_lines = len(predicted_lines) - len(unmatched)

    return CaseScore(
        field_accuracy=_ratio(
            correct_scalars + correct_lines, len(SCALAR_FIELDS) + line_slots, 1.0
        ),
        extraction_completeness=_ratio(present, stated, 1.0),
        hallucination_rate=_ratio(hallucinated, returned, 0.0),
        fields=results,
        error=error,
    )


def aggregate(scores: list[CaseScore]) -> dict[str, float]:
    """Mean of each metric across cases. A failed case counts as zeros."""
    if not scores:
        return {m: 0.0 for m in METRICS}
    return {m: round(sum(getattr(s, m) for s in scores) / len(scores), 4) for m in METRICS}
