# City scoped bid validation — 2026-11-05

The frozen eleven-case scope/source hold inventory is fully represented using separately proven procurement identities and reviewed original PDFs. This removes eleven missing-bid cases in a production-copy replay, from **3,750 to 3,739**. The contradictory Doc4566099607 identity remains held outside this cohort.

The [machine-readable case review](city-scoped-bid-review-2026-11-05.json) records the frozen inventory hash, case-level counts, source hashes, proof digests and remaining source uncertainties. [Source research](city-scoped-bid-sources-2026-11-05.md) records the original evidence; [the mechanism](../scoped-city-bids.md) explains admission and deployment.

## Reconciled result

| Fact | Count |
| --- | ---: |
| Recovered City solicitations | 11 |
| Firm/event participation records | 93 |
| Source scopes, including unspecified scopes | 49 |
| Response observations | 321 |
| Submitted response cells | 300 |
| Explicit No Bid cells | 20 |
| Unscoped outcome observations | 1 |
| Unresolved party contexts | 4 |
| Quoted value cells | 104 |
| Currency values | 102 |
| Percentage discounts, excluded from currency totals | 2 |

These are 93 firm/event participations, not 93 globally distinct companies. Four submitted cells retain unresolved party names, leaving 296 submitted cells linked to proven participants. Repeated lot/category/alternative responses do not increase the firm count.

The source research for 2024.BA61.3 initially counted 32 identities using the narrower supplier key. Existing full legal-form identity normalization proves Garda Canada Security Corp./Corporation is one firm: the accepted representation has **31 participants and 70 response observations**. Both literal aliases survive. 2025.BA133.2 has 26 participants and 124 responses, including responses for cancelled Category B.

## Source qualifications retained

- Five Start/Five Star spelling conflict, two truncated Category B party names, and the compound `2302726 Ontario Inc. Enqueue Staffing*` entry remain unresolved; no extra companies are invented.
- Amovo's three labelled alternatives and 20 explicit No Bid cells remain separate. Blank lot cells are not classified as No Bid.
- Part A discounts off MSRP remain percentages, while Part B's one-year prices remain currency values. KTI's unstated-scope outcome stays unscoped.
- Hank's prompt-payment condition and the Part C budget outcome remain attached to their source context. Noncompliance is retained only for the supplier/scope printed by the publisher.
- Apinds' unusually large published prices, source spelling errors, net-of-HST versus net-of-all-tax wording and unknown tax bases remain literal. Prices are not silently corrected or collapsed into one procurement total.

## Verification

All eleven held originals passed literal PDF crop and geometric supplier/scope/alternative verification. The replay used a fresh SQLite backup of production, no model requests and the unchanged extraction version `v1-b63aa8c835ae`.

The replay added 93 participation rows, 49 scopes, 321 responses, 104 values and eleven immutable evidence entries. Repeated resolution retained IDs and `first_seen`; explicit BD backfill retained scoped facts. Prior bid facts, canonical City IDs, raw extraction cache, document records, awards, agency facts, 172 historical identity proofs and 59 scalar bid-review proofs were preserved. Supplier rebuild produced 11,626 suppliers. SQLite quick and foreign-key checks passed.

Normal JSON, CSV, Parquet and schema exports passed. An exported database copy with all scoped original-PDF paths unavailable also resolved/exported successfully using previously accepted immutable proofs. New admission still requires the original bytes.

Regression tests cover source/hash/cache drift, changed or removed applied reviews, price cells crossing supplier/scope/alternative boundaries, copied outcomes, percentages, No Bid, unknown parties, canonical-conflict rollback, stable history, portable replay, explicit backfill and whole-cohort reconciliation. The frontend fixture covers scoped counts, alternatives, No Bid, percentages, conditions, unknown parties, scope outcomes and supplier links. Full production-copy website generation and internal-link validation complement fixture tests.

Final checks passed: **1,626 backend tests**, **138 frontend unit tests**, **114 fixture website tests** (four existing optional-fixture tests skipped), and **five full-dataset internal-link tests**. The full website build generated 28,009 HTML pages.

Production deployment and publication are pending merge. These counts describe the rehearsed result, not an already published recovery.
