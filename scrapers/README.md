# Toronto Bids scraper

A `uv`-managed Python 3.12+ package that collects City of Toronto and agency procurement records into a local SQLite database and exports them to JSON, CSV, and Parquet.

## Sources

### Core sync (`tb sync`)

- **OData `feis_solicitation_published`**: Authoritative City solicitation lifecycle feed (open, awarded, cancelled) and nested awards.
- **OData `feis_non_competitive_published`**: Non-competitive (sole-source) City contracts.
- **CKAN datasets** (`tobids-awarded-contracts`, `tobids-all-open-solicitations`, `tobids-non-competitive-contracts`, and capital pipeline): Historical backfill and cross-check for the OData feeds.
- **SAP Ariba Discovery (`ariba_discovery`)**: Archives open City postings into `ariba_posting` via public JSON APIs before they close. Postings are linked to `document_number` when the detail endpoint resolves (it frequently returns HTTP 500; later runs fill gaps). The stored price range and currency stay together during a detail outage. Search-only estimates have no stored currency.
- **Suspended and Disqualified Firms (`suspended_firms`)**: Parses the City's public suspension registry into `suspended_firm`, including supplier name, status, dates, type, and council authority reference.
- **Supplier dimension (`supplier`)**: A post-sync linking pass that groups spelling, case, and punctuation variants across awards, non-competitive contracts, bids, and suspensions into one `supplier` row per firm (see [`docs/supplier-entity-resolution.md`](../docs/supplier-entity-resolution.md)).

### Enrichment and agency commands

These run as separate CLI steps (and inside `tb nightly`), not inside `tb sync`:

- **Council enrichment (`tb enrich-council`)**: Fetches City Council decisions and linked staff-report PDFs for suspended firms from TMMIS using headed Chromium (TMMIS blocks headless browsers) and `pdftotext`. On headless servers, pass `--virtual-display` with `xvfb` installed.
- **Titles, agendas, and award summaries**: `tb enrich-titles`, `tb enrich-awards`, and `tb enrich-committee-awards` (see [`docs/city-sources.md`](../docs/city-sources.md)).
- **Ariba attachments (`tb enrich-ariba-attachments`)**: Downloads solicitation files behind Ariba's Respond button while postings are open (see [`docs/ariba-attachments.md`](../docs/ariba-attachments.md)).
- **Agencies and portals**: `tb enrich-agencies`, `tb capture-bonfire-listings`, `tb capture-merx`, `tb capture-ttc-history`, `tb capture-ttc-reports`, `tb capture-waterfront-awards` (see [`docs/agencies.md`](../docs/agencies.md)).
- **Document extraction (`tb extract`, `tb extraction-batches`)**: Extracts bids and awards from PDFs using Claude (see [`docs/document-extraction.md`](../docs/document-extraction.md)).

Playwright, `pyvirtualdisplay`, and `python-dotenv` are base dependencies installed by `uv sync`. Install the Chromium binary separately with `uv run playwright install chromium`.

### Keyspaces

- Competitive City records use the normalized 10-digit `document_number`.
- Non-competitive awards use `workspace_number`.
- Pre-2013 composite awards use `composite_award.call_number`.
- Agency board reports and portals use buyer-scoped keys and canonical procurement IDs.

## Usage

```shell
cd scrapers
uv sync
uv run tb sync                                      # fetch all core sources into files/bids.sqlite
uv run tb sync --only odata_solicitations,ckan_awarded
uv run tb status                                    # row counts and last run per source
uv run tb export [--out PATH]                       # export to JSON (default <DATA_DIR>/export/bids.json)
uv run pytest                                       # offline test suite
```

Set `TB_DATA_DIR` to store `bids.sqlite` and downloads somewhere other than `scrapers/files/`.

Each source runs independently: if one fails, the others still commit their rows. `tb sync` prints errors to stderr and exits non-zero if any source failed. Check `tb status` for per-source status and errors.

### Feed drift checks

`sources/schema_check.py` lists every field read from the OData and CKAN feeds (including nested award arrays). On every sync, `schema_check` samples the live feeds and reports missing fields via `sync_run`, stderr, and the exit code without blocking data ingestion. When a normalizer starts reading a new field, add it to `schema_check.py` in the same change.

### Export seam

`tb export` writes a solicitation-centric nested JSON file (`<DATA_DIR>/export/bids.json` by default). Each solicitation includes its nested `awards`, `ariba_postings`, `bids`, and `documents`, alongside top-level arrays for `noncompetitive`, `suspended_firms`, `suppliers`, `buyers`, `unlinked_ariba_postings`, and `unlinked_awards`.

`build_export_document(conn)` in `export/document.py` builds the format-independent structure and `export_json()` serializes it. Keep all shaping logic in `document.py` so new export formats reuse the same builder.

With a custom `--out` path, the schema, CSV ZIP, and Parquet files are written beside the JSON. If an output name is already occupied, the export stops before writing unless the previous completion marker for that JSON records the file and its contents still match.

## Data caveats

Keep these details in mind when querying `bids.sqlite`:

- **Do not aggregate `award_amount` or `contract_amount`.** They are `TEXT` columns holding the City's raw strings verbatim (such as `"$1,317,169.92 CAD"`, `"kj"`, or concatenated values). SQLite coerces text prefixes during `SUM()` and sorts text above numbers in comparisons. Aggregate `award_amount_numeric` or `contract_amount_numeric` (`REAL`) instead. A `NULL` numeric value next to a non-null raw string means the raw value is not a single parseable CAD amount.
- **Amounts have three separate tiers:**
  - Raw: `award_amount` (verbatim string published by the City).
  - Parsed: `award_amount_numeric` (machine-parsed, conservative).
  - Labelled: `award_amount_labelled` and `award_amount_verdict` (human review stored in `toronto_bids/data/amount_labels.toml`, covering typos like `'S2,035,000.00'` and rates like `'31.65/MT'`).

  ```sql
  -- Machine-parsed only
  SELECT SUM(award_amount_numeric) FROM award WHERE source = 'odata';

  -- Include human-reviewed labels and exclude non-awards
  SELECT SUM(COALESCE(award_amount_labelled, award_amount_numeric))
  FROM award
  WHERE source = 'odata' AND COALESCE(award_amount_verdict, '') != 'not_an_award';
  ```

  Run `tb amounts unlabelled` to list raw strings that failed parsing and have no verdict yet.
- **One `award` row is one award line, not one supplier.** Standing-offer call-ups can produce multiple lines for the same supplier on the same document (for example, `Cascades Recovery Inc.` has ten lines on document `9154157025`). Do not `GROUP BY document_number, supplier_name_raw` to deduplicate.
- **Filter by `source` before summing `award`.** Awards appear once per source (`source='odata'` and `source='ckan_awarded'`). Filter to `source = 'odata'` for a single view. Also note that upstream City data contains a few implausible values in both feeds (such as document `3901175008`, listed at `$9.05B` to an individual). We store what the City published rather than editing upstream errors.
- **Pre-2013 awards live in `composite_award`.** The City's feeds have almost no awards for 2009-2011. Historical awards recovered from Bid Committee composite reports live in `composite_award`, keyed by `call_number`. Aggregate `award_value_numeric`, which represents the initial contract term and excludes option years.
- **Missing titles are stored as `NULL`.** For most solicitations, the City publishes the document number as the title (for example, `Doc-3524228095`). Those placeholders are normalized to `NULL` at ingest. Check `title IS NULL` rather than `title LIKE 'Doc-%'`, which misses bare-number placeholders and wrongly matches real titles that start with a document number.
