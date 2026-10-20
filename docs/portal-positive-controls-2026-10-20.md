# Portal false empties (#226): TRCA verified, Zoo live check pending

Investigation date: 2026-10-20. The TRCA portal was not empty. The configured numeric
status filters silently returned successful empty responses. The public UI sends names
such as `Open` and `Awarded`; its All option sends an empty status string.

## Controlled live evidence

The original `fetch_listings` returned zero records for TRCA across status values 0–5.
Holding the session, token, date range and other parameters constant, changing the filter
to `Awarded` returned total=187. Both landing-page antiforgery token scopes worked in the
same ordinary HTTP session. Adding the UI's exact `sort=DateClosing DESC,Id` succeeded and
provided deterministic ordering. The previous blanket claim that `sort` must be omitted is
therefore not supported by the current site.

Primary implementation evidence:
[TRCA landing page](https://trca.bidsandtenders.ca/Module/Tenders/en) and
[its own search JavaScript](https://trca.bidsandtenders.ca/Module/Tenders/Resources/scriptsV2/home/index.js).
The UI leaves date bounds blank by default; those blank bounds do not explain the false empties.

A rate-limited All sweep fetched **249 distinct listing IDs against endpoint total=249**:

| Status | Listings |
| --- | ---: |
| Open | 4 |
| Closed | 29 |
| Awarded | 187 |
| Terminated | 24 |
| Archived | 5 |

The three known TRCA controls were all present:

| Call | Status | Closing instant (UTC) | Public detail |
| --- | --- | --- | --- |
| 10042501 | Awarded | 2025-03-07 21:00 | [Uniformed security](https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/d58d0f1a-696d-4ee2-8f01-ac13ba93b07b) |
| 10044383 | Closed | 2026-05-19 20:00 | [Toronto Island design](https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/5082161c-f80a-4cd8-aaac-8388a237e99e) |
| 10042322 | Awarded | 2025-01-24 21:00 | [Customer management system](https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/6e5b34fd-2e44-48ff-9114-177964816206) |

No login, bid-document downloads, account actions or paid model calls were used. Requests
retained the granted 1.5-second pacing. The Zoo grant requires off-peak crawling, so its
live sweep was not run during the afternoon investigation.

## Implementation

- Use the UI's All filter and stable closing-date/Id sort. Stop assuming integer statuses.
- Require a nonnegative integer total and a list of object records. Reject premature empty
  pages, changing totals, inconsistent counts and duplicate IDs. Explicit empty replies
  remain valid; malformed replies cannot become empty captures.
- Record nonempty replies automatically before parsing, including partial fetches when a
  later page fails. Content-addressed snapshots retain changed versions without rewriting
  earlier observations. Explicit `--record` remains available.
- Map the real `DateAvailable` and `DateClosing` JSON.NET millisecond timestamps to UTC ISO
  instants. The earlier assumed date fields remain compatible inputs.
- Use explicit reference fields when supplied, or recognized bid-number prefixes in grid
  titles. Public detail metadata independently confirmed ordinary and suffixed TRCA keys.
  Preserve `_33` and `-1` suffixes: they identify distinct published calls. Unknown layouts
  fail before writing the batch instead of permanently using the portal page's GUID.
- Keep the existing backfill-only store contract. Status refresh and the remaining #140
  questions are not settled by this fix. The empty nightly summary now says “no listings”
  because the capture spans all statuses.

Four real TRCA records are fixtures; their independent detail URLs and capture provenance
are in `scrapers/tests/fixtures/agencies/SOURCES.md`. The synthetic sample remains a
compatibility input and does not establish live Zoo validation.

## End-to-end verification

The corrected command ran successfully against a separate data directory:

```sh
TB_DATA_DIR=/tmp/tb-226/trca-validation scrapers/.venv/bin/tb \
  enrich-agencies --only trca --portal --record
```

It recorded 249 raw listing snapshots and populated 242 distinct solicitation keys. The
counts differ because multiple published listings can share the same canonical call key;
all 249 distinct portal IDs remain in the raw snapshots. This is the existing per-call
agency identity contract, not a claim that every posting has its own solicitation row
(the broader identity work is #124). Control rows and both suffix styles were checked in
SQLite. The live run also caught four initially unrecognized hyphenated keys; the batch
was refused and all raw records were retained until their explicit public bid numbers were
verified and the correction passed.

**950 offline tests passed.** New coverage includes the real-record mapping, All filter,
stable sort, suffix preservation, raw recording on parser/page failures, immutable snapshots,
batch validation and incomplete/repeated pages. An obsolete “never include sort” assertion
was replaced with the current UI request contract after the live comparison.

The production database had no `source='bids_tenders'` solicitation rows before this work.
No production data, export or published artifact was changed by this investigation.

## Scheduled Zoo check and completion boundary

One off-peak job is scheduled for **2026-10-21 05:10 America/Toronto (09:10 UTC)**:

- Timer/service: `tb-226-zoo-verify.timer` / `tb-226-zoo-verify.service` (user units).
- Frozen package and runner: `/tmp/tb-226/zoo-code/`, independent of subsequent checkouts.
- Isolated data: `/tmp/tb-226/zoo-validation/`.
- Log: `/tmp/tb-226/zoo-verification.log`.
- Manifest: `/tmp/tb-226/zoo-validation/verification.json`.
- Reproducible runner: [verify_zoo.py](portal-positive-controls-2026-10-20/verify_zoo.py).

The runner uses only the permitted public listing endpoint, automatically records nonempty
replies and reports candidate RFQ-50/RFP-03 controls. It marks the job failed if capture or
parsing fails or either known control has no candidate. Inspect candidates' dates and detail
URLs before declaring those specific controls verified. An offline smoke check proved the
scheduled environment loads the frozen package and can initialize and write its isolated
manifest; it is not evidence of a live Zoo pass.

**#226 remains incomplete until the Zoo live result is inspected.** Keep the PR in draft.
#140 also remains open: Zoo real fixtures, awarded supplier/value availability and nightly
status refresh still need their own acceptance checks. To inspect the scheduled run:

```sh
systemctl --user status tb-226-zoo-verify.service --no-pager
cat /tmp/tb-226/zoo-validation/verification.json
```
