"""The golden dataset: documents paired with hand-written correct extractions.

Each case is two files in evals/golden/:

    <id>.txt    the document text, exactly as the agent will see it
    <id>.json   {"id", "description", "tags", "source", "expected": {...}}

A line containing only PAGE_BREAK splits the text into pages, so multi-page
documents exercise the agent's reading tools.

Labelling conventions (the scorer depends on these):

- `expected` uses the ExtractionResult field names. Only the fields listed in
  evals/scoring.py are scored; `confidence`, `notes`, addresses and emails are
  free.
- null means "the document does not state this". A model that fills it in
  anyway is hallucinating.
- `invoice_number` is the document's own number, whatever it is called
  (receipt no., PO number).
- `vendor` is the party being paid, also on a purchase order.
- When the document's arithmetic is wrong, the label is what the document says.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.pdf import PdfDocument

GOLDEN_DIR = Path(__file__).parent / "golden"
PAGE_BREAK = "=== PAGE BREAK ==="


@dataclass(frozen=True)
class GoldenCase:
    id: str
    description: str
    pages: list[str]
    expected: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    source: str = "synthetic"

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    @property
    def filename(self) -> str:
        # Deliberately uninformative: the case id says things like
        # "purchase_order" and "broken_total", and the agent sees the filename.
        return f"document-{self.id.split('_', 1)[0]}.pdf"

    def document(self) -> PdfDocument:
        return PdfDocument(filename=self.filename, pages=list(self.pages))


def _split_pages(text: str) -> list[str]:
    pages: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.strip() == PAGE_BREAK:
            pages.append([])
        else:
            pages[-1].append(line)
    return ["\n".join(p).strip("\n") for p in pages]


def load_cases(directory: Path = GOLDEN_DIR) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for label_path in sorted(directory.glob("*.json")):
        label = json.loads(label_path.read_text(encoding="utf-8"))
        text_path = label_path.with_suffix(".txt")
        if not text_path.exists():
            raise FileNotFoundError(f"{label_path.name} has no matching {text_path.name}")
        if label["id"] != label_path.stem:
            raise ValueError(f"{label_path.name}: id {label['id']!r} does not match filename")
        cases.append(
            GoldenCase(
                id=label["id"],
                description=label.get("description", ""),
                pages=_split_pages(text_path.read_text(encoding="utf-8")),
                expected=label["expected"],
                tags=list(label.get("tags", [])),
                source=label.get("source", "synthetic"),
            )
        )
    if not cases:
        raise FileNotFoundError(f"no golden cases found in {directory}")
    return cases
