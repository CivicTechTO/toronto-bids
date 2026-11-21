# Historical Bid Committee (BD) report recovery

This workflow repairs historical identity joins, captures 2013-2016 Bid Committee (BD) staff-report PDFs, and admits supported bids from the shared extraction cache.

## Identity and parser repairs

### Legacy fallback guards

The pre-Ariba supplier-and-value fallback keeps its unique-candidate requirement and adds two guards:

- Rejects candidates whose solicitation issue year is later than the council item's year.
- Requires either a full supplier-name core match or a shared identifying word outside generic industry and conjunction terms.

Filtering out an ambiguous candidate cannot promote another candidate into a match.

### Explicit identity repairs (`data/city_identity_repairs.json`)

Pins four reviewed cases: two wrong joins (`2014.BD174.2`, `2015.BD25.1`) and two unresolved spelling/joint-venture variants held for review (`2014.BD184.7`, `2017.BA55.8`). Each entry pins the item/title, report URL/PDF hash, and target issue date.

- A changed context aborts the resolver transaction. A report not yet downloaded may have a `NULL` hash, but once downloaded its hash must match.
- Resolution splits only the unsupported `city_council_bid` key onto a new canonical ID (recording the proof and prior ID in `merge_evidence`), preserving City document IDs, redirects, and raw facts.
- Rejected derived titles are cleared; City-published titles stay intact. Report and item associations are rebuilt from the corrected keys.

### BD agenda table parser

- Strips recognized multiline HST headers and referenced trailing notes before checking positional column alignment, without skipping stray headers mid-table. (Fixed `2015.BD25.1` to restore Bond Paving and align all eight bids.)
- `store_bids` re-derives each successfully parsed reference atomically via `rebuild_rows` (scoped to `source = 'bid_award_panel'` and that reference), keeping IDs and `first_seen` on unchanged keys.
- If a previously populated item fails to parse or loses rows, existing rows stay untouched and `enrich-titles` reports a review failure and exits non-zero.
- Full-archive calibration across the 891 held agendas changed 53 BD references (0 BA changes): 5 previously unreadable items now parse, and 12 ambiguous items are refused for manual review.

## Capturing 2013-2016 BD reports

Run from `scrapers/`:

```sh
uv run python -m toronto_bids.sources.bd_reports --database /path/to/bids.sqlite --directory /path/to/bd-reports --dry-run
uv run python -m toronto_bids.sources.bd_reports --database /path/to/bids.sqlite --directory /path/to/bd-reports
uv run python -m toronto_bids.sources.bd_reports --database /path/to/bids.sqlite --directory /path/to/bd-reports --resume
```

- **Inventory**: Selects held `bgrd` URLs for 2013-2016 BD references that have no stored bids (586 entries in the initial snapshot). `--dry-run` opens the DB read-only and writes nothing. `--limit N` caps attempted downloads; an incomplete run exits 1.
- **Resume and locking**: `inventory.json` freezes URL/reference pairs (restricted to the official City BD report path). `--resume` checks pairs against the DB and hash-verifies held PDFs. An advisory lock guards the inventory directory, and the CLI holds `<DATA_DIR>/.pipeline.lock` during DB writes.
- **Pacing and validation**: Serial requests spaced at least 1.1s apart with exponential backoff and `Retry-After` handling. Long rate limits stop the run cleanly for later resume; 403/404 responses stay visible errors. Downloads verify the PDF header and `pdfinfo` structure before saving by SHA-256. Scanned PDFs with no text layer are saved as valid PDFs and flagged as textless (18 of 586 in the initial capture). Capture writes `kind = 'bgrd'` only and creates no bid or award rows.

## Guarded extraction and admission

Queue a batch with:

```sh
uv run tb extract --batch --corpus bd_report --limit 8
```

Set `TB_DATA_DIR` to the target data directory when running from a worktree so the job uses the right budget ledger. Collect existing batches before submitting new ones.

`bd_admission.admit_report` gates which cached extractions become stored `bd_report` bids:

- Requires one contract, one printed Call Number matching the agenda title and PDF text, literal bidder/price matches, no supplier-name role tags or `No Bid` outcomes, no duplicate bidders, and no shortfall against a declared bid count.
- Multi-part/lot tables and interleaved wrapped names are held (scoped tables are handled separately under [scoped-city-bids.md](scoped-city-bids.md)).
- Stored bids key on the council item reference with `source = 'bd_report'` and **no document number** (Call Numbers are never converted into Ariba IDs). No awards are created.
- Proposals with named proponents and undisclosed prices are stored with `NULL` price/tax basis. Prices with miscellaneous, prompt-payment, or unit-rate qualifiers are held in the cache while keeping participation. Compliance statuses are withheld in this stage; raw cache payloads stay unchanged.
- Swaps replace only admitted council items. Refused, missing, or textless reports leave prior rows untouched, and mixed admitted/refused PDFs on the same item abort before replacement.
