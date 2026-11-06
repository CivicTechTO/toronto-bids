# Image-only City report validation — 2026-11-06

A fresh production-copy replay recovers all eleven City solicitations in the frozen textless-report cohort. Missing bid coverage falls **3,734 → 3,723**. Original PDFs, native capture text, model caches and prior facts stay intact.

The [machine-readable source review](city-image-pdf-review-2026-11-06.json) records public URLs, original byte hashes, source expectations, independent identity proofs, OCR evidence and projection digests. [Image PDF text](../image-pdf-text.md) describes preparation, admission and deployment.

| Source | Participating firms | Response cells | Quoted prices |
| --- | ---: | ---: | ---: |
| [2013.BD129.6](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-59545.pdf) | 2 | 2 | 0 |
| [2016.BD103.1](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-96549.pdf) | 8 | 8 | 7 |
| [2013.BD120.10](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57522.pdf) | 4 | 19 | 19 |
| [2013.BD120.1](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57518.pdf) | 9 | 9 | 9 |
| [2013.BD120.7](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57513.pdf) | 12 | 12 | 12 |
| [2013.BD120.2](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57508.pdf) | 2 | 2 | 2 |
| [2014.BD179.6](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-69031.pdf) | 1 | 1 | 1 |
| [2013.BD120.6](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57512.pdf) | 10 | 10 | 10 |
| [2013.BD120.4](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57510.pdf) | 5 | 5 | 5 |
| [2013.BD120.3](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57509.pdf) | 1 | 1 | 1 |
| [2013.BD129.3](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-59538.pdf) | 10 | 10 | 0 |
| **Total** | **64** | **79** | **66** |

The counts distinguish firm/event participation from repeated scope cells; 64 is not a count of globally distinct companies. The nineteen TreeAzin price cells remain in seven schedules, with four participating firms. The graffiti report declares six submissions but names only the two cost-evaluated proponents; no names or prices are invented for the other four. Its scope label makes this partial disclosure explicit.

The Tacel report preserves including-HST/25% miscellaneous wording and the separate 3.5%/4% prompt-payment conditions on their own prices. Novax remains a named rejected bidder with no invented price. The arborist report says including all applicable taxes and charges; it does not receive an inferred explicit HST basis. The paving report retains its adjusted-price note; Rabcon retains its mathematical-correction note. Six starred proponents retain the source technical-threshold outcome; the four others are not assigned inferred compliance. The source itself prints `Yvelo Mailng Services Inc.`; both a high-resolution PDF crop and the independent City transcription confirmed that spelling, which is preserved.

## Recognition and model validation

All eleven held originals had empty native text and were verified against anonymous downloads from their official City URLs. Local OCR covered all 41 pages, retaining raw text and word geometry. Each source bidder table was visually read, then compared with the unchanged batched Sonnet output and the independently held City-authored agenda transcription. Explicit recognition cleanup retains both readings and cannot rename suppliers or change price digits. Raw model payloads are preserved.

The eleven Sonnet/high batch requests settled at **US$0.092381**, against a conservative US$2.031233 reservation under the existing budget. No prompt/version change, model fallback or paid retry was used. A collection-to-copy handoff issue was repaired from exact retained receipts under the production writer lock; only the normal raw extraction cache changed, with production fact counts and publication unchanged.

## Replay and export checks

The fresh replay adds 64 unpriced participation records, 17 scopes, 79 source response cells, 66 price values, eleven derived-text entries and eleven source reviews. Eleven new independently checked City crosswalks use the existing proof classes, retaining the source/publisher method discrepancy explicitly and binding the first separately recommended Schedule A award without using combined totals. Original 172 identity proofs, 59 scalar bid proofs and eleven previous scoped reviews remain intact.

Prior bid facts, IDs and first-seen history, City canonical IDs, raw capture/cache tables, awards and agency facts are preserved. Repeated resolution retains stable IDs; explicit BD backfill preserves the new scoped facts. Suppliers rebuild successfully; SQLite quick/foreign-key checks pass. Normal JSON, CSV, Parquet and schema exports include the derived-text ledger. A portable accepted copy exports without the original scans, original agendas or installed OCR tools.

The full backend suite passes **1,644 tests**, including a real raster-PDF recognition test, native-first behavior, source/derivative drift, page bounds, missing tools, failed text preparation, immutable shipped evidence, refusal of unreviewed OCR facts, supplier/price correction guards, schedule identity binding and whole-cohort source-cell reconciliation. Frontend checks pass **138 unit tests and 115 fixture site tests** (four existing optional-fixture skips). A full recovered-data build and generated internal links are verified separately.

Production bid derivation and publication remain pending merge. The raw paid responses are retained operationally for normal post-merge deployment; these coverage counts describe the production-copy rehearsal.
