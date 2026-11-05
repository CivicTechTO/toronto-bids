# Whole-inventory City cached bid recovery — November 5, 2026

The frozen November 4 inventory contained 106 awarded City solicitations with 109 cached report associations requiring attribution review. This pass accounts for all of them together. It recovers 50 solicitations through 300 source-supported bid observations, including 145 literal prices, without model calls or changes to the extraction cache/prompt.

The [complete machine-readable case review](city-cached-bid-review-2026-11-05.json) retains the frozen inventory, its source/cache hashes, every report association and a final disposition/reason for each case. The [source investigation](city-cached-source-review-2026-11-04.md) records independent identity findings and source-role pitfalls. The committed runtime registry is `scrapers/toronto_bids/data/city_cached_bid_reviews.json`.

## Reconciliation

| Final disposition | Solicitations |
| --- | ---: |
| Recovered from independently identified, source-reviewed submissions | 50 |
| Identity still unresolved | 43 |
| Identity supported but submission scope/source remains held | 11 |
| Ancestor qualification mentioned by a later event only | 2 |
| Total | 106 |

The 50 approved sources are 23 BD reports (165 observations, 132 prices) and 27 BA reports (135 observations, 13 prices). Twenty-seven recoveries use historical crosswalk identities; 23 use primary Doc assertions. Thirty-two historical identity proofs pass the existing compound-source gate; five of those identities still have held bid scope. The earlier 130 crosswalk proofs and their original publisher records remain unchanged. New proofs use the same pinned November 1 original publisher population and pass current production identity-field checks; the November 4 research capture is separate corroborating evidence.

Source review restores literal wrapped names and correction footnotes where supported. All inferred compliance is withheld. Tax metadata survives only where the bid table explicitly states it. Informal receipts and prequalification responses retain their actual stage in the exported proof; they do not become downstream tender bidders. This pass leaves complex lot/category submissions, the RTP with repeated technology submissions and the internally contradictory Doc4566099607 table held. It does not attach later tender bids to ancestors Doc3512316731 or Doc4906085019.

The initial research hold for Vipond Inc was resolved through an explicit literal-name projection in 2023.BA37.2. The source's extreme Trio Roofing price in 2022.BA184.2 is retained verbatim (`$ 82,820,035,000.00`) with unknown tax basis, rather than silently corrected. Missing coverage and held cases are not claims of zero bids or permanent inaccessibility.

## Fresh production-copy rehearsal

A SQLite online backup of production was taken November 5 and initialized with the new schema. Validation and export ran against that copy, with Slack disabled. Production was not changed. The checks used held report/agenda bytes for new approvals and then the exported evidence for portable replay with no local source files.

| Measurement | Before | After |
| --- | ---: | ---: |
| Awarded City solicitations | 6,451 | 6,451 |
| With captured bids | 2,642 | 2,692 |
| Without captured bids | 3,809 | 3,759 |
| Cached observations awaiting attribution | 106 | 56 |
| Reviewed historical identity proofs | 130 | 162 |
| Reviewed bid projection proofs | 0 | 50 |
| Ambiguous references | 0 | 0 |
| Canonical bid attribution mismatches | 0 | 0 |

The rehearsal verified:

- Exactly 300 additional bid rows, 145 priced; every new projection resolves to its reviewed City target.
- Every pre-existing bid ID, first-seen date and source fact survives. Existing City solicitation canonical IDs remain stable.
- Raw extraction caches, PDF records/text, council items, awards and agency source facts remain unchanged (canonical assignment is checked separately).
- Repeating resolution preserves bid IDs/first-seen dates and proof counts. The held ancestor, conflicting-table and category examples gain no unsupported bids.
- SQLite quick check and foreign-key check pass.
- Normal `tb export` succeeds with the copied database and portable evidence: JSON, schema dictionary, CSV and Parquet include the new review ledger.

Private reproducibility artifacts are under `.validation-city-cached-2026-11-04/`: `final-rehearsal.py`, the untouched baseline and frozen inventory, source review scripts, and `final-rehearsal-2026-11-05-v2/validation.json`. The public case review and committed proof registries carry the portable inventory and witnesses; local paths are not evidence of identity.

## Regression coverage and deployment

The full offline suite passes: **1,585 tests**. Regression cases cover immutable cache retention; source price footnotes and withheld compliance; ordinary resolution and explicit backfills; changed source, text, cache, title, reference or ledger; removed applied proofs; ancestor/conflicting-table identities; crossed or truncated prices; invented tax; no-bid outcomes; moved hash-checked PDFs; release portability; whole-inventory reconciliation; and a late canonical conflict rolling back both new bids and evidence.

After merge, deploy the code, initialize the schema and run ordinary resolution/export against a backed-up production database. New approvals require the held original reports/agendas and current publisher identity context. Any drift must stop application for explicit re-review. This rehearsal is not a production recovery claim. Issues #164 and #167 remain broader capture/coverage work after this bounded cached-source pass.
