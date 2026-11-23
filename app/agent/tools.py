"""The agent's toolbox.

Design rule: every tool does deterministic work in Python that the model is bad
at doing in its head - regex harvesting, arithmetic, schema validation. The model
supplies judgement (which candidate is the vendor, which date is the issue date);
the tools supply ground truth. That split is what makes the arithmetic
trustworthy, and it is what `validate_amounts` exists to enforce.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.agent.prompts import VALIDATION_FAILED_NEXT, VALIDATION_PASSED_NEXT
from app.pdf import Line, PdfDocument
from app.schemas import ExtractionResult, ValidationCheck, ValidationReport

# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------

CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₨": "PKR",
}

ISO_CODES = (
    "USD EUR GBP CHF JPY INR PKR AED CAD AUD SEK NOK DKK PLN CZK TRY CNY SGD"
).split()

_CODE_ALT = "|".join(ISO_CODES)
_SYMBOL_CLASS = "[$€£¥₹₨]"

# Note the thousands separator is [.,] only, never whitespace. PDF tables are
# space-aligned, so allowing a space would merge adjacent columns: the row
# "12   184,50   2.214,00" would harvest a phantom 12184.50. Two real numbers
# with a bit of noise beats one plausible-looking wrong number.
_AMOUNT_RE = re.compile(
    rf"(?P<pre>{_SYMBOL_CLASS}|\b(?:{_CODE_ALT})\b)?\s*"
    r"(?P<num>-?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|-?\d+[.,]\d{1,2}|-?\d+)"
    rf"\s*(?P<post>{_SYMBOL_CLASS}|\b(?:{_CODE_ALT})\b)?"
)

_DATE_RE = re.compile(
    r"\b("
    r"\d{4}[./-]\d{1,2}[./-]\d{1,2}"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
    r"|\d{1,2}\.?\s+[A-Za-zÄÖÜäöü]{3,9}\.?\s+\d{4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"
    r")\b"
)

_DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
    "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y",
    "%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y",
    "%d.%m.%y", "%d/%m/%y", "%m/%d/%y",
    "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
]

# Longest keyword first: 'rechnung' must not win against 'rechnungsnummer' and
# leave us capturing 'snummer'. The \b then stops a short keyword matching inside
# a longer word.
_LABEL_SUFFIX = r"[\s.]*(?:#|nummer|number|no\.?|nr\.?)?\s*[:#-]?\s*"
_ID_VALUE = r"([A-Z0-9][A-Z0-9\-/_.]{2,24})"

_ID_PATTERNS = {
    "invoice_number": re.compile(
        r"(?:rechnungs?[\s.-]?nummer|rechnungs?[\s.-]?nr|invoice|rechnung|bill|facture|inv)\b"
        + _LABEL_SUFFIX
        + _ID_VALUE,
        re.IGNORECASE,
    ),
    "order_number": re.compile(
        r"(?:purchase\s+order|bestellnummer|bestellung|order|p\.?o\.?)\b"
        + _LABEL_SUFFIX
        + _ID_VALUE,
        re.IGNORECASE,
    ),
    "tax_id": re.compile(
        r"(?:vat|ust[\s.-]?id|umsatzsteuer[\s.-]?id|tax\s*id|steuernummer|ein|gstin)"
        r"[\s.]*(?:no\.?|nr\.?|number)?\s*[:#-]?\s*([A-Z]{0,3}\s?[A-Z0-9][A-Z0-9\-/]{4,18})",
        re.IGNORECASE,
    ),
    "iban": re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}\s?[A-Z0-9]{1,4})\b"),
}

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

_TOTAL_KEYWORDS = [
    "total", "grand total", "amount due", "balance due", "gesamt", "gesamtbetrag",
    "summe", "subtotal", "zwischensumme", "net", "netto", "vat", "tax", "mwst",
    "ust", "steuer", "shipping", "discount",
]

_TYPE_SIGNALS: dict[str, list[str]] = {
    "invoice": [
        "invoice", "rechnung", "facture", "amount due", "bill to", "payment terms",
        "due date", "net total", "remit to", "invoice date",
    ],
    "receipt": [
        "receipt", "kassenbon", "quittung", "thank you for your purchase",
        "change due", "cash tendered", "transaction id", "cashier", "store #",
    ],
    "purchase_order": [
        "purchase order", "po number", "ship to", "requisition",
        "order confirmation", "bestellung", "delivery date",
    ],
    "contract": [
        "agreement", "contract", "hereby", "the parties", "terms and conditions",
        "governing law", "in witness whereof", "shall be", "effective date",
        "termination", "vertrag",
    ],
}

_VALID_FIELD_KINDS = {
    "amounts", "dates", "identifiers", "parties", "currency", "total_lines",
}


def parse_amount(raw: str) -> float | None:
    """Turn '1.234,56', '1,234.56', '1 234.56' or '99' into a float.

    Ambiguity rule: whichever of ',' or '.' appears last is the decimal mark.
    """
    s = re.sub(r"[^\d.,\-]", "", (raw or "").strip())
    if not s or s in {"-", ".", ","}:
        return None

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        # A single trailing group of 1-2 digits is a decimal; anything else is
        # a thousands separator.
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date_candidates(raw: str) -> list[str]:
    """Return every plausible ISO reading of a date string.

    '03/04/2024' is genuinely ambiguous, so we hand the agent both readings and
    let it pick using surrounding context rather than guessing a locale.
    """
    stripped = raw.strip()
    variants = {
        stripped,
        re.sub(r"\s+", " ", stripped.replace(",", " ")).strip(),
        re.sub(r"\s+", " ", stripped.replace(",", " ").replace(".", " ")).strip(),
    }
    seen: list[str] = []
    for fmt in _DATE_FORMATS:
        for candidate in variants:
            try:
                iso = datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
            if iso not in seen:
                seen.append(iso)
    return seen


# --------------------------------------------------------------------------
# tool context
# --------------------------------------------------------------------------


@dataclass
class ToolContext:
    """Per-request state shared by the tools within one agent run."""

    doc: PdfDocument
    validation: ValidationReport | None = None
    validated_total: float | None = None
    classification: str | None = None
    result: ExtractionResult | None = None
    calls: list[str] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.result is not None


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------


def tool_get_page_text(
    ctx: ToolContext, page: int, max_chars: int = 4000
) -> dict[str, Any]:
    try:
        text = ctx.doc.page_text(int(page))
    except (IndexError, TypeError, ValueError) as exc:
        return {"error": str(exc)}
    return {
        "page": page,
        "total_pages": ctx.doc.page_count,
        "truncated": len(text) > max_chars,
        "text": text[:max_chars],
    }


def tool_search_document(
    ctx: ToolContext,
    query: str,
    is_regex: bool = False,
    max_results: int = 10,
    context_lines: int = 0,
) -> dict[str, Any]:
    lines = ctx.doc.lines()
    try:
        pattern = re.compile(query if is_regex else re.escape(query), re.IGNORECASE)
    except re.error as exc:
        return {"error": f"invalid regex: {exc}"}

    hits: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        if not pattern.search(line.text):
            continue
        lo = max(0, idx - context_lines)
        hi = min(len(lines), idx + context_lines + 1)
        hit: dict[str, Any] = {
            "page": line.page,
            "line": line.number,
            "text": line.text,
        }
        if context_lines:
            hit["context"] = [item.text for item in lines[lo:hi]]
        hits.append(hit)
        if len(hits) >= max_results:
            break
    return {"query": query, "match_count": len(hits), "matches": hits}


def _harvest_amounts(lines: list[Line]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
        for m in _AMOUNT_RE.finditer(line.text):
            value = parse_amount(m.group("num"))
            if value is None:
                continue
            symbol = m.group("pre") or m.group("post")
            hint = None
            if symbol:
                hint = CURRENCY_SYMBOLS.get(symbol, symbol.upper())
            out.append(
                {
                    "value": value,
                    "raw": m.group(0).strip(),
                    "currency_hint": hint,
                    "page": line.page,
                    "line": line.number,
                    "line_text": line.text,
                }
            )
    return out


def _harvest_dates(lines: list[Line]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
        for m in _DATE_RE.finditer(line.text):
            raw = m.group(1)
            iso = parse_date_candidates(raw)
            if not iso:
                continue
            out.append(
                {
                    "raw": raw,
                    "iso_candidates": iso,
                    "ambiguous": len(iso) > 1,
                    "page": line.page,
                    "line": line.number,
                    "line_text": line.text,
                }
            )
    return out


def _harvest_identifiers(lines: list[Line]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in _ID_PATTERNS}
    for line in lines:
        for kind, pattern in _ID_PATTERNS.items():
            for m in pattern.finditer(line.text):
                value = m.group(1).strip(" .:-")
                # A label pattern with no separator happily swallows the next
                # word ("Invoice Date" -> "Date"). Real identifiers have digits.
                if not any(ch.isdigit() for ch in value):
                    continue
                out[kind].append(
                    {
                        "value": value,
                        "page": line.page,
                        "line": line.number,
                        "line_text": line.text,
                    }
                )
    return {k: v for k, v in out.items() if v}


def _harvest_currency(lines: list[Line]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for line in lines:
        for sym, code in CURRENCY_SYMBOLS.items():
            n = line.text.count(sym)
            if n:
                counts[code] = counts.get(code, 0) + n
        for code in ISO_CODES:
            if re.search(rf"\b{code}\b", line.text):
                counts[code] = counts.get(code, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    return {
        "counts": dict(ranked),
        "most_likely": ranked[0][0] if ranked else None,
    }


def tool_extract_invoice_fields(
    ctx: ToolContext, fields: list[str], page: int | None = None
) -> dict[str, Any]:
    """High-recall, low-precision candidate harvesting. The agent picks winners."""
    lines = ctx.doc.lines()
    if page is not None:
        lines = [item for item in lines if item.page == int(page)]

    unknown = [f for f in fields if f not in _VALID_FIELD_KINDS]
    if unknown:
        return {
            "error": f"unknown field kinds {unknown}; "
                     f"valid kinds are {sorted(_VALID_FIELD_KINDS)}"
        }

    out: dict[str, Any] = {"scanned_lines": len(lines)}
    if "amounts" in fields:
        out["amounts"] = _harvest_amounts(lines)[:60]
    if "dates" in fields:
        out["dates"] = _harvest_dates(lines)[:30]
    if "identifiers" in fields:
        out["identifiers"] = _harvest_identifiers(lines)
    if "currency" in fields:
        out["currency"] = _harvest_currency(lines)
    if "parties" in fields:
        out["parties"] = {
            "emails": sorted({m for item in lines for m in _EMAIL_RE.findall(item.text)}),
            "header_lines": [
                {"page": item.page, "line": item.number, "text": item.text}
                for item in lines[:12]
            ],
            "addressee_blocks": [
                {"page": item.page, "line": item.number, "text": item.text}
                for item in lines
                if re.search(
                    r"\b(bill(ed)? to|ship to|sold to|kunde|rechnung an)\b",
                    item.text,
                    re.IGNORECASE,
                )
            ],
        }
    if "total_lines" in fields:
        out["total_lines"] = [
            {"page": item.page, "line": item.number, "text": item.text}
            for item in lines
            if any(k in item.text.lower() for k in _TOTAL_KEYWORDS)
        ][:40]
    return out


def tool_classify_document_type(
    ctx: ToolContext, proposed_type: str, evidence: str
) -> dict[str, Any]:
    """Cross-check the agent's proposed type against a keyword scorer.

    A disagreement is not an error - it is a prompt to go look again, which is
    exactly the kind of self-correction we want to see in the trace.
    """
    text = ctx.doc.text.lower()
    scores = {
        kind: sum(text.count(sig) for sig in signals)
        for kind, signals in _TYPE_SIGNALS.items()
    }
    found = {
        kind: [sig for sig in signals if sig in text][:6]
        for kind, signals in _TYPE_SIGNALS.items()
    }
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    heuristic = ranked[0][0] if ranked and ranked[0][1] > 0 else "other"

    ctx.classification = proposed_type
    agrees = heuristic == proposed_type
    return {
        "proposed_type": proposed_type,
        "heuristic_type": heuristic,
        "agrees": agrees,
        "keyword_scores": dict(ranked),
        "signals_found": {k: v for k, v in found.items() if v},
        "advice": (
            "Heuristic agrees; proceed."
            if agrees
            else f"Heuristic says {heuristic!r}. Re-read the document and either "
                 f"justify {proposed_type!r} in `notes` or change your answer."
        ),
        "evidence_recorded": evidence,
    }


def tool_validate_amounts(
    ctx: ToolContext,
    total: float,
    line_items: list[dict[str, Any]] | None = None,
    subtotal: float | None = None,
    tax_amount: float | None = None,
    tax_rate: float | None = None,
    tolerance: float = 0.02,
) -> dict[str, Any]:
    """Real arithmetic, done in Python. This is the gate on `submit_extraction`."""
    line_items = line_items or []
    # Models sometimes send line items as strings; say so rather than crash.
    if not isinstance(line_items, list) or any(not isinstance(i, dict) for i in line_items):
        return {
            "error": (
                "line_items must be a list of objects with description, quantity, "
                "unit_price and amount - not strings. Call validate_amounts again."
            )
        }
    checks: list[ValidationCheck] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(ValidationCheck(name=name, passed=passed, detail=detail))

    total = float(total)

    # 1. quantity * unit_price == amount, per line
    bad_lines: list[str] = []
    for i, item in enumerate(line_items, start=1):
        qty = item.get("quantity")
        unit = item.get("unit_price")
        amount = item.get("amount")
        if qty is None or unit is None or amount is None:
            continue
        expected = round(float(qty) * float(unit), 2)
        if abs(expected - float(amount)) > tolerance:
            bad_lines.append(
                f"line {i} ({item.get('description', '?')!r}): "
                f"{qty} x {unit} = {expected}, but amount = {amount}"
            )
    if line_items:
        add(
            "line_item_math",
            not bad_lines,
            "; ".join(bad_lines)
            if bad_lines
            else f"all {len(line_items)} line totals consistent",
        )

    # 2. sum(line items) == subtotal (or == total when there is no tax)
    line_sum = round(sum(float(i.get("amount") or 0) for i in line_items), 2)
    if subtotal is not None:
        expected_base: float | None = float(subtotal)
    elif tax_amount is not None:
        expected_base = round(total - float(tax_amount), 2)
    else:
        expected_base = total
    if line_items:
        delta = round(line_sum - expected_base, 2)
        add(
            "line_items_sum_to_subtotal",
            abs(delta) <= tolerance,
            f"line items sum to {line_sum}, expected {expected_base} "
            f"(difference {delta})",
        )

    # 3. subtotal + tax == total
    if subtotal is not None:
        computed = round(float(subtotal) + float(tax_amount or 0), 2)
        delta = round(computed - total, 2)
        add(
            "subtotal_plus_tax_equals_total",
            abs(delta) <= tolerance,
            f"{subtotal} + {tax_amount or 0} = {computed}, stated total is {total} "
            f"(difference {delta})",
        )

    # 4. stated tax rate matches the stated tax amount
    if tax_rate is not None and subtotal is not None and tax_amount is not None:
        expected_tax = round(float(subtotal) * float(tax_rate) / 100.0, 2)
        delta = round(expected_tax - float(tax_amount), 2)
        add(
            "tax_rate_matches_tax_amount",
            abs(delta) <= max(tolerance, abs(expected_tax) * 0.01),
            f"{tax_rate}% of {subtotal} = {expected_tax}, stated tax is {tax_amount} "
            f"(difference {delta})",
        )

    # 5. sanity
    add("total_is_positive", total > 0, f"total = {total}")

    report = ValidationReport(passed=all(c.passed for c in checks), checks=checks)
    ctx.validation = report
    ctx.validated_total = total

    return {
        "passed": report.passed,
        "checks": [c.model_dump() for c in report.checks],
        "next_step": VALIDATION_PASSED_NEXT if report.passed else VALIDATION_FAILED_NEXT,
    }


def tool_submit_extraction(ctx: ToolContext, **payload: Any) -> dict[str, Any]:
    """Terminal tool. Validates against `ExtractionResult` and gates on validation."""
    if ctx.validation is None:
        return {
            "accepted": False,
            "error": "You must call validate_amounts before submitting.",
        }

    total = payload.get("total")
    if total is not None and ctx.validated_total is not None:
        try:
            if abs(float(total) - ctx.validated_total) > 0.02:
                return {
                    "accepted": False,
                    "error": (
                        f"Submitted total {total} does not match the total you "
                        f"validated ({ctx.validated_total}). Re-run validate_amounts "
                        "with the final numbers before submitting."
                    ),
                }
        except (TypeError, ValueError):
            return {"accepted": False, "error": f"total must be a number, got {total!r}"}

    try:
        result = ExtractionResult.model_validate(payload)
    except ValidationError as exc:
        return {
            "accepted": False,
            "error": "Schema validation failed. Fix these and call submit_extraction again.",
            "issues": [
                {"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                for e in exc.errors()
            ],
        }

    ctx.result = result
    return {
        "accepted": True,
        "message": "Extraction accepted. You are done; stop calling tools.",
    }


# --------------------------------------------------------------------------
# OpenAI tool specs
# --------------------------------------------------------------------------

_LINE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "quantity": {"type": ["number", "null"]},
        "unit_price": {"type": ["number", "null"]},
        "amount": {"type": "number", "description": "Line total."},
    },
    "required": ["description", "amount"],
}

_PARTY_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "address": {"type": ["string", "null"]},
        "tax_id": {"type": ["string", "null"]},
        "email": {"type": ["string", "null"]},
    },
    "required": ["name"],
}

_DOC_TYPE_ENUM = ["invoice", "receipt", "purchase_order", "contract", "other"]

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_page_text",
            "description": (
                "Read the full text of one page. Use when the preview was truncated "
                "or the document has more than one page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "1-indexed page number."},
                    "max_chars": {"type": "integer", "default": 4000},
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_document",
            "description": (
                "Find lines matching a string or regex, with page and line numbers. "
                "Cheaper than reading a whole page when you want one specific value."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "is_regex": {"type": "boolean", "default": False},
                    "max_results": {"type": "integer", "default": 10},
                    "context_lines": {
                        "type": "integer",
                        "default": 0,
                        "description": "Lines of surrounding context to include.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_invoice_fields",
            "description": (
                "Deterministically harvest candidate values with their locations. "
                "High recall, low precision - you decide which candidates are right. "
                "More reliable than eyeballing raw text for numbers and dates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(_VALID_FIELD_KINDS)},
                        "description": "Which kinds of candidates to harvest.",
                    },
                    "page": {
                        "type": ["integer", "null"],
                        "description": "Limit the scan to one page.",
                    },
                },
                "required": ["fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "classify_document_type",
            "description": (
                "Cross-check your proposed document type against a keyword scorer. "
                "Call this once you have read enough to have an opinion."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposed_type": {"type": "string", "enum": _DOC_TYPE_ENUM},
                    "evidence": {
                        "type": "string",
                        "description": "One sentence on what led you to this type.",
                    },
                },
                "required": ["proposed_type", "evidence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_amounts",
            "description": (
                "Check your extracted figures with real arithmetic. You MUST call this "
                "before submit_extraction will accept anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "total": {"type": "number"},
                    "line_items": {"type": "array", "items": _LINE_ITEM_SCHEMA},
                    "subtotal": {"type": ["number", "null"]},
                    "tax_amount": {"type": ["number", "null"]},
                    "tax_rate": {
                        "type": ["number", "null"],
                        "description": "Percent, e.g. 19 for 19%.",
                    },
                    "tolerance": {"type": "number", "default": 0.02},
                },
                "required": ["total"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_extraction",
            "description": (
                "Submit the final structured result. Rejected if it fails schema "
                "validation or if the total does not match what you validated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_type": {"type": "string", "enum": _DOC_TYPE_ENUM},
                    "vendor": _PARTY_SCHEMA,
                    "customer": _PARTY_SCHEMA,
                    "invoice_number": {"type": ["string", "null"]},
                    "issue_date": {
                        "type": ["string", "null"],
                        "description": "ISO 8601, YYYY-MM-DD.",
                    },
                    "due_date": {
                        "type": ["string", "null"],
                        "description": "ISO 8601, YYYY-MM-DD.",
                    },
                    "currency": {
                        "type": "string",
                        "description": "ISO 4217 code, e.g. EUR. Convert symbols yourself.",
                    },
                    "line_items": {"type": "array", "items": _LINE_ITEM_SCHEMA},
                    "subtotal": {"type": ["number", "null"]},
                    "tax_amount": {"type": ["number", "null"]},
                    "tax_rate": {"type": ["number", "null"]},
                    "total": {"type": "number"},
                    "confidence": {
                        "type": "number",
                        "description": (
                            "0-1. Lower it when the document was ambiguous or when "
                            "validation checks did not all pass."
                        ),
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": (
                            "Anything the caller should know: unresolved ambiguity, or "
                            "arithmetic that does not add up in the source document."
                        ),
                    },
                },
                "required": [
                    "document_type", "vendor", "currency", "total", "confidence",
                ],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "get_page_text": tool_get_page_text,
    "search_document": tool_search_document,
    "extract_invoice_fields": tool_extract_invoice_fields,
    "classify_document_type": tool_classify_document_type,
    "validate_amounts": tool_validate_amounts,
    "submit_extraction": tool_submit_extraction,
}

TERMINAL_TOOL = "submit_extraction"
