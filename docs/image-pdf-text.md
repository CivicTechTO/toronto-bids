# Image-only City report text (OCR)

Some Bid Committee reports are scanned PDFs with no text layer. `pdftotext` returns empty text, so normal extraction skips them as `no_text`. `pdf_text.py` adds a local Tesseract OCR fallback for held, textless `bd_report` PDFs while keeping native text as the first choice.

## OCR preparation and storage

- Original PDF bytes, SHA-256, and `background_pdf.text` stay untouched. Derived OCR text is stored separately in `pdf_text`, keyed by the PDF's SHA-256, along with its text digest and OCR provenance (Tesseract version, English language-data hash, DPI, PSM settings, per-page text, and word bounding boxes/confidences).
- `pdf_text.py` first retries `pdftotext -layout`. Only an empty result triggers OCR: pages render serially via Poppler at 300 DPI and run single-threaded Tesseract (`PSM 6`, `preserve_interword_spaces`), capped at 40 pages with subprocess timeouts. See the [Tesseract quality guide](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html) and [CLI docs](https://github.com/tesseract-ocr/tessdoc/blob/main/Command-Line-Usage.md).
- Every page must succeed before writing a record. Empty output, tool errors, or changed bytes fail immediately.
- Eleven reviewed OCR derivatives are committed in `data/city_image_pdf_text.json.gz` so deployments do not depend on reproducing identical Tesseract output. Local originals are hash-verified before accepting a shipped derivative; already-applied records replay without local PDFs or Tesseract installed.

## Extraction and reviewed admission

- Prepared text feeds the existing Claude extraction prompt, cache (still keyed on the original PDF SHA-256), budget ledger, and batch queue. Keyless uncached counts use the same rules. Automatic OCR is limited to `bd_report`.
- Because OCR can misread names and numbers, generic BD backfill holds OCR-derived extractions until reviewed against the original page images.
- Reviewed image reports use the scoped bid representation in [scoped-city-bids.md](scoped-city-bids.md) (one unpriced participation row per firm, plus `bid_scope`, `bid_response`, and `bid_response_price`).
- Each image review pins the OCR proof, original PDF, cached model result, independent City identity proof, agenda hash, and the City's own agenda transcription. Image-space witness rectangles verify exact OCR crops and geometric containment of responses inside supplier columns and scope rows.
- **OCR corrections**: Allowed only when corroborated by the City's agenda transcription, keeping both raw and corrected text. Alphanumeric supplier tokens and price digits must stay intact (including `percent` / `%` equivalence); broader renaming or numeric changes are rejected.
- **Schedule A identity**: An agenda can establish historical identity for a labelled initial Schedule A award when the recommendation assigns that schedule to the exact supplier and its first net amount and dates uniquely match the publisher record.

## Setup and operation

Install Poppler and English Tesseract for new OCR runs (CI installs both and runs a raster-PDF test):

```sh
sudo apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-eng
```

Queue OCR-backed BD reports using the shared archive lock:

```sh
uv run tb extract --batch --corpus bd_report --limit N
```

Unreviewed OCR results stay held even after batch collection. As with all batch jobs, collect into the authoritative archive under `.pipeline.lock` before copying cache entries to a private validation database. The test-pinned review snapshot for the initial 11-report cohort is in `docs/research/city-image-pdf-review-2026-11-06.json`.
