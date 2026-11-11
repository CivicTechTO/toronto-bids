# Received-submission recovery validation — November 11, 2026

This is a component of the exhaustive City recovery workload. It is local and unpublished; it does not establish that the remaining cases are unavailable.

## Complete cohort and source roles

The reviewed cohort contains 50 missing City documents, 52 original report contexts and 244 cached party observations. All original PDF hashes were checked. Forty-eight documents now have admitted, independently identified received-participation reviews, contributing 208 distinct event participants. A second report for RFQ 6106-18-0097 repeats the same two submitted firms after rescinding and replacing an award; it is not another procurement or another recovery.

Every selected cached party observation has one review entry. The immutable shared extraction cache and prompt/version are unchanged. Full selected contracts, model counts/stages, prices and statuses remain withheld evidence. New bid rows are unpriced and carry no overall compliance or award claim. Literal source receipt sections, complete party-name fragments and scope labels are pinned. Two split table-count headings retain the original column fragments rather than guessing from a supplier heading.

[RFQ 6102-15-3139](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-87058.pdf) illustrates the boundary: the original review retains Action Hydraulics' submitted Appendix C response and explicit No Bid in Appendix C1 separately. Event participation does not assert a C1 submission. (The exact source URL and original bytes are authoritative in the committed review registry.) Fleetway's unopened price envelope in RFP 9117-15-0142 does not turn its explicitly received proposal into No Bid. RFQ 1205-15-5020 retains the actual Parts B/C receipt table while withholding the cache's broader stage interpretation.

## Source conflicts retained as unresolved

- 9117177046: Body, title and Call Number field name 9117-17-7046, but the received-proposals table explicitly names 9117-17-7047. Do not assume a typo. Sources: [2017.BA39.2](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-106497.pdf).
- 9117197172: Two reports for the same call each list five proposals, but the fourth party changes from Mark G. Anderson Consultants, LLC to MGAC Canada ULC. Preserve revisions separately; do not infer an alias or a sixth bidder by union. Sources: [2019.BA49.3](https://www.toronto.ca/legdocs/mmis/2019/ba/bgrd/backgroundfile-138793.pdf), [2019.BA59.1](https://www.toronto.ca/legdocs/mmis/2019/ba/bgrd/backgroundfile-141174.pdf).

## Cumulative production-copy outcome

A fresh SQLite backup of production was opened with the branch code. Ordinary full procurement resolution, repeat resolution and JSON export ran over all existing and new evidence.

- Missing awarded City solicitations: **3,316 → 3,249**, or **67** additional recovered documents: 19 earlier single-submission cases plus these 48 received-list cases.
- New unpriced participation rows: **227**. This is a row count, not another recovery count.
- All **21,715** original bid IDs, payloads and first-seen values survived. Original solicitations, awards, composite awards, noncompetitive facts, agency facts and extraction caches were unchanged.
- Every existing City solicitation canonical ID and every prior immutable crosswalk proof survived. Foreign keys and source/canonical bid attribution agree.
- Repeat resolution is stable. Every recovered solicitation exports its exact approved participant names, withheld prices/statuses and original report; all 67 evidence records are present.
- Full offline suite: **1,778 passed, 1 skipped**. Targeted source tests cover full observation accounting, sibling isolation, conflicting call numbers, wrong/missing literal names, out-of-table names, No Bid exclusion, quote-bound scopes, split table headings and changed original/applied evidence.

The private reproducible artifacts are `.validation-city-exhaustive-2026-11-10/received-submission-proposals.json`, `native-rehearsal.json`, `native-rehearsal.sqlite`, `native-bids.json` and their build/rehearsal scripts. Production remains at 3,316 unresolved; this component has not been deployed, pushed or proposed as a standalone small PR. Zero cases have affirmative categorical-unrecoverability evidence. The prepared bulk City data request remains unsent pending explicit authorization.
