# Toronto Bids Scraper — Scratch Rewrite Design

**Date:** 2026-07-14
**Status:** Approved (design phase)
**Author:** Alex Olson (with Claude Code)

## 1. Problem & context

The Toronto Bids Project archives City of Toronto procurement solicitations so the
public can access them after they close. The original scraper (`scrapers/`, Python +
Selenium) drove the SAP Ariba Discovery web UI, downloaded solicitation HTML +
attachment ZIPs, and pushed the result through Azure (Key Vault → File Share → an
Azure Function → SQL DB → a PHP/Angular site).

It has been effectively dead for roughly two years:

- The Azure upload Function (`upload-function20230528103644.azurewebsites.net/api/FlatUpload`)
  returns **HTTP 500**.
- In March 2024 (commit `bed0e15`) the open-data ingest **and** the JSON upload were
  commented out — later deleted (`a40f596`) — because "the city changed the way they
  publish data". Since then the scraper has written only to local disk and transmitted
  nothing.
- The Ariba scrape itself is brittle: it hardcodes **server-generated ephemeral AribaWeb
  widget IDs** (e.g. `//*[@id="_xjqay"]`, `//*[@id="_5wq_j"]`). Ariba regenerates these,
  so the scrape breaks every few months. The current live IDs (`_rqqsbd`, `_zj2gzb`) are
  already different from the hardcoded ones.
- `scrapers/open_data.py` still fetches a **dead** CKAN dataset
  (`call-documents-for-the-purchase-of-goods-and-services`) whose Lotus Notes backend is
  retired (502); the fetch would throw.

**Decision: rewrite from scratch.** This document is the design.

### Goals

1. Pull **everything publicly available downstream of the Toronto Bids Portal**
   and **link the sources** wherever a solicitation can be stitched across them.
2. **Local-first**: produce a clean, queryable local dataset (SQLite + files) with **no
   cloud dependency**. Publishing to any destination is a separate, optional concern
   behind an isolated seam (destination is TBD — not necessarily Azure).
3. **Auth-optional**: everything public works with no credentials; anything requiring an
   Ariba supplier login (attachment PDFs) is a pluggable component that degrades
   gracefully when credentials are absent.
4. Be **robust and re-runnable** — an idempotent batch job that survives upstream flakiness
   and warns early when a source drifts.

### Non-goals (v1)

- Slack notifications, a Docker image, and a scheduler are **explicitly dropped** from v1
  (the operational surface is a blank slate; these can wrap the CLI later if wanted).
- Rebuilding the Azure pipeline or the website.

## 2. Source landscape (live-verified 2026-07-14)

A discovery sweep live-probed every candidate source. The key finding: **the City now
publishes structured, no-auth, daily-refreshed data that makes the fragile Ariba web scrape
unnecessary for metadata.** Ariba is now needed only for attachment PDFs (auth) and for
archiving currently-open posting detail.

### 2.1 Sources we pull (Tier 1 — pure HTTP, no browser, no auth)

