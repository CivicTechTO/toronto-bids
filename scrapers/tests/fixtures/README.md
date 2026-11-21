# Test fixtures

Files referenced by `test_*.py` are active offline test inputs.

Files listed in [legacy-extraction-cases.json](legacy-extraction-cases.json) are preserved difficult document shapes from the per-source parsers retired in #213 (catalogued by corpus, format, and SHA-256 in #227). They are an unadjudicated input pool for future extraction evaluations, not expected outputs: review each document directly before writing assertions rather than treating old parser output as ground truth.

- **`pdf_text`**: Raw document text (including some full meeting packages). Keep layout intact.
- **`pdf_cell_rows`**: Extracted table/row structures rather than full text; any future evaluation should preserve their table layout instead of flattening them.
- **Agency provenance**: Source URLs, synthetic notes, and selection reasons are documented in [agencies/SOURCES.md](agencies/SOURCES.md).
