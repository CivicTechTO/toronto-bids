# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CivicTechTO project that archives City of Toronto procurement data (solicitations, awards, non-competitive contracts, Ariba Discovery postings, suspended firms) into a local SQLite store and exports it as one public JSON artifact — so the record stays available after bids close. All active code is the `scrapers/` Python package (`uv`-managed, Python 3.12+, installs a `tb` CLI). No browser, login, cloud, or API keys for the core pipeline.

## Commands

Everything runs from `scrapers/`:

```shell
cd scrapers
uv sync                                   # install deps (dev group with pytest included)
uv run pytest                             # all tests — offline, fixture-based, no network
uv run pytest tests/test_odata.py        # one file
uv run pytest tests/test_odata.py::test_normalize_solicitation_yields_spine_and_award   # one test
uv run tb sync                            # fetch all sources into files/bids.sqlite (exit 1 if any source failed)
uv run tb sync --only odata_solicitations,ckan_awarded
uv run tb status                          # row counts + last run per source
uv run tb export                          # write JSON artifact (default <DATA_DIR>/export/bids.json)
```

- **No lint/format/typecheck is configured** (no ruff/mypy/black). Don't invent those commands.
- CI (`.github/workflows/tests.yml`) runs `uv sync --locked && uv run pytest` — after changing dependencies, re-lock and commit `uv.lock` or CI fails.
- Council tests skip silently without `pdftotext` (`brew install poppler`); CI installs poppler so they run there.
- `TB_DATA_DIR` env var relocates the DB and downloads (default `scrapers/files/`).
- Opt-in council enrichment: `uv sync --extra council && uv run playwright install chromium`, then `uv run tb enrich-council` (`--virtual-display` for Xvfb on headless servers). Not part of `tb sync`.
- `tb sync` hits live City endpoints and is slow; the tests are the dev loop.

## Architecture

Data flow: sources fetch/normalize → SQLite upsert (`store/db.py`) → supplier-linking pass → export (`export/`).

### Source contract (`toronto_bids/sources/base.py`)

`Source` is a duck-typed Protocol: `name: str`, `overwrite: bool`, `fetch(http) -> Iterable[dict]` (does I/O), `normalize(raw) -> Iterable[Row]` (pure — testable against `tests/fixtures/` without network; keep it that way). There is no registry: sources are a hardcoded ordered list in `pipeline.default_sources()`. To add one: write the class in `sources/`, add its model to `models.py` and the `Row` union in `base.py` if new, append an instance to `default_sources()`, and declare any feed fields it reads in `sources/schema_check.py`.

### Ordering and overwrite semantics

Order in `default_sources()` matters: `schema_check` first (drift detection), then the OData spine (`overwrite=True`, authoritative), then CKAN backfill (`overwrite=False`), then Ariba and suspended firms. Both upsert modes COALESCE (`db._upsert_keyed`): with `overwrite=True` a new non-NULL value wins but NULL never wipes an existing value; with `overwrite=False` only currently-NULL columns get filled. Rows are never deleted — archive semantics with `first_seen`/`last_seen` on every data table.

### Per-source isolation and failure surfacing

`pipeline.run_source` catches all exceptions: one failing source never stops the others, and partial rows are committed. Failures are recorded in the `sync_run` table, printed to stderr as `FAILED <name>: ...`, and make `tb sync` exit non-zero. This is the only place a source exception is seen — silence here means silence everywhere.

### Schema drift detection (`sources/schema_check.py`)

Normalizers read feed fields with `raw.get(...)`, so a field the City renames silently NULLs a column forever. `schema_check.py` declares exactly the fields the OData and CKAN normalizers read and samples those five feeds on each sync, failing loudly on missing keys. When a normalizer starts reading a new field, add it to the declared sets in the same change. Ariba and suspended-firms are outside its coverage (suspended-firms parsing raises on header drift itself; Ariba field drift is unguarded).

### Linking

- Everything competitive is keyed on the normalized 10-digit `document_number` (`linking/document_number.py`: strip non-digits, require exactly 10, reject a placeholder denylist). Non-competitive contracts live in a separate keyspace (`workspace_number`) — there is no join between them.
- After every sync, `linking/supplier.py:build_supplier_dimension` rebuilds the supplier dimension from scratch: a normalized string key groups raw names across `award`/`noncompetitive`/`suspended_firm`; `supplier_id` FKs are cleared and re-backfilled each run. Legal suffixes (Inc, Ltd) are deliberately kept in the key.
- Ariba postings bridge to a document number via the detail `externalRfxId`, falling back to a `Doc(\d{10})` token in the title; unbridged postings keep a NULL `document_number`.

### Council enrichment (`sources/council.py`) — not a Source

A separate opt-in step (`tb enrich-council`). TMMIS is Akamai-gated, so it needs a *headed* Playwright Chromium (headless is blocked); Playwright lives only behind the `council` extra. It fetches council decisions for each `suspended_firm.council_authority` and extracts staff-report PDFs with `pdftotext`.

### Export seam (`export/`)

`build_export_document(conn)` in `export/document.py` is deterministic given a `generated_at` (result-shaping queries ORDER BY, no file I/O); `export_json` is a thin serializer over it. A new publishing destination is another function over the same builder — keep all shaping logic in `document.py`.

## Gotchas

- `award` rows are per-source (`source` is part of the UNIQUE key: OData spine plus `ckan_awarded` cross-check), so naive `COUNT(*)`/`SUM(award_amount)` double-counts — filter to `source='odata'` or GROUP BY.
- Ariba detail calls return HTTP 500 ~40% of the time; runs are idempotent and later runs fill the gaps. Expected, not a bug.
- CKAN resource UUIDs rotate on refresh — they are resolved at runtime via `package_show`, never hardcoded.
- The design spec (`docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md`) records dead-end data sources (retired CKAN datasets, Ariba HTML shell, Ariba public attachment API) — don't rebuild against them.
