"""The tools themselves - the deterministic half of the system."""

from __future__ import annotations

from app.agent.tools import (
    ToolContext,
    tool_classify_document_type,
    tool_extract_invoice_fields,
    tool_get_page_text,
    tool_search_document,
    tool_submit_extraction,
    tool_validate_amounts,
)
from tests.conftest import VALID_SUBMISSION, VALID_VALIDATION_ARGS

# --------------------------------------------------------------------------
# reading tools
# --------------------------------------------------------------------------


def test_search_returns_page_and_line(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_search_document(ctx, query="Gesamtbetrag")
    assert out["match_count"] == 1
    hit = out["matches"][0]
    assert hit["page"] == 1
    assert "4.967,06" in hit["text"]


def test_search_rejects_bad_regex(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    assert "error" in tool_search_document(ctx, query="(unclosed", is_regex=True)


def test_get_page_text_out_of_range(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    assert "error" in tool_get_page_text(ctx, page=7)


# --------------------------------------------------------------------------
# harvesting
# --------------------------------------------------------------------------


def test_harvester_finds_the_total(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_extract_invoice_fields(ctx, fields=["amounts"])
    values = {a["value"] for a in out["amounts"]}
    assert 4967.06 in values
    assert 4174.0 in values
    assert 793.06 in values


def test_harvester_does_not_merge_table_columns(invoice_doc):
    """'12  184,50  2.214,00' is three columns, not one number 12184.50."""
    ctx = ToolContext(doc=invoice_doc)
    out = tool_extract_invoice_fields(ctx, fields=["amounts"])
    values = {a["value"] for a in out["amounts"]}
    assert 184.5 in values
    assert 2214.0 in values
    assert 12184.5 not in values
    assert 8125.0 not in values


def test_harvester_keeps_the_source_line_for_disambiguation(invoice_doc):
    """Candidates are low-precision by design, so each must carry its context."""
    ctx = ToolContext(doc=invoice_doc)
    out = tool_extract_invoice_fields(ctx, fields=["amounts"])
    total = next(a for a in out["amounts"] if a["value"] == 4967.06)
    assert "Gesamtbetrag" in total["line_text"]
    assert total["page"] == 1
    assert total["currency_hint"] == "EUR"


def test_harvester_finds_identifiers(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_extract_invoice_fields(ctx, fields=["identifiers"])
    ids = out["identifiers"]
    assert any("NLS-2024-0417" in c["value"] for c in ids.get("invoice_number", []))
    assert any("DE812345678" in c["value"] for c in ids.get("tax_id", []))


def test_harvester_flags_currency(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_extract_invoice_fields(ctx, fields=["currency"])
    assert out["currency"]["most_likely"] == "EUR"


def test_harvester_finds_email(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_extract_invoice_fields(ctx, fields=["parties"])
    assert "billing@nordlicht-systeme.de" in out["parties"]["emails"]


def test_harvester_rejects_unknown_field_kind(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    assert "error" in tool_extract_invoice_fields(ctx, fields=["vibes"])


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------


def test_classifier_agrees_on_an_invoice(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_classify_document_type(ctx, proposed_type="invoice", evidence="has RECHNUNG header")
    assert out["heuristic_type"] == "invoice"
    assert out["agrees"] is True


def test_classifier_pushes_back_on_a_wrong_guess(contract_doc):
    ctx = ToolContext(doc=contract_doc)
    out = tool_classify_document_type(ctx, proposed_type="invoice", evidence="guessing")
    assert out["heuristic_type"] == "contract"
    assert out["agrees"] is False
    assert "contract" in out["advice"]


# --------------------------------------------------------------------------
# validation - the heart of it
# --------------------------------------------------------------------------


def test_correct_arithmetic_passes(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_validate_amounts(ctx, **VALID_VALIDATION_ARGS)
    assert out["passed"] is True
    assert {c["name"] for c in out["checks"]} >= {
        "line_item_math",
        "line_items_sum_to_subtotal",
        "subtotal_plus_tax_equals_total",
        "tax_rate_matches_tax_amount",
        "total_is_positive",
    }


def test_inflated_total_is_caught(invoice_doc):
    """The broken_invoice.pdf case: subtotal + tax != stated total."""
    ctx = ToolContext(doc=invoice_doc)
    out = tool_validate_amounts(
        ctx, total=1398.63, subtotal=1137.50, tax_amount=216.13, tax_rate=19.0
    )
    assert out["passed"] is False
    failed = {c["name"] for c in out["checks"] if not c["passed"]}
    assert "subtotal_plus_tax_equals_total" in failed
    assert "45.0" in next(
        c["detail"] for c in out["checks"] if c["name"] == "subtotal_plus_tax_equals_total"
    )


def test_bad_line_item_math_is_caught(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_validate_amounts(
        ctx,
        total=100.0,
        line_items=[{"description": "widget", "quantity": 3,
                     "unit_price": 10.0, "amount": 100.0}],
    )
    failed = {c["name"] for c in out["checks"] if not c["passed"]}
    assert "line_item_math" in failed


def test_wrong_tax_rate_is_caught(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_validate_amounts(
        ctx, total=1190.0, subtotal=1000.0, tax_amount=190.0, tax_rate=7.0
    )
    failed = {c["name"] for c in out["checks"] if not c["passed"]}
    assert "tax_rate_matches_tax_amount" in failed


def test_rounding_within_tolerance_passes(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_validate_amounts(
        ctx, total=1190.01, subtotal=1000.0, tax_amount=190.0, tolerance=0.02
    )
    assert out["passed"] is True


# --------------------------------------------------------------------------
# the submission gate
# --------------------------------------------------------------------------


def test_submit_is_refused_before_validation(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    out = tool_submit_extraction(ctx, **VALID_SUBMISSION)
    assert out["accepted"] is False
    assert "validate_amounts" in out["error"]
    assert ctx.result is None


def test_submit_is_refused_when_total_drifts(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    tool_validate_amounts(ctx, **VALID_VALIDATION_ARGS)
    payload = {**VALID_SUBMISSION, "total": 5000.00}
    out = tool_submit_extraction(ctx, **payload)
    assert out["accepted"] is False
    assert "does not match" in out["error"]


def test_submit_reports_schema_errors_field_by_field(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    tool_validate_amounts(ctx, **VALID_VALIDATION_ARGS)
    payload = {**VALID_SUBMISSION, "currency": "euros", "confidence": 4.0}
    out = tool_submit_extraction(ctx, **payload)
    assert out["accepted"] is False
    fields = {issue["field"] for issue in out["issues"]}
    assert "currency" in fields
    assert "confidence" in fields


def test_valid_submission_is_accepted(invoice_doc):
    ctx = ToolContext(doc=invoice_doc)
    tool_validate_amounts(ctx, **VALID_VALIDATION_ARGS)
    out = tool_submit_extraction(ctx, **VALID_SUBMISSION)
    assert out["accepted"] is True
    assert ctx.result is not None
    assert ctx.result.currency == "EUR"
    assert ctx.result.issue_date.isoformat() == "2024-03-14"
    assert len(ctx.result.line_items) == 3
