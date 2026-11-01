# Historical City crosswalk validation — 2026-11-01

The post-BD-run production snapshot has 3,932 awarded City solicitations without attributed bids. Refreshing the earlier search inventory finds **768 historical candidate solicitations with 3,897 bid observations already held**, including 20 candidates with multiple council references. These are discovery candidates, not accepted identities. Original numeric-key encoding is used only to find pairs to inspect.

The pinned independent publisher capture contains **7,804 original OData records**. The [publisher investigation](city-call-publisher-keys-2026-11-01.md) documents original field availability, response hashes, source roles and counterexamples. Existing enriched solicitation titles and old winner/value links were excluded from proof.

## Reviewed result

**130 candidate relationships pass the corroborated compound evidence gate**, connecting **557 existing bid observations**. Every one has:

- one original agenda item/title and unambiguous labelled native Call Number;
- matching original issued and closed dates, with no date tolerance;
- matching explicitly stated solicitation type;
- the complete supplier identity, allowing legal-form/punctuation typography but retaining geography, spelling and joint-venture components;
- an exactly equal initial award value explicitly net of applicable taxes, with no rounding, tax conversion, option-year substitution or period aggregation;
- one matching record in the complete original publisher population, with no conflicting currently held award/source identity.

The proof class is **`corroborated_compound`**, not a publisher assertion that Call Number encoding is an identifier-equivalence rule. The full original records for these relationships and a compact complete uniqueness population ship with the review bundle. Each accepted relationship retains source URLs, publisher row ID, record/page/item hashes and literal witnesses. No LLM requests were made; extraction caches and prompt versions are unchanged.

The implementation also supports a distinct `publisher_native_call` proof class, but **none is approved in this pass**. The direct original assertion for `3707190005` conflicts with the council item's issue/closing dates and remains held.

## Source examples and root cause

| Primary council source | Printed call | Original numeric City key | Independent comparison |
| --- | --- | --- | --- |
| [2013.BD122.10](https://secure.toronto.ca/council/report.do?meeting=2013.BD122&type=agenda) | 123-2013 | 1232013000 | Furfari Paving; issued March 28, closed April 15; initial net award $903,710.00. |
| [2017.BA35.6](https://secure.toronto.ca/council/report.do?meeting=2017.BA35&type=agenda) | 0401-17-0113 | 0401170113 | Orbis Canada; issued April 6, closed May 2; initial net award $484,000. The $605,000 potential total including the option is not substituted. |
| [2015.BD40.10](https://secure.toronto.ca/council/report.do?meeting=2015.BD40&type=agenda) | 3909-15-7084 | 3909157084 | Furcon Environmental; issued April 21, closed May 8; initial three-year net award $3,000,000. The $5,000,000 including option years is a different scope. |
| [2020.BA64.4](https://secure.toronto.ca/council/report.do?meeting=2020.BA64&type=agenda) | 0103-19-0030 | 0103190030 | Nitsom Promotional Manufacturing; issued February 5, closed April 12, 2019; initial net award $163,209. |

The old legacy matcher skips meetings dated 2019 onward on the assumption they name document numbers directly. **77 of the reviewed relationships are in 2019/2020 meetings that still name native calls**. The new source-reviewed path does not infer a namespace from meeting year. The other accepted council years are 2013 (7), 2014 (3), 2015 (14), 2016 (15), 2017 (7) and 2018 (7). Source types are 96 RFT, 21 RFQ and 13 RFP.

Fourteen retained primary-source fixtures cover nine positive cases spanning council years and solicitation types, plus five negative controls. The latter keep the two known wrong-period joins, the unresolved spelling/JV identities and the direct-call date conflict refused. Original item text and publisher records are retained, rather than treating the old matcher's decisions as truth.

## Current-source guard

Award rows are an archive: an earlier matching price remains after an upstream correction or removal. A reproduced regression showed that checking existence in `award` alone could silently reuse stale proof. `solicitation.publisher_identity_json` now retains the latest original identity fields and current supplier/value pairs. Resolution compares that projection to the reviewed publisher record before reusing a link; the immutable ledger retains the original accepted evidence separately. New ingestion preserves old award rows while replacing an empty/changed current projection. Source bookkeeping and supplier-array order do not create false evidence changes.

The first deployment must populate this newly introduced column from original OData data before applying links. The rehearsal seeds only the new identity metadata from the pinned official capture; all pre-existing source fields remain unchanged. [Operational contract](../city-call-crosswalk.md).

## Production-copy rehearsal

| Measure | Before | After |
| --- | ---: | ---: |
| Awarded City solicitations with attributed bids | 2,512 | 2,642 |
| Awarded City solicitations without attributed bids | 3,932 | 3,802 |
| Newly attributed existing bid observations | — | 557 |
| Reviewed proof records | 0 | 130 |
| Canonical/reference attribution mismatches | 0 | 0 |

The rehearsal preserves every pre-existing solicitation/award/bid/agency/attachment/cache payload, existing City-document IDs, and historical bid IDs/timestamps. It adds the original publisher identity projection and reviewed links/evidence; canonical redirects record proof hashes. Foreign keys and SQLite integrity checks pass. A second resolver pass leaves identities, links and evidence history unchanged. Legacy supplier/value rebuilding preserves reviewed links.

**Production has not been modified by this implementation task.** Results are from `.validation-city-crosswalk-2026-11-01/rehearsal.sqlite`; after merge, controlled deployment and publication are still required.

## Remaining candidates

The other **638** remain unresolved in this pass:

| Refusal class | Candidates |
| --- | ---: |
| Compound facts fail a unique global source match | 344 |
| Exact issued/closed witnesses absent or conflicting | 111 |
| Source solicitation types disagree or are unsupported | 87 |
| Single winner / explicit net award witness absent | 50 |
| Multiple council contexts for native call | 20 |
| Publisher document record missing or repeated | 7 |
| Initial award role missing | 6 |
| Initial award scope disagrees | 4 |
| Native call role unconfirmed | 6 |
| Title identifies multiple/no native calls | 2 |
| Invalid publisher date ordering | 1 |

These are evidence/admission gaps, not proof that records are inaccessible, procurements differ, or no bids were submitted. Do not broaden supplier matching, guess dates, select an option amount, flatten multi-call items, or approve padded keys to increase the recovered count. Further source/reissue evidence can support a separately reviewed proof without re-extracting unchanged bids.
