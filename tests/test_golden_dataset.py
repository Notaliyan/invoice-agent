"""Checks on the golden dataset itself.

A wrong label is worse than no label: the eval would then punish the agent for
being right. So every label is machine-checked against its own document.
"""

from __future__ import annotations

import pytest

from app.agent.tools import ToolContext, tool_validate_amounts
from app.schemas import ExtractionResult
from evals.dataset import GoldenCase, load_cases
from evals.scoring import SCALAR_FIELDS, DocumentIndex, _get, score_case

CASES = load_cases()


def by_id(case: GoldenCase) -> str:
    return case.id


def test_dataset_has_the_promised_size_and_variety():
    assert 10 <= len(CASES) <= 15
    assert len({c.id for c in CASES}) == len(CASES)
    tags = {t for c in CASES for t in c.tags}
    assert {"invoice", "receipt", "purchase_order"} <= tags
    assert len({c.expected["currency"] for c in CASES}) >= 4
    assert any(len(c.pages) > 1 for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=by_id)
def test_label_is_a_valid_extraction(case: GoldenCase):
    ExtractionResult.model_validate({**case.expected, "confidence": 1.0})


@pytest.mark.parametrize("case", CASES, ids=by_id)
def test_label_scores_perfectly_against_itself(case: GoldenCase):
    score = score_case(case.expected, case.expected, case.text)
    assert score.field_accuracy == 1.0, score.summary()
    assert score.extraction_completeness == 1.0
    assert score.hallucination_rate == 0.0


@pytest.mark.parametrize("case", CASES, ids=by_id)
def test_every_labelled_value_appears_in_the_document(case: GoldenCase):
    """Catches typos in labels: a labelled number, date or id that is not in
    the text is a labelling mistake."""
    index = DocumentIndex(case.text)
    for path, kind in SCALAR_FIELDS.items():
        value = _get(case.expected, path)
        if value is None or kind == "enum":
            continue
        assert index.contains(kind, value), f"{path}={value!r} not found in {case.id}.txt"
    for line in case.expected["line_items"]:
        assert index.contains("name", line["description"]), line["description"]
        for key in ("quantity", "unit_price", "amount"):
            assert index.contains("number", line[key]), f"{key}={line[key]} ({line})"


@pytest.mark.parametrize("case", CASES, ids=by_id)
def test_label_arithmetic_is_consistent(case: GoldenCase):
    """Labels must pass the same arithmetic gate the agent has to pass - except
    the cases whose whole point is that the document's own maths is wrong."""
    expected = case.expected
    report = tool_validate_amounts(
        ToolContext(doc=case.document()),
        total=expected["total"],
        line_items=expected["line_items"],
        subtotal=expected["subtotal"],
        tax_amount=expected["tax_amount"],
        tax_rate=expected["tax_rate"],
    )
    failed = [c["name"] for c in report["checks"] if not c["passed"]]
    if "arithmetic_mismatch" in case.tags:
        assert failed == ["subtotal_plus_tax_equals_total"]
    else:
        assert not failed, report["checks"]


def test_filename_does_not_leak_the_answer():
    for case in CASES:
        assert case.id not in case.filename
        assert not any(tag in case.filename for tag in case.tags)
