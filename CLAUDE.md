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
- Two linking passes run after the sources on every sync, isolated the same way sources are (`pipeline._run_linking_pass` — a failure is recorded in `sync_run` and returned, never raised). They run regardless of `--only`, since they read whatever is in the store.
- `linking/supplier.py:build_supplier_dimension` rebuilds the supplier dimension from scratch: a normalized string key groups raw names across `award`/`noncompetitive`/`suspended_firm`; `supplier_id` FKs are cleared and re-backfilled each run. Legal suffixes (Inc, Ltd) are deliberately kept in the key.
- `linking/ariba.py:bridge_postings_to_spine` is the primary Ariba bridge: `solicitation.ariba_posting_link` embeds the rfx id (`/RfxEvent/preview/<id>`), so the spine names the join outright. `sources/ariba.py` also bridges inline from the detail `externalRfxId`, falling back to a `Doc(\d{10})` title token, but that path depends on the detail call — which 500s ~48% of the time — so it bridges roughly half. The linking pass fills the rest and only fills NULLs (the two agree wherever both fire). Older spine rows carry dead link formats (`discovery.ariba.com/rfx/`, merx, Lotus Notes, `n/a`) that are deliberately unparsed: those postings are long closed and absent from `ariba_posting`.
- A posting with no `document_number`, or one whose `document_number` has no `solicitation` row, stays unlinked and surfaces in the export's `unlinked_ariba_postings`.

### Council enrichment (`sources/council.py`) — not a Source

A separate opt-in step (`tb enrich-council`). TMMIS is Akamai-gated, so it needs a *headed* Playwright Chromium (headless is blocked); Playwright lives only behind the `council` extra. It fetches council decisions for each `suspended_firm.council_authority` and extracts staff-report PDFs with `pdftotext`.

### Export seam (`export/`)

`build_export_document(conn)` in `export/document.py` is deterministic given a `generated_at` (result-shaping queries ORDER BY, no file I/O); `export_json` is a thin serializer over it. A new publishing destination is another function over the same builder — keep all shaping logic in `document.py`.

## Gotchas

- `award` rows are per-source (`source` is part of the UNIQUE key: OData spine plus `ckan_awarded` cross-check), so naive `COUNT(*)`/`SUM(award_amount)` double-counts — filter to `source='odata'` or GROUP BY.
- Ariba detail calls return HTTP 500 ~40% of the time; runs are idempotent and later runs fill the gaps. Expected, not a bug.
- CKAN resource UUIDs rotate on refresh — they are resolved at runtime via `package_show`, never hardcoded.
- The design spec (`docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md`) records dead-end data sources (retired CKAN datasets, Ariba HTML shell, Ariba public attachment API) — don't rebuild against them.
