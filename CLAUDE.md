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
- `uv run tb enrich-titles` recovers titles the City never published (#65). **Offline by default** — it reads agendas already cached under `<DATA_DIR>/council/agendas/` plus the legacy archive, so it needs no browser. `--scrape` fetches Bid Award Panel agendas first (headed browser, needs the `council` extra; ~10 min cold, seconds once cached — an agenda on disk is never refetched). Not part of `tb sync`.
- `tb sync` hits live City endpoints and is slow; the tests are the dev loop.

## Architecture

Data flow: sources fetch/normalize → SQLite upsert (`store/db.py`) → supplier-linking pass → export (`export/`).

### Source contract (`toronto_bids/sources/base.py`)

`Source` is a duck-typed Protocol: `name: str`, `overwrite: bool`, `fetch(http) -> Iterable[dict]` (does I/O), `normalize(raw) -> Iterable[Row]` (pure — testable against `tests/fixtures/` without network; keep it that way). There is no registry: sources are a hardcoded ordered list in `pipeline.default_sources()`. To add one: write the class in `sources/`, add its model to `models.py` and the `Row` union in `base.py` if new, append an instance to `default_sources()`, and declare any feed fields it reads in `sources/schema_check.py`.

### Ordering and overwrite semantics

Order in `default_sources()` matters: `schema_check` first (drift detection), then the OData spine (`overwrite=True`, authoritative), then CKAN backfill (`overwrite=False`), then Ariba and suspended firms. `ckan_pipeline` is the one CKAN source with `overwrite=True`: no spine covers the forward-looking `capital_project` table, so CKAN is authoritative there (#69). Both upsert modes COALESCE (`db._upsert_keyed`): with `overwrite=True` a new non-NULL value wins but NULL never wipes an existing value; with `overwrite=False` only currently-NULL columns get filled. Rows are never deleted — archive semantics with `first_seen`/`last_seen` on every data table.

### Per-source isolation and failure surfacing

`pipeline.run_source` catches all exceptions: one failing source never stops the others, and partial rows are committed. Failures are recorded in the `sync_run` table, printed to stderr as `FAILED <name>: ...`, and make `tb sync` exit non-zero. This is the only place a source exception is seen — silence here means silence everywhere.

### Schema drift detection (`sources/schema_check.py`)

Normalizers read feed fields with `raw.get(...)`, so a field the City renames silently NULLs a column forever. `schema_check.py` declares exactly the fields the OData and CKAN normalizers read and samples those five feeds on each sync, failing loudly on missing keys. When a normalizer starts reading a new field, add it to the declared sets in the same change. Ariba and suspended-firms are outside its coverage (suspended-firms parsing raises on header drift itself; Ariba field drift is unguarded).

### Linking

- Everything competitive is keyed on the normalized 10-digit `document_number` (`linking/document_number.py`: strip non-digits, require exactly 10, reject a placeholder denylist). Non-competitive contracts live in a separate keyspace (`workspace_number`) — there is no join between them.
- Three post-source passes run on every sync, isolated the same way sources are (`pipeline._run_linking_pass` — a failure is recorded in `sync_run` and returned, never raised). They run regardless of `--only`, since they read whatever is in the store.
- `title.py:clear_placeholder_titles` runs first. For ~72% of solicitations the City publishes the document number *as* the title (`Doc-3524228095`, `Doc-Ariba Doc No. 2243638006 RFP NO. 9118205024`); `title.py:clean_title` normalizes those to NULL at ingest, because a non-NULL placeholder both clobbered real titles on multi-record documents and blocked backfill sources (COALESCE guards NULL, not *worse*). **`title IS NULL` means "no title published" — do not use `title LIKE 'Doc-%'`**, which both misses placeholders (`'3586141004'`, `'Tender - Call Doc4247073892'`) and catches real titles that merely lead with the doc number (`'Doc4171532487 Request for Quotations for Uptown Yonge BIA - Benches'`). The pass exists because COALESCE keeps an existing placeholder when the incoming value is NULL, so rows written before this could never self-clear.
- `linking/supplier.py:build_supplier_dimension` rebuilds the supplier dimension from scratch: a normalized string key groups raw names across `award`/`noncompetitive`/`suspended_firm`/`bid`; `supplier_id` FKs are cleared and re-backfilled each run. Legal suffixes (Inc, Ltd) are deliberately kept in the key. **`bid` roughly doubles the dimension (4,189 → 6,728)** and that is the point (#87): most bidders never win, so a dimension built from winners alone cannot answer who loses, who only ever bids unopposed, or whether a suspended firm kept bidding. `bid` names its supplier in `bidder_name_raw`, not `supplier_name_raw` — see `_NAME_COLUMN`.
- `linking/ariba.py:bridge_postings_to_spine` is the primary Ariba bridge: `solicitation.ariba_posting_link` embeds the rfx id (`/RfxEvent/preview/<id>`), so the spine names the join outright. `sources/ariba.py` also bridges inline from the detail `externalRfxId`, falling back to a `Doc(\d{10})` title token, but that path depends on the detail call — which 500s ~48% of the time — so it bridges roughly half. The linking pass fills the rest and only fills NULLs (the two agree wherever both fire). Older spine rows carry dead link formats (`discovery.ariba.com/rfx/`, merx, Lotus Notes, `n/a`) that are deliberately unparsed: those postings are long closed and absent from `ariba_posting`.
- A posting with no `document_number`, or one whose `document_number` has no `solicitation` row, stays unlinked and surfaces in the export's `unlinked_ariba_postings`.

### Council enrichment (`sources/council.py`) — not a Source

A separate opt-in step (`tb enrich-council`). TMMIS is Akamai-gated, so it needs a *headed* Playwright Chromium (headless is blocked); Playwright lives only behind the `council` extra. It fetches council decisions for each `suspended_firm.council_authority` and extracts staff-report PDFs with `pdftotext`.

### Title recovery (`sources/bid_award_panel.py`, `sources/legacy_titles.py`) — not Sources

`tb enrich-titles`. The City publishes the document number *as* the title for ~72% of solicitations, so `title` is NULL for most of the awarded record (#70). Two sources fill it, and **both only ever touch a NULL — a title the City published always wins**:

- **Bid Award Panel agendas** — all 475 (2017-01-04 → present), scraped with one headed Chromium for the whole run (not one per page as `council.py` does; that is fine for 3 suspended firms and ruinous across 475 meetings). Raw HTML is cached under `<DATA_DIR>/council/agendas/`, so re-parsing never re-drives a browser and the pre-Ariba pages stay available for #77. Also populates `council_item` (#68). **References cannot be derived** — the City's schedule omits `MTG #` before the 2022-2026 term, and date-order inference is wrong in both directions (2017.BA1 and 2017.BA2 are both 2017-01-04), so `discover_meetings` probes and confirms against each page's own stated date.
- **The legacy archive's Ariba posting pages** — `<title>` is the solicitation's real title. Offline; the bytes are on disk from the rescue.
- **Pre-Ariba council items** (`match_pre_ariba_titles`, #77) — 2017-2018 agendas name no document number (Toronto adopted Ariba ~2019), so they are matched on **(supplier, award value)** instead. Council publishes three figures per award; `award_amount` is the **"net of all applicable taxes"** one — calibrated against 980 Ariba-era items where the document number gives ground truth (820 matched; "including HST" matched 0). **The value carries the match; the supplier only confirms it** — 4,725 of 4,861 title-less amounts occur exactly once. So the supplier check is looser here (`supplier_tokens`: legal form and `&`/`and` stripped, one shared token) than `linking/supplier.py`'s `supplier_key`, and safely: that key must not MERGE two firms into one dimension row, this one only has to confirm a value match. Tuned against 777 Ariba-era items where the document number gives ground truth — exact key 62.8% recall, one shared token 97.7%, **0 false positives at either**. Only a *unique* match is taken; a wrong title is worse than none.

The same cached agendas also populate `bid` — **12,443 bids including the losers**, which rewrite spec §2.5.2 calls "never published anywhere. **Unrecoverable.**" (#84). They are tabulated on every agenda in real `<table>` markup. This is what lets the archive ask whether a procurement was *competitive* (280 solicitations drew a single bidder), not merely what it cost. Two traps: **`hst_basis` is load-bearing** — 5,801 bids are quoted including HST and 4,097 excluding, so comparing across them without it is wrong; and **`bid_price` is not `award_amount`** — a bid excludes contingency, so the same item's award value is higher. The City also writes outcomes in the price column (`Non-Compliant`, `No bid`), which is why the raw string is kept and `bid_price_numeric` is NULL for exactly those.

The cached agendas also populate `background_pdf` (3,142 staff reports, attributed to the item that links each one). Spec §2.3 calls these "the richest award context" but says they have **"no index"** — the agendas *are* the index. Only the URL is recorded; the bytes are a separate pass, and unlike the agendas these are plain HTTP (verified 200 `application/pdf`), so fetching them needs no browser.

**Reach is bounded by history, not effort.** Toronto adopted Ariba ~2019; earlier agendas identify awards by Call Number (`6032-16-3114`), the spine is keyed on the 10-digit Ariba number backfilled later, and `Contract_Number_Purchase_Order` is empty on all 7,592 feed records — so **there is no join key for 2012-2018** and ~4,100 title-less rows are unreachable this way (#77 proposes supplier+amount instead). Match on *any* 10-digit number, never on the word "Ariba": the labels vary ("Ariba Document Number", "Ariba Doc.", "Tender Call Number", "Request for Quotation") and keying on the vocabulary silently drops the 2019-2020 items.

A legacy posting title outranks a Bid Award Panel heading — the posting page names the solicitation, the council heading describes the award. That precedence lives in `legacy_titles`' query, not in call order.

### Export seam (`export/`)

`build_export_document(conn)` in `export/document.py` is deterministic given a `generated_at` (result-shaping queries ORDER BY, no file I/O); `export_json` is a thin serializer over it. A new publishing destination is another function over the same builder — keep all shaping logic in `document.py`.

## Gotchas

- **Never aggregate `award_amount` / `contract_amount`** — they are `TEXT` holding the City's string verbatim (`"$1,317,169.92 CAD"`, `"kj"`, three amounts concatenated). `SUM()` coerces text prefixes to 0 or truncates, and SQLite sorts text above every number so `award_amount > 1000` matches *every* row. Aggregate `award_amount_numeric` / `contract_amount_numeric` (`REAL`, parsed by `toronto_bids/amount.py`) instead. A NULL numeric beside a non-NULL raw string means the raw value is not a single CAD amount (77 of 13,559 awards) — deliberate, not missing data.
- `award` rows are per-source (`source` is part of the key: OData spine plus `ckan_awarded` cross-check), so a naive `COUNT(*)`/`SUM(award_amount_numeric)` double-counts — filter to `source='odata'` or GROUP BY. Both hazards apply at once: the sum must use the numeric column *and* filter by source.
- Some award amounts are implausible in **both** feeds (doc `3901175008`: `9054510208` — $9.05B to an individual, against a ~$16B city budget). Upstream data, not a parsing bug — don't "fix" it in a normalizer; `amount.py` correctly converts what the City published. 3 such rows carry ~$15B of the total, so `SUM(award_amount_numeric)` is *faithful* without being *trustworthy*.
- `award` holds one row per award **line**, not per (document, supplier): a document can award the same supplier many times (standing-offer call-ups — `Cascades Recovery Inc.` has 10 lines on doc `9154157025`). Uniqueness is an expression index (`award_line_key`) that COALESCEs the nullable key parts, because SQLite treats NULLs as distinct and 864 awards have no amount. **`db._upsert_keyed`'s conflict target must match that expression exactly** — see `_CONFLICT_TARGETS`.
- Ariba detail calls return HTTP 500 ~40% of the time; runs are idempotent and later runs fill the gaps. Expected, not a bug.
- CKAN resource UUIDs rotate on refresh — they are resolved at runtime via `package_show`, never hardcoded.
- The design spec (`docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md`) records dead-end data sources (retired CKAN datasets, Ariba HTML shell, Ariba public attachment API) — don't rebuild against them.
