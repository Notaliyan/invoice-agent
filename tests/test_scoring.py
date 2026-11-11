"""The scorer is the measuring instrument, so it gets its own tests: a metric
that is subtly wrong makes every eval run after it meaningless."""

from __future__ import annotations

import copy

import pytest

from evals.scoring import DocumentIndex, aggregate, score_case
from tests.conftest import INVOICE_TEXT, VALID_SUBMISSION

EXPECTED = {k: v for k, v in VALID_SUBMISSION.items() if k != "confidence"}


def predicted(**changes):
    out = copy.deepcopy(VALID_SUBMISSION)
    out.update(changes)
    return out


def test_perfect_extraction_scores_perfectly():
    score = score_case(EXPECTED, predicted(), INVOICE_TEXT)
    assert score.field_accuracy == 1.0
    assert score.extraction_completeness == 1.0
    assert score.hallucination_rate == 0.0
    assert score.summary() == "all fields correct"


def test_formatting_differences_are_not_errors():
    score = score_case(
        EXPECTED,
        predicted(
            vendor={"name": "NORDLICHT SYSTEME GMBH", "tax_id": "DE 812 345 678"},
            invoice_number="nls-2024-0417",
            currency="eur",
            total=4967.0600001,
        ),
        INVOICE_TEXT,
    )
    assert score.field_accuracy == 1.0


def test_misread_costs_accuracy_but_is_not_a_hallucination():
    # 4174.00 is in the document - it is the subtotal, not the total.
    score = score_case(EXPECTED, predicted(total=4174.00), INVOICE_TEXT)
    total = next(f for f in score.fields if f.field == "total")
    assert total.correct is False
    assert total.hallucinated is False
    assert score.field_accuracy < 1.0
    assert score.hallucination_rate == 0.0


def test_value_not_in_document_is_a_hallucination():
    score = score_case(EXPECTED, predicted(total=5000.00), INVOICE_TEXT)
    total = next(f for f in score.fields if f.field == "total")
    assert total.hallucinated is True
    assert score.hallucination_rate > 0.0
    assert "[hallucinated]" in score.summary()


def test_filling_a_field_the_document_does_not_state_is_a_hallucination():
    expected = {**EXPECTED, "due_date": None}
    # 2024-04-13 really is in the text, but the label says there is no due date.
    score = score_case(expected, predicted(), INVOICE_TEXT)
    due = next(f for f in score.fields if f.field == "due_date")
    assert due.correct is False and due.hallucinated is True


def test_null_for_a_missing_field_is_correct():
    expected = {**EXPECTED, "due_date": None, "customer": None}
    score = score_case(expected, predicted(due_date=None, customer=None), INVOICE_TEXT)
    assert score.field_accuracy == 1.0
    assert score.extraction_completeness == 1.0


def test_blank_fields_cost_completeness_not_hallucination():
    score = score_case(
        EXPECTED, predicted(invoice_number=None, due_date=None, customer=None), INVOICE_TEXT
    )
    assert score.extraction_completeness < 1.0
    assert score.hallucination_rate == 0.0
    assert score.field_accuracy < 1.0


def test_missing_line_item_lowers_accuracy_and_completeness():
    score = score_case(
        EXPECTED, predicted(line_items=VALID_SUBMISSION["line_items"][:2]), INVOICE_TEXT
    )
    assert score.field_accuracy == pytest.approx(14 / 15, abs=1e-3)
    assert score.extraction_completeness == pytest.approx(14 / 15, abs=1e-3)
    assert score.hallucination_rate == 0.0


def test_invented_line_item_is_a_hallucination():
    lines = VALID_SUBMISSION["line_items"] + [
        {"description": "Express handling", "quantity": 1, "unit_price": 77.77, "amount": 77.77}
    ]
    score = score_case(EXPECTED, predicted(line_items=lines), INVOICE_TEXT)
    assert score.field_accuracy == pytest.approx(15 / 16, abs=1e-3)
    assert score.hallucination_rate > 0.0


def test_line_item_with_wrong_quantity_does_not_match():
    lines = copy.deepcopy(VALID_SUBMISSION["line_items"])
    lines[0]["quantity"] = 21
    score = score_case(EXPECTED, predicted(line_items=lines), INVOICE_TEXT)
    assert score.field_accuracy == pytest.approx(14 / 15, abs=1e-3)
    # The amount is still a real number from the document: a misread.
    assert score.hallucination_rate == 0.0


def test_line_item_order_does_not_matter():
    lines = list(reversed(VALID_SUBMISSION["line_items"]))
    assert score_case(EXPECTED, predicted(line_items=lines), INVOICE_TEXT).field_accuracy == 1.0


def test_failed_run_scores_zero_and_keeps_the_error():
    score = score_case(EXPECTED, None, INVOICE_TEXT, error="ran out of steps")
    assert score.field_accuracy == 0.0
    assert score.extraction_completeness == 0.0
    assert "ran out of steps" in score.summary()


def test_aggregate_is_the_mean_over_cases():
    good = score_case(EXPECTED, predicted(), INVOICE_TEXT)
    failed = score_case(EXPECTED, None, INVOICE_TEXT)
    assert aggregate([good, failed])["field_accuracy"] == 0.5


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("Gesamtbetrag 4.967,06 EUR", 4967.06),
        ("Total Due $1,813.14", 1813.14),
        ("Total CHF 6'669.77", 6669.77),
        ("Total HT 1 844,60 €", 1844.60),
        ("MwSt 19%", 19.0),
        ("Menge 1.000", 1000.0),
    ],
)
def test_document_index_reads_every_number_format(text, number):
    assert DocumentIndex(text).contains("number", number)


def test_document_index_does_not_merge_table_columns():
    index = DocumentIndex("1    Sensor gateway SG-40    12   184,50   2.214,00")
    assert not index.contains("number", 12184.50)


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("Rechnungsdatum: 14.03.2024", "2024-03-14"),
        ("Invoice Date: 03/04/2024", "2024-03-04"),
        ("Invoice Date: 03/04/2024", "2024-04-03"),  # ambiguous: both are grounded
        ("Invoice date: 5 March 2024", "2024-03-05"),
        ("Invoice Date: September 12, 2024", "2024-09-12"),
        ("Date: 2 Nov 2024", "2024-11-02"),
        ("Date: 2024-12-03", "2024-12-03"),
    ],
)
def test_document_index_reads_dates(text, iso):
    assert DocumentIndex(text).contains("date", iso)
