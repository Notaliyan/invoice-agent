"""The number and date parsers. These are where locale bugs hide."""

from __future__ import annotations

import pytest

from app.agent.tools import parse_amount, parse_date_candidates


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.234,56", 1234.56),      # German
        ("1,234.56", 1234.56),      # US
        ("4.967,06", 4967.06),
        ("1 234.56", 1234.56),      # thin-space grouping
        ("99", 99.0),
        ("99,50", 99.50),           # single comma, 2 decimals -> decimal mark
        ("1,234", 1234.0),          # single comma, 3 digits -> thousands
        ("-42.00", -42.0),
        ("EUR 4.174,00", 4174.00),
        ("$1,671.10", 1671.10),
        ("", None),
        ("n/a", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_unambiguous_date_has_one_reading():
    assert parse_date_candidates("2024-03-14") == ["2024-03-14"]


def test_day_first_date_beyond_twelve_is_unambiguous():
    # 14 cannot be a month, so there is only one valid reading.
    assert parse_date_candidates("14.03.2024") == ["2024-03-14"]


def test_ambiguous_date_returns_both_readings():
    candidates = parse_date_candidates("03/04/2024")
    assert set(candidates) == {"2024-04-03", "2024-03-04"}


def test_written_month_is_parsed():
    assert "2024-03-14" in parse_date_candidates("March 14, 2024")
    assert "2024-03-14" in parse_date_candidates("14 March 2024")


def test_nonsense_date_yields_nothing():
    assert parse_date_candidates("99/99/9999") == []



@pytest.mark.parametrize("text", ["null", "None", "N/A", "  ", "unknown", "-"])
def test_no_value_written_as_text_becomes_empty(text):
    from app.schemas import ExtractionResult
    from tests.conftest import VALID_SUBMISSION

    result = ExtractionResult.model_validate({
        **VALID_SUBMISSION,
        "vendor": {"name": "Acme", "tax_id": text, "email": text},
        "customer": {"name": text},
        "invoice_number": text,
        "due_date": text,
    })
    assert result.vendor.tax_id is None and result.vendor.email is None
    assert result.customer is None
    assert result.invoice_number is None and result.due_date is None


def test_real_values_are_left_alone():
    from app.schemas import ExtractionResult
    from tests.conftest import VALID_SUBMISSION

    result = ExtractionResult.model_validate({
        **VALID_SUBMISSION,
        "vendor": {"name": "None Such Ltd", "tax_id": "GB123"},
        "invoice_number": "NA-2024-1",
    })
    assert result.vendor.name == "None Such Ltd"
    assert result.vendor.tax_id == "GB123"
    assert result.invoice_number == "NA-2024-1"
