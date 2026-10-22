# bids&tenders parser validation and refresh (#140)

The parser is validated against actual listings: a fresh anonymous TRCA All sweep on
October 22 returned **249/249 distinct pages**, all parsed successfully. The complete saved
Zoo off-peak capture supplies **122/122 pages**. No daytime Zoo crawl was performed.
The [TRCA capture report](portal-refresh-validation-2026-10-22/live-trca.json) records the
endpoint-validated total, pagination completion, captured fields and named statuses.

## Remaining questions from the provisional implementation

**Statuses are published names.** The live UI uses an empty All filter, or named filters;
its JSON `Status` field is text. Numeric search filters previously returned false empties
(#226); they are not a status-enum mapping. Preserve named status values verbatim, including
unknown future names. A legacy `StatusText` label remains supported; an unlabelled numeric
or malformed status fails the batch rather than entering the archive as a supposed state.

| Source | Open | Closed | Awarded | Cancelled | Terminated | Archived | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRCA, fresh 2026-10-22 | 4 | 29 | 187 | 0 | 24 | 5 | 249 |
| Zoo, saved 2026-10-21 off-peak capture | 0 | 29 | 70 | 1 | 10 | 12 | 122 |

Zero is the observation in that sweep, not a claim that a status is unsupported. Cancelled
and Terminated remain different source labels. DateAvailable/DateClosing carry absolute
JSON.NET millisecond timestamps; parsing retains their UTC instants. Real positive controls
independently compare the published date displays and posting reference fields.

**Printed references overlap but do not establish identity.** Of 242 distinct TRCA portal
labels, 70 also appear in its 369 board solicitation labels; the Zoo has 16 overlaps between
114 portal labels and 93 board labels in the production-copy baseline. Seven TRCA and eight
Zoo labels are reused by different portal pages. The #124 contract therefore keeps page
identity independent of labels and board contracts; #140 does not restore reference-only
COALESCE enrichment. Matching spelling is a candidate, not a proven procurement join.

**Awarded JSON does not supply award facts.** All **257 Awarded grid records** were included
in the field audit (187 TRCA, 70 Zoo). Neither grid exposes supplier identity or an award
amount. The VendorHasBidInProgress/VendorIsRegistered-style fields are UI flags, not winning
supplier data. No supplier or amount is inferred from status, descriptions, counts or those
flags, and no portal `agency_award` path is added. Board-derived award facts remain intact.
This finding is bounded to the captured public listing JSON, not every possible portal page.

The primary `bids_tenders_record_sample.json` fixture now contains an unchanged real TRCA
record. The old hand-built sample is explicitly named `bids_tenders_legacy_synthetic.json`
and remains only for legacy field-alias tests. Full real TRCA/Zoo datasets establish recall.

## Refresh semantics

`portal_listing` belongs to its source page. On re-observation, the latest **non-null**
status, posted/closing dates, title and printed reference replace the previous values.
Missing/null fields keep held values; a posting absent from a later response is neither
closed nor deleted. There is no assumed monotonic status order, because sources can correct
or reopen a posting. The stable buyer/page key, row ID, procurement ID and first_seen survive;
last_seen continues to record observation. A corrected reference is a label change, not a
new procurement. Board records and other portal pages with the same label are unaffected.

Content-addressed raw captures retain earlier values. Full-batch parsing still precedes
any update, and incomplete paging/malformed fields remain operational failures. Updating
page metadata never converts an Awarded label into a monetary claim.

## Verification

**1,110 offline tests passed.** Refresh regressions use unchanged captured observations
against clearly controlled stale database values; they do not claim those simulated earlier
states were witnessed on the source. Coverage includes status/date/title/reference updates,
null preservation, missing pages, repeated-reference isolation, raw history, named-status
validation and forward compatibility. The real paged fetch, storage and refresh path also
runs through `tb nightly` with recorded HTTP responses: 371 listings, a successful portal
history entry, retained identities, raw recordings and current exported status.

A [full-data isolated replay](portal-refresh-validation-2026-10-22/replay.json) cloned the
post-#124 production-copy baseline, deliberately seeded stale portal metadata, then restored
all five fields for all 371 pages from the fresh TRCA and saved Zoo records. IDs and first-seen
timestamps survived, including reverse-order replay. Every other table compared identical,
including all 1,011 board awards, identity evidence and monetary values. Integrity and FK
checks passed, 371 raw records were retained, and JSON exported the current metadata.
Production was not migrated or modified; no paid extraction ran.

Reproduce the controlled replay against a post-#124 database containing the 371 baseline
portal pages and a directory of the corresponding complete TRCA raw capture files:

```sh
scrapers/.venv/bin/python docs/portal-refresh-validation-2026-10-22/validate.py \
  --baseline /path/to/bids.sqlite \
  --trca-recordings /path/to/trca-recordings \
  --out-dir /path/to/new-replay
```

The baseline opens read-only. The replay uses the saved Zoo fixture and performs no network
requests. The local evidence is retained under `/home/alex/tb-data/operations/140/`.
Normal off-peak nightly operation applies this refresh policy on subsequent source captures.
