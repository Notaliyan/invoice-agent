# Invoice data summary

Generated 2026-11-16 23:39 UTC.

15 documents, 39 processing runs (39 succeeded), 39 stored extractions, 119 line items. Spend figures count each invoice once, using its latest extraction.

## Spend by vendor

| vendor_name | currency | invoices | total_spend | largest | share_of_currency_pct |
| --- | --- | --- | --- | --- | --- |
| Alpenblick Engineering AG | CHF | 1 | 6,669.77 | 6,669.77 | 100.00 |
| Polder Data B.V. | EUR | 1 | 7,260.00 | 7,260.00 | 45.84 |
| Nordlicht Systeme GmbH | EUR | 1 | 4,967.06 | 4,967.06 | 31.36 |
| Atelier Lumière SARL | EUR | 1 | 2,213.52 | 2,213.52 | 13.97 |
| Halden Werkzeug GmbH | EUR | 1 | 1,398.63 | 1,398.63 | 8.83 |
| Thornbury & Hale Ltd | GBP | 1 | 1,611.00 | 1,611.00 | 84.57 |
| OKAFOR PLUMBING | GBP | 1 | 294.00 | 294.00 | 15.43 |
| Brightwater Print & Pack Inc. | USD | 1 | 3,719.41 | 3,719.41 | 40.41 |
| Lindqvist Design Studio LLC | USD | 1 | 3,604.73 | 3,604.73 | 39.17 |
| Kestrel Supply Co. | USD | 1 | 1,813.14 | 1,813.14 | 19.70 |
| HARBOR HARDWARE | USD | 1 | 65.93 | 65.93 | 0.72 |

## Monthly spend by currency

| month | CHF | EUR | GBP | USD |
| --- | --- | --- | --- | --- |
| 2024-03 | 0.00 | 4,967.06 | 1,611.00 | 1,813.14 |
| 2024-05 | 0.00 | 2,213.52 | 0.00 | 0.00 |
| 2024-06 | 0.00 | 5,176.38 | 0.00 | 0.00 |
| 2024-07 | 0.00 | 0.00 | 0.00 | 65.93 |
| 2024-08 | 0.00 | 7,260.00 | 0.00 | 0.00 |
| 2024-09 | 0.00 | 1,398.63 | 0.00 | 3,719.41 |
| 2024-10 | 6,669.77 | 0.00 | 0.00 | 0.00 |
| 2024-11 | 0.00 | 0.00 | 294.00 | 0.00 |
| 2024-12 | 0.00 | 0.00 | 0.00 | 3,604.73 |

## Payment terms (days from issue to due date)

| currency | invoices | shortest | median | longest |
| --- | --- | --- | --- | --- |
| CHF | 1 | 30 | 30.00 | 30 |
| EUR | 3 | 30 | 30.00 | 30 |
| GBP | 1 | 30 | 30.00 | 30 |
| USD | 3 | 14 | 30.00 | 30 |

## Processing time (seconds)

| model | prompt_version | runs | mean | median | p90 | slowest |
| --- | --- | --- | --- | --- | --- | --- |
| gemini-3.5-flash-lite | v2 | 15 | 23.26 | 22.00 | 42.66 | 61.59 |

## Extraction accuracy by prompt version

Eval runs only: these are the runs with a labelled answer to score against.

| day | prompt_version | model | documents | field_accuracy | completeness | hallucination_rate | worst_document_accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-11 | v1 | gemini-3.5-flash-lite | 12 | 0.98 | 1.00 | 0.04 | 0.80 |
| 2026-11-11 | v2 | gemini-3.5-flash-lite | 12 | 0.99 | 1.00 | 0.01 | 0.93 |
| 2026-11-16 | v2 | gemini-3.5-flash-lite | 12 | 0.99 | 1.00 | 0.01 | 0.93 |

## Hardest documents

| filename | runs | field_accuracy | hallucination_rate |
| --- | --- | --- | --- |
| document-10.pdf | 3 | 0.89 | 0.21 |
| document-05.pdf | 3 | 0.98 | 0.03 |

## Is the model's confidence informative?

36 scored runs; 4 had at least one wrong field. Mean confidence was 0.99 overall and 1.00 on the runs with a mistake. Correlation between confidence and accuracy: -0.09.
