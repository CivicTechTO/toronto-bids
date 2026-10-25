# MERX capture and TTC supplementary reports

`capture-merx` archives buyer-scoped public listings and optional public bid/award
tabs for TTC, Police, Parking Authority, CreateTO (Build Toronto/TPLC), and Pan Am.
Reproduction authorization is recorded in [the permission record](permissions/2026-10-24-merx.md).
`nightly` runs incremental MERX capture for all enabled buyers after Bonfire,
including advertised public bid/award detail tabs (`details=True`). The isolated
`merx` step records status in `sync_run`, reports real bid/award row-count growth,
and forwards per-buyer failures and legacy detail gaps to the nightly summary.
Other buyers and later pipeline steps continue after a failure. The core `sync`
command does not invoke MERX. Historical backfills and the reviewed supplementary
TTC report archive remain separately invoked commands.

## Running it

From `scrapers/`, with `TB_DATA_DIR` pointing at the intended data directory:

```sh
# Recent closed/results/awarded windows, plus all accessible open pages.
uv run tb capture-merx
# Capture the public bid/award tabs advertised by those detail pages as well.
uv run tb capture-merx --only toronto-transit-commission --details
# Historical listing enumeration, with a hard request budget and resumable pages.
uv run tb capture-merx --only toronto-transit-commission --section closed --backfill --max-requests 300
uv run tb capture-merx --only toronto-transit-commission --section closed --backfill --resume --max-requests 300
# A bounded query: completeness applies to T11 only, not the whole buyer.
uv run tb capture-merx --only toronto-transit-commission --section closed --keywords T11 --backfill
```

`--section` is repeatable (`open`, `closed`, `results`, `awarded`). The default
requests every section. Incremental historical sections capture the latest 25
listings; this is explicitly a recent window, not complete history. Absence from a
window never deletes or closes a held listing. The request budget applies per
buyer and includes retries and detail/fragment requests.

Historical enumeration collects the latest and oldest public windows (at most 40
pages each), then searches observed TTC code prefixes using ordinary positive
keywords **without a wildcard**. `--query` adds positive search terms within the
same buyer profile. Searches can overlap: counts are never added to assert coverage.
A bounded `--keywords` cohort stays scoped to that query and does not automatically
expand into unrelated prefix queries. Whole-population coverage requires this
attempt's distinct ID union to equal the initial advertised count, with an
unchanged live count and latest first-page ID sequence at the end. This guards
observed drift; it cannot guarantee a transactionally consistent publisher snapshot.
A smaller query that still exceeds the public window is reported as incomplete.

### Retention and resumption

`<DATA_DIR>/agencies/merx/<buyer>/` retains immutable response bytes by SHA-256,
request manifests, per-attempt coverage reports, `capture.json` and backfill-page
pointers. `merx_capture` retains coverage history in SQLite and the buyer export's
`merx_captures` array. `complete` applies to the report's sections, keyword and
`details_requested` scope. It does not assert a complete procurement history.

`--resume` reuses hash-verified historical listing/detail/fragment response bytes and always rechecks the
live population. A fresh backfill replaces only resume pointers; original response
snapshots remain. Changed populations may require a fresh attempt. Malformed,
changing, duplicate or clamped pages fail validation. Already validated query
batches remain held; an invalid batch writes no partial listings. Coverage is
computed from this attempt, never from accumulated database row counts.

A failed capture, incomplete backfill, exhausted request budget or requested gated
detail produces exit code 1. A normal incremental window is not an error simply
because historical coverage is partial. Reports distinguish that scope explicitly.
This code does not retry unknown records through alternate gated detail routes.

All request attempts wait at least 5.2 seconds. A process lock serializes MERX
requests for buyers sharing a data directory; retries use bounded exponential
backoff and honour Retry-After. Long deadlines persist across buyers/runs and defer
capture. A redirect or access denial stops that buyer's network sequence. Requests
identify the Toronto Bids archive. Ordinary capture uses HTTP. An incomplete HTTP 202/empty reply triggers one
bounded anonymous Chromium fallback after at least 60 seconds (and any longer
Retry-After deadline defers the run). The site's normal JavaScript may render the
public page; navigation is bounded and shares the request budget and pacing. The
reader retains source bytes, reuses anonymous browser cookies privately, and never
logs cookie values or interacts with account/CAPTCHA controls. Diagnostic response
headers are stored under `diagnostics/` using an allowlist. No login, registration
or submissions are performed. Separate data directories do not share the process lock; run only one
live capture environment against MERX at a time.

## Identity, bids and awards

`merx_notice` maps buyer + publisher notice ID to the first observed public page.
Nine-digit legacy IDs and modern zero-prefixed ten-digit IDs remain strings. Tab,
slug or tracking-parameter changes preserve the archive page and procurement IDs.
Printed references are nullable, nonunique labels; the source is not joined to the
City document-number spine.

