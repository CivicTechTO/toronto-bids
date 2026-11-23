# CLAUDE.md

Engineering reference for working in this repository.

**Keeping this file current:** Document rules, mechanisms, and rationale rather than point-in-time counts (row counts, cached file counts, test counts, line numbers). Historical measurements that explain a decision are fine when cited to their issue number. When code changes behavior described here, update this file or the linked topic doc in the same change.

## Overview

A CivicTechTO project that archives City of Toronto and agency procurement data into a local SQLite database and exports public JSON, CSV, and Parquet artifacts. All active code lives in `scrapers/` (`uv`-managed Python 3.12+, installing the `tb` CLI).

Core `tb sync` requires no browser, login, or API key. Two optional stages do: PDF extraction calls the direct Claude API (`ANTHROPIC_API_KEY`), and some captures (Ariba attachments, TMMIS discovery, TRCA verification fallback) run headed Chromium under Xvfb.

## Commands

Run from `scrapers/`:

```shell
cd scrapers
uv sync                                   # install deps (includes dev/pytest)
uv run pytest                             # offline fixture-based test suite
uv run pytest tests/test_odata.py         # single test file
uv run pytest tests/test_odata.py::test_normalize_solicitation_yields_spine_and_award
uv run tb sync                            # fetch core sources into files/bids.sqlite (exits 1 if any fail)
uv run tb sync --only odata_solicitations,ckan_awarded
uv run tb status                          # row counts and last run per source/step
uv run tb export                          # write JSON export (default <DATA_DIR>/export/bids.json)
uv run tb nightly                         # full scheduled run + Slack summary
uv run tb extract --corpus award_summary --dry-run
```

- **No linter, formatter, or typechecker is configured** (no ruff, mypy, or black).
- **CI** (`.github/workflows/tests.yml`) runs `uv sync --locked && uv run pytest`. Always re-lock and commit `uv.lock` after dependency changes.
- **Poppler**: Council tests need `pdftotext` (`brew install poppler` / `apt-get install -y poppler-utils`) and skip if it is absent.
- **`TB_DATA_DIR`** relocates the SQLite database and downloads (default `scrapers/files/`).
- **Browser deps** (`playwright`, `pyvirtualdisplay`, `python-dotenv`) are mandatory base dependencies so `uv sync --locked` never drops them on the deploy host. Install the browser binary separately with `uv run playwright install chromium`.
- `tb sync` hits live City endpoints and takes several minutes; use `pytest` for local iteration.
- Other standalone commands (not part of `tb sync`): `enrich-council` (`--virtual-display`), `enrich-titles` (`--reports`), `enrich-awards` (`--download`), `enrich-committee-awards` (`--scrape`), `enrich-agencies` (`--only`, `--fetch`, `--scrape`, `--portal`, `--record`), `enrich-ariba-attachments` (`--capture`, `--document N`, `--ingest`, `--reindex`), `capture-bonfire-listings`, `capture-merx`, `capture-ttc-history`, `capture-ttc-reports`, `capture-trca-history`, `capture-waterfront-awards`, `recover-trca-documents`, `extraction-batches`, `agency-coverage`, `amounts`, `manifest`, `record-step`, `known-gaps`.

## Architecture

Data flow: sources fetch and normalize -> SQLite upsert (`store/db.py`) -> linking passes -> enrichment and extraction -> export (`export/`).

### Source contract (`toronto_bids/sources/base.py`)

`Source` is a Protocol with `name: str`, `overwrite: bool`, `fetch(http) -> Iterable[dict]` (I/O), and `normalize(raw) -> Iterable[Row]` (pure, offline-testable against `tests/fixtures/`). Sources are listed in order in `pipeline.default_sources()`. To add one: implement the class in `sources/`, add its model to `models.py` and the `Row` union in `base.py` if new, append it to `default_sources()`, and declare any feed fields it reads in `sources/schema_check.py`.

### Ordering and upsert semantics

Order in `default_sources()` matters:

