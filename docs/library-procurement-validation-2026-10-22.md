# Toronto Public Library procurement validation — 2026-10-22

Issue #110's own-site premise was checked against the [Library's Doing Business page](https://tpl.ca/about-the-library/business-with-the-library/). The old `torontopubliclibrary.ca` URL redirects there. The procurement section supplies a link to [the Library's Bonfire purchasing portal](https://torontopubliclibrary.bonfirehub.ca/portal/?tab=openOpportunities); the inspected Library page itself contains no opportunity listings or local award register.

The maintainer explicitly confirmed Bonfire permission with respectful usage and backoff. The [permission record](permissions/2026-10-22-bonfire.md) records that instruction and the bounded public-metadata scope. The implementation follows the anonymous requests identified in the portal's public JavaScript; it requires no browser or account.

## Actual source capture

A live capture made three successful serial requests: public portal page, Open collection and Past collection. It returned **412 distinct project pages**: one open and 411 past opportunities. The oldest published closing date in the returned history is 2014-09-29. The normalized UI statuses were **1 Open, 400 Closed and 11 Cancelled**. These counts describe the collections returned on this date, not proof of an exhaustive Library procurement history.

The printed reference is not a unique key. For example, `221-21-TPL-RFP` appears on four distinct public project pages. All four remain separate listings/procurement identities. The open project was `210-26-TPL-RFP`, Self-Checkout Kiosks and RFID Circulation Solution, closing at `2026-11-13T16:00:00+00:00`. The explicit UTC interpretation comes from the portal's `BFUtil.createMomentFromUtcDateString` calls, not a guessed Toronto local offset.

The captured collections publish no winning supplier or award value. All returned `IsPublicAward` flags were false. Closed opportunities were retained as listings, without producing award or bid facts. Raw responses retain the underlying workflow codes and other published metadata for later review.

Final live response SHA-256 values:

| Response | SHA-256 |
|---|---|
| Portal page | `b50884f5ec3589efa5029fb1b29cf016ef2cd9eb2f983f61631cd0ac5ed30100` |
| Open collection | `ebebed1e45de310ff830aa4dd878c086a438e8418040d1ad919c6779a20b8878` |
| Past collection | `f8e919a9c3747a32350dfe82a15d0c55383446834d238c6f134e64d0fc4a54e8` |

## Preservation and end-to-end checks

Used SQLite's backup API to make an isolated copy of production at `/home/alex/tb-data/operations/110/rehearsal/bids.sqlite`. Ran the implemented live capture on the copy, then replayed all retained responses offline. All **412 listing IDs, procurement IDs and first-seen timestamps** remained stable. Every listing has a procurement identity; foreign-key checking reported zero violations.

Generated JSON, schema, CSV zip and all Parquet tables. The Library has 412 listings consistently across JSON, CSV and Parquet, with attribution and no invented awards/bids. Digests of the original source-table payloads, excluding derived supplier/procurement IDs, matched before and after. Production was not modified. Operational original responses, manifests, exports, replay evidence and payload digests are retained under `/home/alex/tb-data/operations/110/`.

The complete offline suite passed: **1,166 tests**. Real public HTML/JSON fixtures exercise full history, repeated/missing references, stable refreshes, metadata preservation and complete-batch refusal. Retry tests cover transport failures, 429/server errors, bounded exponential delays, Retry-After seconds and HTTP dates, long-delay deferral, and persisted deadlines preventing early contact across command runs. A full mocked nightly exercises the real adapter and confirms history plus JSON/CSV/Parquet output. Failure tests confirm a Bonfire error marks its nightly step failed while export still runs.

## Scope after this change

#110's public opportunity capture is implemented as the first consumer of the shared Bonfire adapter under #134. Additional Bonfire buyers require source/configuration verification and onboarding; they are not automatically crawled. Project documents, account actions, bidder/submission data and separate Library board-report award extraction remain outside this listing adapter.
