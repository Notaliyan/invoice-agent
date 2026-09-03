"""Generate sample invoice PDFs so you can exercise /process without hunting
for a real document.

    python samples/make_sample_invoices.py

Writes three files next to this script:

  clean_invoice_eur.pdf   well-formed German invoice, 19% VAT, day-first dates
  clean_invoice_usd.pdf   US invoice, month-first dates, no VAT line
  broken_invoice.pdf      subtotal + tax deliberately does not equal the total,
                          so you can watch validate_amounts catch it and the
                          agent go back and re-read

Requires reportlab (in requirements-dev.txt).
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent


def _draw(path: Path, lines: list[tuple[str, str, float]]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 25 * mm
    for text, font, size in lines:
        if text == "---":
            y -= 4 * mm
            c.line(20 * mm, y, width - 20 * mm, y)
            y -= 4 * mm
            continue
        c.setFont(font, size)
        c.drawString(20 * mm, y, text)
        y -= (size + 4)
    c.showPage()
    c.save()
    print(f"wrote {path}")


B, R = "Helvetica-Bold", "Helvetica"


CLEAN_EUR = [
    ("Nordlicht Systeme GmbH", B, 16),
    ("Hafenstraße 14, 20359 Hamburg, Germany", R, 10),
    ("USt-IdNr: DE812345678   billing@nordlicht-systeme.de", R, 10),
    ("", R, 10),
    ("RECHNUNG / INVOICE", B, 14),
    ("Rechnungsnummer: NLS-2024-0417", R, 11),
    ("Rechnungsdatum: 14.03.2024", R, 11),
    ("Fällig am: 13.04.2024", R, 11),
    ("Zahlungsziel: 30 Tage netto", R, 10),
    ("", R, 10),
    ("Rechnung an:", B, 11),
    ("Meridian Analytics AG", R, 11),
    ("Bahnhofplatz 3, 8001 Zürich, Switzerland", R, 10),
    ("---", R, 10),
    ("Pos  Beschreibung                        Menge   Einzelpreis      Betrag", B, 10),
    ("1    Sensor gateway SG-40                    12       184,50     2.214,00", R, 10),
    ("2    Installation und Inbetriebnahme          8       125,00     1.000,00", R, 10),
    ("3    Wartungsvertrag 12 Monate                1       960,00       960,00", R, 10),
    ("---", R, 10),
    ("                                   Zwischensumme (netto)      4.174,00 EUR", R, 10),
    ("                                   MwSt 19%                     793,06 EUR", R, 10),
    ("                                   Gesamtbetrag               4.967,06 EUR", B, 11),
    ("", R, 10),
    ("IBAN: DE44 5001 0517 5407 3249 31   BIC: INGDDEFFXXX", R, 9),
]


CLEAN_USD = [
    ("Kestrel Supply Co.", B, 16),
    ("880 Harrison St, San Francisco, CA 94107", R, 10),
    ("EIN: 47-2938471   ar@kestrelsupply.com", R, 10),
    ("", R, 10),
    ("INVOICE", B, 14),
    ("Invoice #: KS-88213", R, 11),
    ("Invoice Date: 03/04/2024", R, 11),
    ("Due Date: 04/03/2024", R, 11),
    ("Terms: Net 30", R, 10),
    ("", R, 10),
    ("Bill To:", B, 11),
    ("Rivera Fabrication LLC", R, 11),
    ("1200 Industrial Pkwy, Reno, NV 89502", R, 10),
    ("---", R, 10),
    ("Item  Description                       Qty    Unit Price      Amount", B, 10),
    ("1     Steel bracket, 4in (box of 50)      14        $62.40      $873.60", R, 10),
    ("2     Anti-vibration mount AVM-2           6       $118.75      $712.50", R, 10),
    ("3     Freight, expedited                   1        $85.00       $85.00", R, 10),
    ("---", R, 10),
    ("                                          Subtotal          $1,671.10", R, 10),
    ("                                          Sales tax 8.5%       $142.04", R, 10),
    ("                                          Total Due          $1,813.14", B, 11),
]


BROKEN = [
    ("Halden Werkzeug GmbH", B, 16),
    ("Industriering 22, 44145 Dortmund, Germany", R, 10),
    ("USt-IdNr: DE119876543", R, 10),
    ("", R, 10),
    ("RECHNUNG", B, 14),
    ("Rechnungsnummer: HW-7741", R, 11),
    ("Rechnungsdatum: 02.09.2024", R, 11),
    ("", R, 10),
    ("Rechnung an: Brandt Montage GmbH, Koeln", R, 11),
    ("---", R, 10),
    ("Pos  Beschreibung                        Menge   Einzelpreis      Betrag", B, 10),
    ("1    Drehmomentschlüssel DS-9                 4       212,00       848,00", R, 10),
    ("2    Ersatzteilset ETS-14                     3        96,50       289,50", R, 10),
    ("---", R, 10),
    ("                                   Zwischensumme            1.137,50 EUR", R, 10),
    ("                                   MwSt 19%                   216,13 EUR", R, 10),
    ("                                   Gesamtbetrag             1.398,63 EUR", B, 11),
    ("", R, 9),
    ("(Note: the stated total is 45.00 higher than subtotal + tax. This file", R, 8),
    (" exists to prove validate_amounts catches it.)", R, 8),
]


def main() -> None:
    _draw(HERE / "clean_invoice_eur.pdf", CLEAN_EUR)
    _draw(HERE / "clean_invoice_usd.pdf", CLEAN_USD)
    _draw(HERE / "broken_invoice.pdf", BROKEN)


if __name__ == "__main__":
    main()
