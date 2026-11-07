# City bulk discovery and backfill — 2026-11-07

The first all-term workload captured **601 agendas and 1,279 PDFs** and recovered
**36 additional awarded City solicitations with named bids** in a private replay.
Missing coverage fell from **3,723 to 3,687**. The workload is resumable, without
per-year approvals or handwritten per-report admission patches.

This is a measured first pass. It does not resolve the remaining historical gap
or establish that every scheduled meeting/report is available.

## Source census

The official meeting-schedule CKAN package resolves five CSV resources. Filtering
dated core meetings from 2010 through 2026 yields 622 seeds: four complete terms'
previously measured 592 plus 30 dated 2010 meetings from the preceding term.
Multi-day Council rows are grouped by term/body/meeting number.

| Stage | Result |
| --- | ---: |
| Scheduled core meeting seeds attempted | 622 |
| Validated agendas held | 601 |
| Meeting URLs failing validation, responses retained | 21 |
| Parsed item records | 31,037 |
| Procurement candidate item records | 990 |
| Distinct candidate attachment URLs | 1,323 |
| PDFs held | 1,279 |
| Unique held PDF hashes | 1,268 |
| PDF URLs returning HTTP 404 | 44 |
| Held-source SHA-256 failures | 0 |
| Typed item headings omitted by the parser | 0 |

The bodies are Council, Executive, Government Management/General Government
(including Licensing), and Public Works/Infrastructure and Environment. This is
not an inventory of all City boards. Committee/Council repeated consideration
remains provenance; URLs and bytes are deduplicated for processing.

The 21 meeting failures and 44 PDF URLs remain explicit gaps. They do not prove
permanent inaccessibility, cancellation or absence of alternate publications.
An initial genuine TMMIS maintenance page deferred collection. A later false
maintenance classification was traced to agenda prose about traffic signal
system maintenance. The classifier now requires the platform's dedicated
breadcrumb and notice; its regression test preserves that distinction.

## Extraction and admission

Six Sonnet/high batch submissions completed **149 requests**, all collected
successfully. The shared ledger settled **USD 1.858788**. The existing USD 20
monthly guard was retained, with no automatic paid retries, model fallbacks or
shared-prompt/version changes. Raw results were collected operationally under the
pipeline lock, then immutable cache entries were copied into validation. The
shared queue was not collected into a private archive.

Initial bounded research cohorts included unlocated reports. The final default
queue requires a discovery target in the awarded-City missing population and
skips events outside that population. Native-call-only reports remain archived
without speculative further spending; bounded `--include-unlocated` research is
available. A final default queue pass submitted zero additional requests.

The [runbook](../city-bulk-backfill.md) describes the shared admission rules:
primary printed Doc roles; literal received-firm lists or received-bid tables;
participation stage and declared-count checks; source/cache integrity; and one
coherent list per event rather than invented supplier aliases across supplements.
Prices, tax bases and compliance statuses remain null. Source quotations and
refusals are retained. Conflicting current headers/footers remain exceptions,
including the copied Paramedic report ID.

The report replay admitted **112 participation rows across 28 events**. Of those,
**25 newly cover awarded City solicitations**. Replay preserves IDs and first-seen
values. Evidence exports separately as `city_bulk_bid_evidence`.

## Existing-policy linkage batch

Twelve earlier identity candidates were checked against the complete archive.
1192015000 / 2015.BD34.2 was refused because the call is also held under
2015.BD37.3. The other **eleven** pass unchanged source, publisher snapshot,
global uniqueness and original-agenda gates. Nine retain explicit method
discrepancies under the existing review class. No generic matcher or amount
tolerance was relaxed. Complete original publisher records accompany the proofs.

## Coverage and integrity

The frozen private baseline starts after the previously merged OCR recovery:
6,451 awarded City solicitations, with 3,723 missing bids. The eleven linkages
reduce that to 3,712; report backfill reduces it to **3,687**. The new workload
therefore recovers **36 solicitations**, rather than counting files or names as
coverage gains.

All **21,595 existing bid rows** retain their payloads, IDs and first-seen values.
Canonical attribution mismatches and foreign-key violations are empty. The full
private JSON/schema export succeeded; JSON size is 68,414,143 bytes. The offline
suite passed **1,684 tests**, with one existing skip.

These are private validation results. New facts and crosswalks have not been
deployed by this run. The earlier OCR production recovery/publication remains a
separate operational action.

## Remaining systematic frontier

Historical native Call Number identity is now a larger obstacle than locating
PDFs alone. Sources disclose named bidders, but publisher dates, methods or award
values can disagree with source evidence. RFQ6032-14-0189 has different
source/publisher dates; RFP9117-18-7185 agrees on dates and winner but carries a
different award value. This does not establish which source field is defective.

The raw sources and model results remain available for cohort reconciliation.
Numeric encodings remain discovery hypotheses. Complex participation layouts
also remain exceptions. Neither frontier should become per-record approval/PR
cycles or unsupported joins to manufacture a larger recovery count.

## Reproduction

See the runbook. Local artifacts under
`.validation-city-bulk-discovery-2026-11-06/` include pinned schedules, source
HTML/PDFs, extraction receipts, explicit gaps, linkage refusal, private replays
and exports. `rehearsal/source-census.json`, `crosswalk-replay.json`,
`report-backfill.json` and `integrity.json` record the measured transitions.
