# Toronto Bids scraper

A `uv`-managed Python package that pulls City of Toronto procurement data into a
local SQLite store. No browser, no login, no cloud.

## Sources (P0/P1)

- **OData `feis_solicitation_published`** — the solicitation lifecycle spine
  (open / awarded / cancelled), the authoritative source.
- **OData `feis_non_competitive_published`** — non-competitive (sole-source) awards.
- **CKAN `tobids-awarded-contracts` / `tobids-all-open-solicitations` /
  `tobids-non-competitive-contracts`** — backfill for the spine.

Everything competitive is keyed on the normalized 10-digit `document_number`.
Non-competitive awards are a separate keyspace (`workspace_number`).

## Usage

```shell
cd scrapers
uv sync
uv run tb sync            # fetch all sources into files/bids.sqlite
uv run tb sync --only odata_solicitations,ckan_awarded
uv run tb status          # row counts
uv run pytest             # tests (offline; uses fixtures)
```

Set `TB_DATA_DIR` to change where `bids.sqlite` and downloads live (default `scrapers/files/`).

See `../docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md`
(from repo root) for the full design, source inventory, and the later phases
(Ariba Discovery JSON, attachments, council/PDF enrichment).

## Data notes

`award` rows are stored per-source: OData (`source='odata'`) is the spine, and CKAN's
awarded-contracts dataset (`source='ckan_awarded'`) is a cross-check, so the same
`(document_number, supplier_name_raw)` pair can legitimately appear once per source
(`source` is part of the table's UNIQUE key). A naive `COUNT(*)` or `SUM(award_amount)`
over `award` will double-count. Consumers should either filter to `source='odata'` or
`GROUP BY document_number, supplier_name_raw` to get a de-duplicated view. Fuzzy
cross-source supplier de-duplication is a later phase.