`portal_listing` retains titles, date-only publication/closing dates and explicitly
labelled printed references. A listing on a results/awarded tab does not create an
award. Those tabs' result-publication/award-publication dates are not closing dates;
unknown closing dates remain null. Open/Closed status is assigned only from those
listing sections. Full original responses retain the other displayed dates.

Modern detail pages must match the listing's Reference Number. Only advertised
public `data-ajax-url` tabs are fetched. JavaScript envelopes are decoded as a
literal string, never executed. Bidder amounts bind inside each bidder container;
`No Bid` has a null price and explicit status. `agency_bid.result_stage` preserves
`Unofficial Results`, `Official Results`, or the generic `Bid Results` label
independently of compliance status. `N/A` and omitted prices retain named bidders
with null prices; they do not mean No Bid. Only required metadata fields must be
unique: repeated ancillary locations, events or pricing sections are valid.
No lowest-price award or unknown tax basis is inferred.

Awards require the separate explicit awardee collection and matching notice
reference. An explicit `No Award` tab creates no award facts and is a valid
non-award outcome, including cancellations. `agency_award.award_date` and `publication_date` retain distinct source
days; tax/value semantics remain those of the source. MERX bid/award facts use the
held notice page as their internal `native_ref`, keeping them on the listing's
procurement ID even when printed references are missing or reused. These facts
retain the actual fragment URL as `report_url`. Missing fields and source absence
preserve held facts, following archive upsert semantics.

Legacy listing metadata is public, but sampled legacy details require account
entitlement. They remain visible `detail_gaps`, independently of listing coverage.
Whole-history reconciliation and exhaustive legacy detail recovery remain unproven.

## Independent TTC reports

```sh
uv run tb capture-ttc-reports
# Queue shared Sonnet batch extraction; collection and extraction are separate.
uv run tb extract --corpus ttc --batch --backfill
uv run tb agency-coverage --only ttc
```

The reviewed seed inventory in `toronto_bids/data/ttc_reports.json` contains three
independently published TTC procurement reports. This is supplementary coverage,
not an exhaustive meeting crawler. The source investigation is recorded in
[the TTC note](ttc-independent-sources-2026-10-24.md); systematic meeting enumeration
remains future work. Add reports only from verified published TTC links, with a
review basis. Downloading invokes no model, publication or supplier-account action.

Reports are queued as `background_pdf.kind='ttc_board'`. Downloads validate PDF
structure, save by content hash, and require successful `pdftotext`; HTML errors
remain queued with visible failures. Already held PDFs are hash-verified before
skipping. Unattempted URLs remain in the inventory if a denial stops a run.

The `ttc` corpus reuses the existing shared prompt/cache, Claude batch queue and
monthly budget. The prompt is unchanged. Bid facts retain `result_stage='Board
report'`. Staff-report award recommendations are withheld and reported through the
agency coverage ledger until independent decision verification is implemented.
They do not become approved awards or an Awarded solicitation status. No automatic
MERX-to-report bridge is asserted from a shared title or a contract-number guess.
Original extraction results remain available for a later reviewed decision.

## Validation

The fixture suite covers actual public listings for all five profiles, TTC's nine
`T11` pages (210 advertised at that earlier capture), bid and award fragments, hidden
empty templates, missing dates, singular/zero counts, reused routes, stage/date
separation, access failures, cooldowns, request budgets, resume and changing totals.
A simulated 2,050-notice population verifies the complete cap workaround: two
1,000-row windows plus an overlapping smaller query reconcile to 2,050 unique IDs.

The new code's isolated live validation on 2026-10-24 captured seven open TTC pages,
eight bidder entries from a public notice and all three supplementary TTC PDFs.
A subsequent live open capture also validated Police (4 listings), Parking Authority
(2), CreateTO (1) and Pan Am (0). Its live `T11` backfill captured all
**201 of 201 then-advertised** records across
nine pages plus a fresh first-page population check. This differs from the earlier
210-record snapshot: preserve counts as dated observations. No full buyer-wide
history, paid extraction, production deployment or publication was performed.

The first production run's refusals and saved evidence are recorded in
[the validation report](merx-production-validation-2026-10-25.md). A subsequent
ordinary Chromium probe observed `x-amzn-waf-action: challenge` on the exact
Parking Authority URL. AWS documents this [HTTP 202 browser-check behavior](https://docs.aws.amazon.com/waf/latest/developerguide/waf-captcha-and-challenge-actions.html).
The reader loads the site's normal rendering assets, bounds automatic navigation,
and refuses an incomplete page if normal rendering does not finish. An HTTP 202
alone never establishes a successful capture or permanent blocking.