1. `schema_check` (feed drift detection)
2. OData spine (`overwrite=True`, authoritative)
3. CKAN backfill (`overwrite=False`, except `ckan_pipeline` which uses `overwrite=True` because it is the only source for `capital_project`, #69)
4. Ariba Discovery and suspended firms

For source fields, `db._upsert_keyed` prefers non-null incoming values with `overwrite=True` and non-null stored values with `overwrite=False`. A non-competitive upsert takes the parse from the source that supplied the winning raw amount and clears the review when the raw text changes. Feed rows are never deleted (`first_seen`/`last_seen` archive semantics). Only derived tables rebuilt from held bytes (supplier dimension, Ariba attachment index, extraction-backed bid/award tables) are swapped.

### Failure surfacing and known gaps

- `pipeline.run_source` catches exceptions per source so one failing feed never blocks others. Failures are logged to `sync_run`, printed to stderr as `FAILED <name>: ...`, and cause `tb sync` to exit non-zero. `pipeline.sync` returns `[(name, error)]` rather than raising (#178).
- Nightly steps run through `_run_step(steps, failures, name, fn, conn=conn)`, which writes a `sync_run` row (#176).
- Steps that swallow per-item errors (per portal, agency, Ariba event, or document) must append them to `failures` and call `_mark_if_swallowed_failures(steps, failures, conn, run_id, before_len, known_gaps)` immediately after `_run_step` (#178, #219, #223, #226).
- **Known gaps (#283)**: `_mark_if_swallowed_failures` separates reviewed gaps (`known_gaps.py`) from real failures in the nightly verdict only (standalone commands keep strict exit codes). Reviewed gaps cover `download:trca` entries in `data/trca_document_gaps.json`, `coverage:*` withholdings, `identity:*` refusals matching `data/known_agency_refusals.json` (regenerate with `tb known-gaps record-refusals` after review), and `waterfront:<month>` errors recorded verbatim in `data/known_gaps.json`. Anything else, including a registry load error, stays a failure.
- Skipped steps (such as `enrich-council` when it is not the 1st of the month) write no `sync_run` row.
- "What's new" counts diff real `SELECT COUNT(*)` queries before and after, never store return values (#142, #177, #218).
- `deploy/publish-data.sh` records `publish`, `r2_mirror`, `github_release_freshness`, `r2_freshness`, and `frontend_deploy_trigger` via `tb record-step` (helpers in `deploy/publish-lib.sh`, tested in `tests/test_publish_lib.py`).

### Schema drift (`sources/schema_check.py`)

`schema_check.py` samples OData and CKAN feeds (including nested award lists) on every sync and flags missing keys without halting ingestion. Update its declared schema whenever a normalizer reads a new field. Suspended firms checks its own table headers; Ariba is outside schema-check coverage.

### Keyspaces and linking

- **City competitive**: normalized 10-digit `document_number` (`linking/document_number.py`).
- **City non-competitive**: `workspace_number` (separate keyspace, no join to competitive).
- **Pre-2013 composite awards**: `composite_award.call_number` (`linking/call_number.py`, #96), matching shapes `3905-10-0097` and `317-2010` regardless of prefix. A trailing `, Contract No. ...` is a different identifier. Check `normalize_call_number` before stripping to 10 digits because call numbers can have 10 digits.
- **Agencies and portals**: board records use `(buyer_id, native_ref)`; portal pages use `(buyer, page URL)` with nullable, non-unique printed references.
- **Canonical procurement identity (#124)**: `linking/procurement.py` assigns stable archive IDs and redirects across keyspaces using explicit published links (`solicitation_link`, dual-key bids, reviewed crosswalks). No fuzzy matcher runs. Read `docs/designs/2026-10-22-procurement-identity.md` before changing resolution, migration, or export joins.
- **Sync linking passes** (`pipeline.linking_passes()`, run on every sync regardless of `--only`):
  - `title_cleanup`: clears placeholder titles (`Doc-3524228095`) to `NULL` and drops orphaned `title_source`. Always test `title IS NULL`, never `title LIKE 'Doc-%'`.
  - `ariba_bridge`: links `ariba_posting` rows to `solicitation` via `/RfxEvent/preview/<id>` in `ariba_posting_link` (legacy `discovery.ariba.com/rfx/<id>` URLs still redirect and work, #117). Unlinked postings export in `unlinked_ariba_postings`.
  - `amount_backfill` and `amount_labels`: backfill machine-parsed numeric amounts and apply human verdicts from `data/amount_labels.toml`. These passes also repair stored non-competitive parses and reviews against the current raw text.
  - `supplier_dimension`: rebuilds canonical `supplier` rows across awards, non-competitive contracts, suspended firms, bids (`bidder_name_raw`), composite awards, and agency records (#87; see `docs/supplier-entity-resolution.md`).

### Export seam (`export/`)

`build_export_document(conn)` in `export/document.py` is deterministic given `generated_at` (ordered queries, no file I/O). The full export resolves and commits procurement identities first (because nightly enrichment runs after sync linking passes). The default export then freezes SQLite and serializes every release format from that snapshot. Keep all document-shaping logic in `document.py`. Per-solicitation `documents` union Ariba leaves, Award Summary Forms, and staff reports using exact bridges only; internal hashes stay private and Ariba zip bytes are unpublished.

## Subsystem documentation

Read the relevant guide before changing these areas:

| Area | Doc |
|---|---|
| Deployment, nightly schedule, Claude API budget, systemd, R2 mirror, publishing | [`deploy/README.md`](deploy/README.md) |
| Document extraction, corpora, cache, classifier gate, backfill swap, parsing discipline | [`docs/document-extraction.md`](docs/document-extraction.md) |
| Asynchronous Claude batches, collector, lost receipts | [`docs/claude-batches.md`](docs/claude-batches.md) |
| City council agendas (BA/BD), titles, composite awards, Award Summary Forms, committee awards | [`docs/city-sources.md`](docs/city-sources.md) |
| Ariba attachment capture behind Respond, per-file bundling, version merging, UI quirks | [`docs/ariba-attachments.md`](docs/ariba-attachments.md) |
| Reviewed City Call Number crosswalks and reviewed cached bids | [`docs/city-call-crosswalk.md`](docs/city-call-crosswalk.md) |
| Published City Call Number / Ariba Doc aliases | [`docs/city-document-aliases.md`](docs/city-document-aliases.md) |
| Scoped City bids (multi-lot, alternatives, price variants) | [`docs/scoped-city-bids.md`](docs/scoped-city-bids.md) |
| Native-call participation in composite reports | [`docs/city-native-participation.md`](docs/city-native-participation.md) |
| Scanned BD report OCR and image-space reviews | [`docs/image-pdf-text.md`](docs/image-pdf-text.md) |
| 2013-2016 BD report capture, identity repairs, and guarded admission | [`docs/bd-report-recovery.md`](docs/bd-report-recovery.md) |
| Multi-term City committee/Council bulk backfill | [`docs/city-bulk-backfill.md`](docs/city-bulk-backfill.md) |
| Agency board reports (TRCA, Zoo, EP), bids&tenders portals, TRCA history & recovery | [`docs/agencies.md`](docs/agencies.md) |
| Bonfire public listings | [`docs/bonfire-listings.md`](docs/bonfire-listings.md) |
| MERX public capture and TTC supplementary reports | [`docs/merx.md`](docs/merx.md) |
| TTC Board historical capture and reviewed metadata withholding | [`docs/ttc-history.md`](docs/ttc-history.md) |
| Waterfront Toronto public awarded contracts | [`docs/waterfront-awards.md`](docs/waterfront-awards.md) |
| Supplier entity resolution (`supplier_key`) | [`docs/supplier-entity-resolution.md`](docs/supplier-entity-resolution.md) |

## Gotchas

- **Never aggregate `award_amount` or `contract_amount`.** They are raw `TEXT`. Aggregate `award_amount_numeric` / `contract_amount_numeric` (`REAL`). A `NULL` numeric value next to a non-null raw string means the string is not a single CAD amount.
- **Three amount tiers (#74):** raw (`award_amount`), machine-parsed (`award_amount_numeric`), and human-labelled (`award_amount_labelled` + `award_amount_verdict` from `toronto_bids/data/amount_labels.toml`). Write human judgments only to the labelled and verdict columns. Keep those columns off the `Award` and `NonCompetitive` source models. Verdicts are `amount`, `not_an_amount`, `corrupt`, `unknown` (including USD values like `'$1,311,936.00 USD'`), and `not_an_award`. Run `tb amounts unlabelled` to find unreviewed unparseable strings.
- **Filter `award` by `source` before aggregating.** Both `odata` and `ckan_awarded` store rows in `award`; filter to `source = 'odata'` to avoid double-counting.
- **Do not deduplicate `award` by `(document_number, supplier_name_raw)`.** One row is one award line (standing-offer call-ups repeat the same supplier). Uniqueness uses the expression index `award_line_key`, which coalesces nullable columns; `db._upsert_keyed`'s `_CONFLICT_TARGETS` must match it exactly.
- **Do not edit upstream City errors in normalizers.** Some amounts are wrong in both City feeds (for example, doc `3901175008` lists `$9.05B` to an individual). Store what the City published.
- **Ariba detail calls often return HTTP 500.** This is expected; reruns fill missing bridges idempotently.
- **CKAN resource UUIDs rotate on refresh.** Always resolve them at runtime via `package_show`.
- **Dead-end and declined sources (do not rebuild):**
  - Retired CKAN datasets backed by dead Lotus Notes endpoints: `call-documents-for-the-purchase-of-goods-and-services`, `competitive-call-award-results`, and empty slug `procurement-pipeline`.
  - Ariba public attachment API (`.../attachments/RFX/{id}`, returns HTTP 500) and AribaWeb Discovery HTML pagination (ephemeral widget IDs, replaced by `doIndexedSearch`).
  - Declined CKAN datasets (#69): `pcard-expenditures` (~820k rows, no join key) and `consulting-services-expenditures` (XLSX only, no datastore API).

## Agent workflow

- **Issue tracker:** GitHub issues on `CivicTechTO/toronto-bids`. `gh issue create` is blocked in agent sessions; use `gh api repos/CivicTechTO/toronto-bids/issues`, and confirm with the maintainer before any outward GitHub write. See `docs/agents/issue-tracker.md`.
- **Triage labels:** `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.
- **Domain docs:** Single-context layout (`GLOSSARY.md` + `docs/adr/`). See `docs/agents/domain.md`.
