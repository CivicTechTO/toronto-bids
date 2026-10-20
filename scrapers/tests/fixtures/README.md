# Fixture roles

Files referenced by `test_*.py` are active offline test inputs. The retained extraction
inputs listed in [legacy-extraction-cases.json](legacy-extraction-cases.json) have a
different role: they preserve difficult document shapes from the per-source parsers
retired in #213. #227's audit retained their bytes and catalogued their corpus, input
format and SHA-256 rather than deleting them as unused fixtures.

The catalogue is an input pool for future extraction evaluation, not an executable
benchmark or a set of correct expected outputs. Each case is explicitly unadjudicated.
Review bidder-to-price relations and document declarations before adding expected
records; the retired parser's output alone is not ground truth.

- `pdf_text` inputs can be read as document text. Preserve their layout and provenance;
  some are long meeting packages rather than individual award reports.
- `pdf_cell_rows` inputs are recorded table/row structures. They are not model response
  fixtures or full document text. A future evaluation must declare how it presents cells
  to the extractor rather than silently flattening away the table relationships.
- Agency source URLs, synthetic cases and the original selection reasons are recorded
  in [agencies/SOURCES.md](agencies/SOURCES.md). Retention does not establish provenance
  for cases that lack a source record.

The catalogue covers the otherwise unreferenced extraction inputs at the #227 audit.
It does not change production extraction, resurrect the retired parsers, invoke a model,
or claim automated regression coverage for these cases.
