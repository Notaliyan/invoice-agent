"""Shared fixtures.

The OpenAI client is faked throughout: these tests are about our loop, our tools
and our schema, and they must run in CI without an API key or a network call.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.pdf import PdfDocument

INVOICE_TEXT = """\
Nordlicht Systeme GmbH
Hafenstrasse 14, 20359 Hamburg, Germany
USt-IdNr: DE812345678   billing@nordlicht-systeme.de

RECHNUNG / INVOICE
Rechnungsnummer: NLS-2024-0417
Rechnungsdatum: 14.03.2024
Faellig am: 13.04.2024

Rechnung an:
Meridian Analytics AG
Bahnhofplatz 3, 8001 Zuerich, Switzerland

Pos  Beschreibung                 Menge  Einzelpreis     Betrag
1    Sensor gateway SG-40            12       184,50   2.214,00
2    Installation                     8       125,00   1.000,00
3    Wartungsvertrag 12 Monate        1       960,00     960,00

Zwischensumme (netto)   4.174,00 EUR
MwSt 19%                  793,06 EUR
Gesamtbetrag            4.967,06 EUR
"""

CONTRACT_TEXT = """\
SERVICE AGREEMENT

This Agreement is entered into by and between the parties hereto.
The parties agree to the terms and conditions set forth herein.
Governing law shall be the laws of the Federal Republic of Germany.
Effective date: 2024-01-01
In witness whereof the parties have executed this contract.
"""


@pytest.fixture
def invoice_doc() -> PdfDocument:
    return PdfDocument.from_text(INVOICE_TEXT, filename="nordlicht.pdf")


@pytest.fixture
def contract_doc() -> PdfDocument:
    return PdfDocument.from_text(CONTRACT_TEXT, filename="agreement.pdf")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        openai_model="fake-model",
        agent_max_steps=6,
        page_preview_chars=400,
        # Never trace from the test suite, even if the developer's .env has keys.
        langfuse_public_key="",
        langfuse_secret_key="",
    )


# ---------------------------------------------------------------------------
# fake OpenAI client
# ---------------------------------------------------------------------------


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def prose(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=None)


def turn(*calls: SimpleNamespace) -> SimpleNamespace:
    """One assistant turn made up of the given tool calls."""
    return SimpleNamespace(content=None, tool_calls=list(calls))


class FakeCompletions:
    def __init__(self, script: Iterable[SimpleNamespace]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        # The runner appends to the same `messages` list across turns, so we have
        # to snapshot it - otherwise every recorded request aliases the final one.
        self.requests.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})
        if not self.script:
            raise AssertionError(
                f"agent made {len(self.requests)} model calls but the script ran out"
            )
        message = self.script.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


class FakeOpenAI:
    def __init__(self, script: Iterable[SimpleNamespace]) -> None:
        self.completions = FakeCompletions(script)
        self.chat = SimpleNamespace(completions=self.completions)


VALID_SUBMISSION: dict[str, Any] = {
    "document_type": "invoice",
    "vendor": {"name": "Nordlicht Systeme GmbH", "tax_id": "DE812345678"},
    "customer": {"name": "Meridian Analytics AG"},
    "invoice_number": "NLS-2024-0417",
    "issue_date": "2024-03-14",
    "due_date": "2024-04-13",
    "currency": "EUR",
    "line_items": [
        {"description": "Sensor gateway SG-40", "quantity": 12,
         "unit_price": 184.50, "amount": 2214.00},
        {"description": "Installation", "quantity": 8,
         "unit_price": 125.00, "amount": 1000.00},
        {"description": "Wartungsvertrag 12 Monate", "quantity": 1,
         "unit_price": 960.00, "amount": 960.00},
    ],
    "subtotal": 4174.00,
    "tax_amount": 793.06,
    "tax_rate": 19.0,
    "total": 4967.06,
    "confidence": 0.93,
}

VALID_VALIDATION_ARGS: dict[str, Any] = {
    "total": 4967.06,
    "subtotal": 4174.00,
    "tax_amount": 793.06,
    "tax_rate": 19.0,
    "line_items": VALID_SUBMISSION["line_items"],
}
