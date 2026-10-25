# MERX production capture — 2026-10-25

The deployed nightly MERX runner completed in **23m38s** with partial capture and a
correctly recorded failed `sync_run` status. It used production data at
`/home/alex/tb-data`, held the shared pipeline lock, and backed up SQLite before
capture. No code was changed during the run.

## Retained records

Production MERX counts grew from zero to **141 notice pages, 123 bidder entries
and 59 explicit award lines**. All retained bidder entries preserve the source
`Unofficial Results` stage. These are observed entries, including any explicit
No Bid statuses; the count is not a claim about evaluated submissions.

| Buyer | Notice pages | Bid/award facts retained | Reported failures | Requests |
| --- | ---: | ---: | ---: | ---: |
| toronto-transit-commission | 52 | 147 | 18 | 118 |
| toronto-police-service | 58 | 35 | 18 | 90 |
| toronto-parking-authority | 31 | 0 | 19 | 36 |
| createto | 0 | 0 | 1 | 1 |
| toronto-pan-am-sports-centre | 0 | 0 | 1 | 1 |

These are routine recent windows, not a full historical backfill. TTC retained
123 bidder entries and 24 awards; Police retained 35 awards. Parking Authority
retained listing metadata only. CreateTO and Pan Am failed on their first listing
request, so neither buyer was successfully captured in this attempt.

## Failures and cause evidence

The **57 reported failures** group as follows:

- **30 repeated labels**: 23 Pricing, six Location and one Onsite Visit. The
  current detail parser demands uniqueness across all named fields, including
  ancillary sections. This is stricter than the actual public source layout.
- **14 price-layout refusals**: eleven `N/A` bid amounts and three bidder
  containers without a bound amount. Supported names-only bidder evidence must
  remain distinct from `No Bid` and from a zero-dollar bid.
- **Eight publication-type refusals**: Police labels these `Bid Results`, whereas
  the parser currently accepts only Official Results or Unofficial Results.
  Preserve the literal source stage without assuming an evaluation status.
- **Two award-tab validation refusals**: matching-reference validation failed.
  Inspect the retained tab structures before changing identity rules.
- **Three empty HTTP 202 responses**: one Parking Authority detail, then the
  CreateTO and Pan Am initial listing pages. Each had a zero-byte body, HTML
  content type, and no retained Retry-After value. The cause is unverified.
  HTTP 202 must not be treated as a successfully captured HTML document.

The run made **246 requests**: 243 HTTP 200 responses and three HTTP 202
responses. The shortest observed interval between archived response completions
was 5.34 seconds; the deployed requester sleeps at least 5.2 seconds before
each attempt. No paid model requests or supplier-account actions were invoked.

## Integrity and next work

- Zero foreign-key violations.
- Zero MERX bid/award page-identity mismatches.
- Original responses and request manifests were retained before parsing.
- The failed status and complete error list are stored under `sync_run.source=merx`.
- This was the MERX step only: it did not run the complete nightly or publish
  an updated release. New records are held in the production database.

Before the next scheduled run, fix the convergent parser cases from the retained
bytes, add those source layouts to the fixture suite, and replay them offline.
Investigate HTTP 202 separately with bounded, respectful access; do not assume
automatic recovery or infer permanent blocking from these responses alone.
Then rerun production capture and verify CreateTO/Pan Am and the refused detail
records explicitly. Whole-history reconciliation remains separate.

## Local evidence

- `.validation-merx/production-run-2026-10-25/result.json` — complete per-buyer report.
- `.validation-merx/production-run-2026-10-25/verification.json` — integrity and empty-response checks.
- `.validation-merx/production-run-2026-10-25/ttc-refusals.json` and `police-refusals.json` — offline field evidence.
- `.validation-merx/production-run-2026-10-25/run.log` — progress and final report.
- `/home/alex/tb-data/agencies/merx/` — raw responses and manifests.
- `.validation-merx/production-run-2026-10-25/before.sqlite` — private pre-run backup.