| Source | What | Access | Freshness |
|---|---|---|---|
| **OData `feis_solicitation_published`** — THE SPINE | Full competitive lifecycle in one table: 908 Open + 6,514 Awarded + 231 Cancelled (~7,653 rows), 63 fields/record incl. nested `Awarded_Suppliers[]`, buyer, division, dates, award amounts, `Ariba_Discovery_Posting_Link`. Status transitions in place. | `GET https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/feis_solicitation_published?$format=application/json;odata.metadata=none&$count=true&$top=1000&$skip=N` (page via `$skip`/`$top`) | Live; newest `__ModifiedOn` = today |
| **OData `feis_non_competitive_published`** | Non-competitive/sole-source master (~2,955 rows). `Non_Competitive_Reference_Number`, reason, supplier, `Council_Authority_Link_to_Staff_Report`. | Same service, `.../pmmd_solicitations/feis_non_competitive_published?...` | Live |
| **CKAN `tobids-awarded-contracts`** | Competitive awards, one row per successful supplier (~7,574 rows). `Document Number`, RFx type, `Successful Supplier`, `Award` ($), date, division, buyer. | `datastore_search?resource_id=<uuid>` or `/datastore/dump/<uuid>` (one request) | Live; newest award = today |
| **CKAN `tobids-all-open-solicitations`** | Solicitation notices (~903 rows; archive back to 2008 — only ~44 truly open). `Document Number`, RFx/NOIP type, division, buyer, wards. | `datastore_search` / dump | Live |
| **CKAN `tobids-non-competitive-contracts`** | Sole-source awards (~2,920 rows). `Workspace Number`, `Reason`, `Supplier Name`, `Contract Amount`, `Contract Date`. | `datastore_search` / dump | Live |
| **CKAN `capital-project-pipeline`** | 46 forward-looking upcoming solicitations (no doc/award ids yet). Built 2026-07-16 (#69) — see §2.1.1. | `datastore_search?resource_id=<uuid>` | ~6 wks |
| **Ariba Discovery leads search** (`doIndexedSearch`) | Enumerate currently-open Toronto postings + their `rfxID` (Ariba internal posting id). | `POST https://service.ariba.com/Network/discoveryweb/search/public/v1/doIndexedSearch?siteName=Quote` body `{"pageSize":1000,"pageNum":0,"searchType":"Quote","sortBy":"RESPONSE_DEAD_LINE","filters":[]}`; filter `customerName=="City of Toronto"` client-side | Live |
| **Ariba Discovery detail** (`/rfx/{rfxId}`) | Per-posting detail incl. `externalRfxId` (= `Doc##########`) needed to join to the spine, UNSPSC categories, dates. | `GET https://service.ariba.com/Network/discoveryweb/api/public/v1/rfx/{rfxId}` with `Accept: application/json` | Live, **open postings only** (closed → 401); **~48% return HTTP 500 → retry/skip** |

#### 2.1.1 Considered and declined (resolved 2026-07-16, #69)

Two datasets were listed in this table as Tier 1 "sources we pull" and never implemented.
That was not an oversight: **they were listed because they are easy to fetch, not because they
belong in this archive**, and §2.5.6 already says so — "no join key; orthogonal spend data".
The table contradicted itself. Recording the decision rather than leaving every future reader
to re-litigate whether the implementation is incomplete.

| Declined | Why |
|---|---|
| **`pcard-expenditures`** | **819,993 rows** (measured 2026-07-16) of free-text merchant names, ~5-month lag, no join key. It would dominate the export artifact while linking to nothing, and feeding 820k more free-text merchant strings to the fuzzy supplier dimension (§2.5.4) would manufacture false links, not insight. The City publishes it directly; we would add nothing. |
| **`consulting-services-expenditures`** | XLSX only — **no datastore API** (confirmed 2026-07-16: 4 resources, 0 datastore-active) — annual, not joinable. Lowest value-to-effort of the three. |

`capital-project-pipeline` **was** built, because it is a different case despite sitting in the
same row of the table. It does not join the spine either — a project has no document number
until it is actually solicited — but it is the only forward-looking source in the entire
landscape, it is 46 rows and one request, and **the City refreshes it, so entries drop off as
they are sourced**. What the City *planned* to buy is preserved nowhere else once it stops
planning. That vanishing act is precisely what this archive exists for, whereas pcard spend
sits permanently on the City's own portal.

It is the one CKAN source with `overwrite=True`: no spine covers it, so CKAN is authoritative
there and a project whose target year slips must land rather than be COALESCEd away.

**CKAN API base:** `https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/`
Resolve CKAN resource UUIDs **at runtime** via `package_show?id=<slug>` — they rotate on
refresh. `datastore_search_sql` is disabled (404); use `datastore_search` params.

**OData response shape** (re-verified live 2026-07-14): with `odata.metadata=none` the service
returns a flat JSON object `{ "@odata.count": <int>, "value": [ {record}, ... ] }` — records
are in `value` (not a `d`/`results` wrapper) and the total is `@odata.count`. Page with
`$skip`/`$top` until `len(value) == 0`.

### 2.2 Sources we pull (Tier 2)

| Source | What | Access | Notes |
|---|---|---|---|
| **Suspended & Disqualified Firms** | Supplier suspension registry (currently 3 rows): name, status, dates, type, council `Authority` (`2025.GG26.3`). | `GET https://www.toronto.ca/business-economy/doing-business-with-the-city/searching-bidding-on-city-contracts/suspended-disqualified-firms/`; parse single `<table>` with lxml; diff on schedule | Low brittleness |
| **Ariba attachment fetcher** (OPTIONAL, AUTH) | The actual RFx documents/spec PDFs/addenda for a `Doc##########`. | Toronto-realm Ariba **supplier login** via Playwright; targets from the rfxId→doc bridge | Only real use for auth. Public attachment API is a dead end (see §2.4). Off by default; enabled when credentials present |

### 2.3 Sources we pull (Tier 3 — high brittleness enrichment)

| Source | What | Access | Notes |
|---|---|---|---|
| **TMMIS council agenda-items** | Council-approved awards as agenda items/decisions (~95k items; filter `termId=8` current). `reference` (`YYYY.CCnn.n`). | `GET /council/api/csrf.json` for `XSRF-TOKEN`, then `POST https://secure.toronto.ca/council/api/multiple/agenda-items.json` with `X-XSRF-TOKEN` | Akamai-gated + CSRF; index has no procurement fields — drill into item text |
| **Background-file PDFs** | Full award staff reports (richest award context) as PDF text. | `GET https://www.toronto.ca/legdocs/mmis/{year}/{committee}/bgrd/backgroundfile-{id}.pdf`; parse with pdftotext | **No index** — source `(year, committee, id)` tuples from TMMIS; committee segment must be correct |

### 2.4 Dead ends — do NOT rebuild

- **`call-documents-for-the-purchase-of-goods-and-services`** (CKAN) — retired; Lotus Notes
  backend dead (502). `is_retired=true`. Replaced by `tobids-all-open-solicitations`.
  **`scrapers/open_data.py` still fetches this and must be deleted.**
- **`competitive-call-award-results`** (CKAN) — same dead Lotus Notes backend. Replaced by
  the `tobids-awarded-contracts` + `tobids-non-competitive-contracts`.
- **`procurement-pipeline`** slug — empty shell (`num_resources:0`). Use `capital-project-pipeline`.
- **`discovery.ariba.com/rfx/{id}` HTML** — empty SPA shell; use the JSON detail API.
- **Ariba public attachment API** (`.../attachments/RFX/{id}`) — returns 500 for every id
  anonymously and attachment ids are never surfaced publicly. Unusable even with auth; only
  the authenticated Ariba Sourcing UI yields attachments.
- The **AribaWeb Discovery profile HTML pagination** (the old scraper's path) — stateful
  WebObjects with ephemeral widget IDs. Superseded by the JSON search API (`doIndexedSearch`).

### 2.5 Coverage gaps (what "everything downstream" still cannot give us)

1. **Bid documents/specs/attachments** live only inside the authenticated Ariba Sourcing
   event (supplier login + browser). Biggest gap.
   **UPDATE (2026-07-15, P4b exploration): a supplier login is NOT sufficient.** Live test
   with a real Toronto-realm supplier account (logged in, AN…011) opening an open Toronto
   event returned *"You do not have the correct permission to view the event."* Event
   documents are visible only to accounts **participating in that specific event** (invited /
   responded). There is no way to archive attachments across all open solicitations without
   actively registering intent-to-bid on each one (spamming the buyer) — so the attachment
   fetcher (P4b) is **DEFERRED / effectively infeasible for a public archive**. It could only
   ever fetch attachments for the handful of events a given account actually participates in
   (a personal-workflow tool, not an archive component). The public archive already captures
   everything publicly available (metadata, awards, non-competitive, open-posting Discovery
   JSON, suspended firms).
2. ~~**Losing bidders and bid prices** are never published anywhere. **Unrecoverable.**~~
   **WRONG — resolved 2026-07-16 (#84).** They are tabulated on **every Bid Award Panel
   agenda**, in real `<table>` markup, and have been all along. 12,387 bids from 5,096
   distinct bidders are now stored, parsed out of the 475 agendas cached under
   `<DATA_DIR>/council/agendas/` — offline, no browser, no PDFs. 9,814 carry a parseable
   price; the rest carry an outcome (`Non-Compliant`, `No bid`) which is itself the reason a
   bid lost. 3,341 are pre-2019.

   This was the most consequential error in this document. It declared the archive's most
   valuable dataset non-existent, and it went unchallenged because nobody looked at an
   agenda. Two lessons worth keeping: **"unrecoverable" is a claim about where we looked,
   not about the world**; and this data was reachable *only* because #65 scraped those
   agendas for an unrelated reason (titles) and cached the raw HTML.

   Caveats now in `CLAUDE.md`: `hst_basis` is load-bearing (5,801 bids quote including HST,
   4,097 excluding), and a bid price is not an award amount (a bid excludes contingency).
3. **Closed-posting Ariba detail/attachments** — public detail API serves open postings only.
   Must **archive at scrape time**; no backfill. **Caveat (2026-07-16, #78):** that 401 is
   an *API* result. The UI has never been tested with a real browser, and §2.4's dismissal of
   the Discovery HTML as "an empty SPA shell" was assessed against plain HTTP — a browser
   renders the SPA, which is how the legacy archive's pages came to carry titles. If an
   authenticated view serves closed postings, this gap narrows substantially.
4. **Canonical supplier ID** — none exists; suppliers are free text everywhere. Cross-source
   supplier linkage is fuzzy only.
5. **Non-competitive → competitive** — non-competitive rows carry no doc number; permanent
   separate keyspace.
6. **PCard / consulting spend → bids** — no join key; orthogonal spend data. Formally
   declined 2026-07-16 (#69) — see §2.1.1.
7. **Pre-retirement historical records** that lived only in the dead Lotus Notes feeds are gone.
8. **Solicitation titles before ~2019 — the archive's largest hole.** Added 2026-07-16 (#65),
   which this document never recorded despite it being the biggest gap in the dataset.

   For ~66% of solicitations the City publishes the **document number as the title**
   (`Doc-3524228095`), which carries nothing the primary key does not. Those are stored NULL
   (#70), so `title IS NULL` means "the City published no title". **4,913 of 7,444 have no
   subject line** — the awarded record is a number, a supplier and an amount, with no
   statement of what was bought.

   | source | titles |
   |---|---|
   | `odata` — the City's own feed | 2,053 |
   | `bid_award_panel` — council agendas (#65) | 342 |
   | `legacy_ariba_html` — the rescued archive (#65) | 136 |

   **The remaining gap is 4,616 rows in 2012–2019, and it is bounded by history, not
   effort.** Toronto adopted Ariba around 2019. Earlier council agendas identify awards by
   Call Number (`2017.BA1.2`: "Award of Call Number 6032-16-3114 to MeteoGroup…"), the spine
   is keyed on the 10-digit Ariba number backfilled later, and
   `Contract_Number_Purchase_Order` is **empty on all 7,592 feed records**. There is no join
   key in either direction. Verified dead ends: TMMIS plain HTTP (403, Akamai), CKAN council
   voting records (742k vote rows → 2 usable titles), and the 46 unread OData fields (all
   metadata, no title).

   Two candidate routes remain, both open: **#77** (match pre-Ariba council items on
   supplier+amount rather than identifier — 3,341 pre-2019 bids now carry both) and **#78**
   (browser-scrape publicly visible Ariba posting detail; the spine holds 1,681
   `ariba_posting_link` values against the 42 postings currently reachable).

## 3. The linking model

**Central entity: `solicitation`, keyed by the normalized 10-digit `document_number`** (the
Ariba Solicitation Document Number). This is the single spine for the entire competitive
lifecycle. Non-competitive awards are a **separate entity on their own keyspace**.

### 3.1 Critical structural fact

You **cannot** reconstruct the lifecycle by joining the two CKAN tables to each other:
`all-open-solicitations.Document Number ∩ awarded-contracts.Document Number` = **only 17 of
872 distinct docs (1.9%)**. They are complementary status snapshots, not stitchable views.
**Route the open→awarded transition through OData `Status`, never through a CKAN-to-CKAN join.**
OData is the spine; CKAN feeds are pre-flattened projections / backfill onto the same key.

### 3.2 Verified joins

| Join | Keys | Transform | Confidence | Evidence |
|---|---|---|---|---|
| CKAN awarded ↔ OData | `Document Number` ↔ `Solicitation_Document_Number` | exact string | **CERTAIN** | 6,307/6,307 (100%) |
| CKAN solicitations ↔ OData | `Document Number` ↔ `Solicitation_Document_Number` | exact string | **CERTAIN** | 872/872 (100%), all `Status=Open` |
| Ariba Discovery ↔ spine | detail `externalRfxId` (`Doc5725384704`) ↔ Document Number | **strip non-digits → require 10 digits** | **HIGH** | 6/6 live open postings matched CKAN+OData |
| OData ↔ Ariba posting (direct) | `Ariba_Discovery_Posting_Link` (`…/rfx/21623320`) ↔ Ariba `id` | regex trailing rfxId | **HIGH where populated** | Populated on only 1,420/7,653; polymorphic (also MERX ~313, dead `.nsf`, one `test.com`) — classify by host first |
| CKAN non-competitive ↔ OData non-competitive | `Workspace Number` ↔ `Non_Competitive_Reference_Number` | exact string | **CERTAIN** | 2,811/2,811 (100%) |
| Suspended firms ↔ council | `Authority` (`2025.GG26.3`) ↔ TMMIS `reference` / bgrd committee+item | exact for TMMIS `reference` | **MEDIUM** (not executed end-to-end) | 3 rows; TMMIS needs CSRF |
| Supplier identity (all sources) | `Successful Supplier` / `Supplier Name` / `CONSULTANT'S NAME` | lowercase + strip punctuation + drop `(Submitted by:…)` | **LOW / fuzzy** | No canonical ID anywhere; 471 fuzzy overlaps found |

Note: the `rfxID` in the Ariba **search** feed is the Ariba internal posting id, **not** the
doc number — you must call the **detail** endpoint to get `externalRfxId` before joining.

### 3.3 The key rule — `normalize_document_number(raw) -> str | None`

The `Document Number` column is dirty and will silently break naive joins. The rule:

1. Strip all non-digit characters (`re.sub(r'\D', '', raw)`).
2. Require **exactly 10 digits**; otherwise reject.
3. Reject a **placeholder denylist**.

Dirty cases observed (must be handled/tested):
- BOM/mojibake bytes: `﻿3674586673`, trailing junk `4147794028﻿`, `2821040966 )`.
- Excel scientific-notation corruption: `3.77E+1100`, `3710106+0111` → **unrecoverable, drop**.
- Placeholders/junk: `xxxxxxxx`, `390513test`, `Notice913418`, `Summary67141`, `No. 22436`,
  OData test row `1111111111`.
- Hyphenated forms in free text (`3303-12-3110`) and title-embedded forms
  (`Doc5581608073 - Request for Quotations...`) — strip-non-digits handles both.

~21 corrupt values in solicitations, ~50 in awarded. `_id` (CKAN) and `id` GUID (OData) are
the only guaranteed row-unique keys but they do **not** join across sources — only
`document_number` does.

## 4. Architecture

### 4.1 Stack

- **Python 3.12+**, managed with **uv** (`uv add`, `uv run`); no conda / `environment.yml`.
- **SQLite** (stdlib `sqlite3`) for the store; a `files/` dir (gitignored) for the DB and
  downloaded documents.
- **httpx** (HTTP/2, timeouts, retries) for HTTP; **lxml** for HTML; stdlib `json` for JSON;
  **openpyxl** for XLSX; **pypdf** / system `pdftotext` for PDFs.
- **Playwright** — used only for (a) the authenticated Ariba attachment path and (b) an
  Akamai fallback for `secure.toronto.ca`. Optional; absence degrades gracefully.

### 4.2 Package layout

The new package replaces the flat scripts in `scrapers/` (`rfp_scraper.py`, `open_data.py`,
`ariba_driver.py`, `filemanage.py`, `transmit_json.py`, `azurefileshare.py`,
`secret_manager.py`, `slack.py`, conda `environment.yml`, `Dockerfile`).

```
scrapers/
  pyproject.toml                 # uv-managed project
  toronto_bids/
    __init__.py
    cli.py                       # `tb sync`, `tb export`, `--only <sources>`, `--no-browser`
    config.py                    # source registry; runtime CKAN resource-ID resolution; settings
    http.py                      # shared client: Chrome UA, retry/backoff, rate-limit, Akamai fallback
    store/
      db.py                      # connection, migrations, upsert helpers
      schema.sql                 # relational model (§5)
      models.py                  # dataclasses for canonical entities
    sources/
      base.py                    # Source protocol: name, tier, fetch() -> raw, normalize() -> rows
      odata.py                   # feis_solicitation_published + feis_non_competitive (SPINE)  [T1]
      ckan.py                    # generic CKAN client + per-dataset configs                    [T1]
      ariba_discovery.py         # doIndexedSearch + /rfx/{id} detail                            [T1]
      suspended_firms.py         # HTML table diff                                              [T2]
      ariba_attachments.py       # authenticated Playwright attachment fetch (optional)         [T2]
      tmmis.py                   # council agenda-items (CSRF handshake)                        [T3]
      background_pdfs.py         # award staff-report PDF fetch + text extraction               [T3]
    linking/
      document_number.py         # normalize_document_number (§3.3)
      supplier.py                # fuzzy supplier entity resolution
      link.py                    # build bridges (ariba_posting, council, supplier)
    pipeline.py                  # orchestrate: fetch → normalize → upsert → link
    export/
      base.py                    # Exporter interface (the publish seam)
      json_export.py             # export to JSON (first implementation)
  tests/
    test_document_number.py      # exhaustive, using the real dirty values above
    test_supplier.py
    fixtures/                    # saved OData/CKAN/Ariba/HTML/PDF responses
    test_sources_*.py            # fixture-based per-adapter normalize tests
    test_pipeline_integration.py # end-to-end over fixtures
    test_smoke_live.py           # opt-in, network — asserts endpoint shape (drift canary)
```

### 4.3 Source adapter interface

Every source implements a uniform interface so the pipeline treats them identically and they
are independently testable:

```python
class Source(Protocol):
    name: str
    tier: int                       # 1, 2, 3
    requires_auth: bool
    requires_browser: bool
    def fetch(self, ctx) -> Iterable[Raw]: ...      # network → raw records
    def normalize(self, raw: Raw) -> Iterable[Row]: ...  # raw → canonical rows for the store
```

`fetch` and `normalize` are separate so `normalize` is pure and fixture-testable with no
network.

## 5. Data model (SQLite)

Keyed on the normalized `document_number` except where noted. Rows are **never deleted**;
`first_seen` / `last_seen` provide archive semantics.

- **`solicitation`** (spine) — `document_number` PK; `status` (open/awarded/cancelled);
  `rfx_type`; `noip_type`; `title`; `description`; `issue_date`; `submission_deadline`;
  `category`; `division`; `buyer_name`/`buyer_email`/`buyer_phone`; `wards`;
  `ariba_posting_link`; `odata_id`; `first_seen`; `last_seen`; `source`.
- **`award`** — child of `solicitation`, **one row per successful supplier**:
  `id` PK; `document_number` FK; `supplier_name_raw`; `supplier_id` (FK to `supplier`, fuzzy);
  `award_amount`; `award_date`.
- **`noncompetitive`** (separate island) — `workspace_number` PK; `supplier_name_raw`;
  `supplier_id`; `reason`; `contract_amount`; `contract_date`; `division`;
  `council_authority_link`; `first_seen`; `last_seen`. **No FK to `solicitation` (by design).**
- **`ariba_posting`** (bridge + archive) — `rfx_id` PK; `document_number` FK (via strip);
  `title`; `categories` (UNSPSC); `open_date`; `close_date`; `raw_json` (snapshot);
  `fetched_at`.
- **`attachment`** (Tier 2) — `id` PK; `document_number` FK; `filename`; `local_path`;
  `sha256`; `source`; `fetched_at`.
- **`supplier`** (dim) — `supplier_id` PK; `canonical_name`; `name_variants` (JSON).
  Fuzzy-resolved from all name fields.
- **`suspended_firm`** — `id` PK; `supplier_name_raw`; `supplier_id`; `status`; `start_date`;
  `end_date`; `type`; `council_authority`; `first_seen`; `last_seen`.
- **`council_item`** (Tier 3) — `reference` (`YYYY.CCnn.n`) PK; `title`; `status`;
  `committee`; `term_id`.
- **`background_pdf`** (Tier 3) — `id` PK; `year`; `committee`; `url`; `local_path`;
  `extracted_text`; `linked_reference` FK.
- **`capital_project`** — forward-looking, **explicitly unlinked** (no shared key with the
  spine; a project has no document number until it is solicited). Built 2026-07-16 (#69).
  `pcard` and `consulting` were declined — see §2.1.1.
- **`sync_run`** (provenance/observability) — `id` PK; `source`; `started_at`; `finished_at`;
  `status` (ok/failed); `rows_fetched`; `rows_upserted`; `error`.

## 6. Data flow & idempotency

`tb sync` is **re-runnable without duplicating data**:

1. For each enabled source: `fetch` raw → `normalize` to canonical rows → **upsert on natural
   key** into the store. Record a `sync_run` row.
2. **Linking pass**: normalize document numbers, build/refresh the `supplier` dim (fuzzy),
   populate bridges (`ariba_posting`, council, supplier FKs on `award`/`noncompetitive`).
3. Never delete; maintain `first_seen`/`last_seen`.
4. `tb export --format json` runs the publish seam.

**Archival guarantee.** Open-posting Ariba detail and attachments **vanish when a solicitation
closes** (closed → 401). The pipeline snapshots `ariba_posting.raw_json` and downloads
attachments **at capture time**; this at-scrape-time archival is the core mission.

## 7. Error handling & resilience

- **Per-source isolation** — each adapter runs inside its own try-boundary. A failure is
  written to `sync_run` (status=failed, error) and the remaining sources still complete. One
  Akamai 403 or a wave of Ariba 500s never aborts the run.
- **Retry with backoff** for known-flaky endpoints. Ariba detail returns HTTP 500
  (errorCode 1044) ~48% of the time → retry N times, then skip and log.
- **Akamai fallback** — try plain httpx + Chrome User-Agent first (worked in recon on
  `secure.toronto.ca`); on 403, fall back to Playwright (browser TLS) for OData/TMMIS.
  Behaviour is IP-dependent, so this fallback is defensive.
- **Graceful degradation** — no Ariba credentials → skip the attachment fetcher, everything
  else runs. No Playwright installed → skip browser-dependent fallbacks with a clear warning.
- **Runtime resource-ID resolution** — CKAN resource UUIDs rotate on refresh; resolve via
  `package_show?id=<slug>` each run. Watch a CloudFront `Vary: Cookie` quirk that can return
  the wrong package on a bare slug — cache-bust or pin the resolved UUID for the run.
- **Robust key normalization** (§3.3) prevents silent join breakage.

## 8. Testing

- **TDD** for the pure core: `normalize_document_number` and supplier fuzzy-matching get
  exhaustive unit tests using the real dirty values in §3.3.
- **Fixture-based adapter tests**: saved JSON/HTML/CSV/PDF responses → `normalize` → assert
  canonical rows. No network.
- **Live smoke suite** (opt-in, `-m live`): hits each endpoint and asserts response shape —
  the early-warning canary for upstream drift, the failure mode that silently killed the old
  scraper.
- **End-to-end integration test**: run the pipeline over fixtures and assert a known doc
  number links across `solicitation` + `award` + `ariba_posting`.

## 9. Credentials & configuration

- No Azure Key Vault. Secrets (Ariba supplier login for the optional attachment fetcher) come
  from **environment variables** / a local `.env` (gitignored), read by `config.py`.
- No `secrets.pickle`, no `DefaultAzureCredential`.
- All source URLs / resource slugs live in `config.py`; resource UUIDs are resolved at runtime.

## 10. Phasing

Each phase ships something that works standalone and gets its own implementation plan.

- **P0 — scaffold**: uv project, package layout, SQLite schema + `db.py`, `http.py`, CLI
  skeleton, `normalize_document_number` + full tests.
- **P1 — robust core (Tier 1)**: OData spine adapter (`feis_solicitation_published` +
  `feis_non_competitive`) + CKAN adapters (awarded / open / non-competitive) + the linking
  pass on `document_number` → SQLite. **Reproduces and exceeds the old scraper's mission with
  zero browser.**
- **P1.5 — adjacent datasets (optional)**: `capital-project-pipeline` as a clearly-unlinked
  table. Done 2026-07-16 (#69); `pcard` and `consulting` declined, see §2.1.1.
- **P2 — Ariba Discovery JSON**: `doIndexedSearch` + `/rfx/{id}` detail adapters, the
  rfxId↔document_number bridge, open-posting archival. Still no auth/browser.
- **P3 — publish seam**: `Exporter` interface + `json_export.py`; `tb export`.
- **P4 — Tier 2**: suspended-firms diff (cheap, low-brittleness). **DONE (P4a).** The
  authenticated Ariba attachment fetcher (P4b) was investigated and **deferred as infeasible
  for a public archive** — a supplier login does not grant access to event documents (only
  event *participants* can view them); see §2.5 gap #1. No `attachment` table / Playwright /
  credential plumbing was built.
- **P5 — Tier 3 enrichment**: TMMIS council agenda-items + background-file PDFs; the supplier
  fuzzy `dim` + council bridge.

## 11. Immediate cleanup

- Delete the dead flat scripts once superseded (esp. `scrapers/open_data.py`, which fetches a
  retired dataset and would throw).
- Remove Azure/Slack/conda/Docker machinery from the scraper path (retain in git history).
