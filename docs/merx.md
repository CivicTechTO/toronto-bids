# MERX capture and supplementary TTC reports

`tb capture-merx` archives public listings and advertised public bid/award tabs for five buyers: TTC, Toronto Police Service, Toronto Parking Authority, CreateTO (Build Toronto/TPLC), and Pan Am. Written permission is in [permissions/2026-10-24-merx.md](permissions/2026-10-24-merx.md).

`tb nightly` runs incremental MERX capture (with `details=True`) after Bonfire as an isolated step, recording status in `sync_run`, reporting bid/award row growth, and forwarding buyer errors and legacy detail gaps to the nightly summary. `tb sync` does not run MERX. Historical backfills and supplementary TTC reports are run manually.

## Commands

Run from `scrapers/` with `TB_DATA_DIR` set:

```sh
# Incremental capture: all open pages + latest 25 per closed/results/awarded section
uv run tb capture-merx

# Include advertised public bid/award detail tabs for one buyer
uv run tb capture-merx --only toronto-transit-commission --details

# Historical backfill with request budget and resume
uv run tb capture-merx --only toronto-transit-commission --section closed --backfill --max-requests 300
uv run tb capture-merx --only toronto-transit-commission --section closed --backfill --resume --max-requests 300

# Bounded keyword backfill (coverage applies to T11 only)
uv run tb capture-merx --only toronto-transit-commission --section closed --keywords T11 --backfill
```

- `--section` is repeatable (`open`, `closed`, `results`, `awarded`); defaults to all four.
- Incremental historical sections fetch only the latest 25 listings; absence from that window never deletes or closes a stored listing.
- `--max-requests` applies per buyer and includes retries and detail/fragment requests.

### Historical pagination and coverage

MERX caps standard pagination at 40 pages (1,000 rows). `--backfill` fetches both the newest and oldest 40-page windows, then runs non-wildcard prefix queries (`--query` adds extra positive terms). `--keywords` stays strictly scoped to that query.

Whole-population completeness requires the distinct ID union from this run to equal the initial advertised count, with an unchanged live count and first-page ID sequence at the end. Any smaller query that still hits the 40-page cap is reported as incomplete.

### Storage, resume, and pacing

- Files live under `<DATA_DIR>/agencies/merx/<buyer>/`: SHA-256 response snapshots, request manifests, per-attempt coverage reports, `capture.json`, and backfill-page pointers. Coverage history is also stored in SQLite (`merx_capture`) and exported in `merx_captures`.
- `--resume` reuses hash-verified historical listing/detail/fragment responses while rechecking the live population. Malformed, duplicate, clamped, or shifting pages fail validation without writing partial batches.
- **Identity resolution (#305)**: Each validated listing query and notice detail commits as it completes; procurement identity resolves once per buyer capture in `finally` before writing the coverage report. A failed pass is recorded as a coverage failure, while committed rows stay in place to be resolved on the next pass or before export.
- Exit code 1 indicates a capture error, incomplete backfill, exhausted request budget, or gated detail failure. Normal incremental runs exit 0.
- **Pacing and browser fallback**: Requests wait at least 5.2s, serialized per `TB_DATA_DIR` via a process lock, with exponential backoff and persisted `Retry-After` deadlines. Run only one live MERX capture environment at a time. Redirects or access denials stop that buyer immediately. If plain HTTP gets an incomplete HTTP 202 / empty response (AWS WAF browser check), the reader waits at least 60s (or longer per `Retry-After`) and makes one bounded anonymous Chromium fallback sharing the same budget and pacing. Anonymous cookies stay in memory (never logged); allowlisted diagnostic headers are saved under `diagnostics/`. No logins, registrations, or CAPTCHA interactions are performed.

## Identity, bids, and awards

- `merx_notice` maps `(buyer, notice_id)` to the first observed public URL. Notice IDs (9-digit legacy or zero-padded 10-digit modern) stay strings. URL slug, tab, or query changes preserve the listing and canonical procurement ID. Printed references are optional, non-unique labels and are not joined to the City spine.
- `portal_listing` stores titles, date-only publication/closing dates, and printed references. Listings on results/awarded tabs do not create award rows, and result/award publication dates are not written to `closing_date`. `Open`/`Closed` status comes only from those listing sections.
- **Detail tabs**: Modern detail pages must match the listing's Reference Number. Only advertised public `data-ajax-url` tabs are fetched, and JS envelopes are decoded as literal strings rather than executed.
- **Bids**: `agency_bid.result_stage` preserves `Unofficial Results`, `Official Results`, or `Bid Results` independently of compliance status. `No Bid` records a `NULL` price and explicit status; `N/A` or omitted prices keep the named bidder with `NULL` price. Advertised Bid Results tabs showing an explicit `No Files` row record `no_bidder_disclosure` in `detail_notes` (creating no bids and setting `bidder_disclosure_complete = false`), whereas unmarked empty or malformed fragments fail parsing.
- **Awards**: Require an explicit awardee list and matching notice reference. Explicit `No Award` tabs are valid non-award outcomes and create no award rows. `award_date` and `publication_date` stay separate. MERX bids and awards use the held notice page as their internal `native_ref` (keeping them attached to the listing's procurement ID) and store the fragment URL in `report_url`.
- **Legacy details**: Sampled legacy notices require account entitlement and are recorded under `detail_gaps` without retrying alternate routes.

## Supplementary TTC reports

For year-by-year TTC Board archive capture and reviewed admission, see [ttc-history.md](ttc-history.md).

```sh
uv run tb capture-ttc-reports
uv run tb extract --corpus ttc --batch --backfill
uv run tb agency-coverage --only ttc
```

- `toronto_bids/data/ttc_reports.json` holds a reviewed seed list of three public TTC procurement reports.
- Reports queue as `background_pdf.kind = 'ttc_board'`, verify PDF structure and `pdftotext`, save by SHA-256, and hash-verify already-held files before skipping.
- Extraction uses the shared Claude prompt, batch queue, and monthly budget. Extracted bids get `result_stage = 'Board report'`. Staff award recommendations stay withheld in `agency_coverage` until backed by independent Board decision verification. No automatic MERX-to-report links are inferred.

## Validation notes

Offline tests cover all five buyer profiles, TTC `T11` pages, bid/award fragments, empty templates, missing dates, access errors, cooldowns, request budgets, resume, and a simulated 2,050-notice backfill reconciling two 1,000-row windows plus an overlapping query.
