# City identity holds — whole-cohort review, November 5, 2026

This pass reviewed all **43 identity-unresolved cases** left by #295 together. The [complete case review](city-identity-holds-review-2026-11-05.json) retains the frozen inventory, source hashes, original refusals, independent source findings and final decisions. The [22-case source investigation](city-identity-holds-source-review-2026-11-05.md) details the type/date/call cases; the case review records the other 21 supplier/value cases individually. No model requests, prompt edits or raw cache changes were made.

## Result

| Outcome | Cases |
| --- | ---: |
| Source-supported bids recovered | 9 |
| Identity established; bid category/submission scope still held | 1 |
| Identity/source context remains held | 33 |
| Total | 43 |

Ten new identity proofs append to the existing 162: seven explicit type discrepancies, two separately named initial awards and one ordinary compound proof after recognizing literal `Request of Quotation`. The existing proofs and publisher population remain unchanged. Nine new source-reviewed bid projections append to the existing 50 and retain **39 bid observations, including 23 prices**.

### Why these identities pass

Seven reports disagree with the original publisher on procurement method. Their exact issue/close dates, full recommended supplier and exact initial net award uniquely identify one original record across **all methods**. An explicit `reviewed_type_discrepancy` proof retains both methods and the review reason; ordinary proposal generation continues to refuse disagreements. Current ambiguity is also checked across all methods, rather than relying on the disputed type to narrow the match. Both source records remain unchanged.

Two combined winner headings hide separately named initial awards: W.M. Weller in [2015.BD62.6](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-87062.pdf) and CIMA in [2016.BD93.10](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-95135.pdf). Their proof binds the exact recommendation, first supplier heading and initial net value. No option or combined value is selected to obtain a match. BD93.10's typed title prints its call with spaces; its labelled source Call Number, dates and scoped award independently corroborate it. The original publisher Posting_Title is research corroboration only; runtime approval does not use that currently untracked field.

UBA in [2013.BD141.2](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-61311.pdf) passes the ordinary complete compound gate after recognizing the literal wording `Request of Quotation`. Colonless date syntax is recognized too, but its held case still fails full supplier/scope checks and gains no identity.

### Bid boundaries retained

- SCI Interiors gains a proven identity but no flattened category/alternative-submission bid rows.
- F. Hams retains nine named receipts, including two informal submissions; both conditional prompt-payment price columns remain withheld.
- Inter-Provincial retains four receipts, withholding the first two discount-qualified prices while preserving the two unqualified prices and correction marker.
- Asco's source declares eight bids but prints four named rows. Only those four are projected, with an exported `declared_coverage` discrepancy; four unnamed bids remain unavailable in that report.
- Weller's four prices retain `/Hour`; numeric totals stay NULL. A stripped unit is refused. Its fifth receipt remains unpriced.
- CIMA's four proponents remain unpriced: three cached amounts are award values, not bid prices. Inferred compliance is withheld throughout.
- Other recovered simple price rows retain source correction/non-compliance markers and explicit HST basis.

### Why the remaining cases stay held

The supplier/value review found rounded whole-dollar recommendations against cent-precise publisher amounts, spelling/trading-name differences, and multi-part report scopes. No rounding tolerance, inferred corporate alias, dropped geography or fuzzy name rule was introduced. Exact scoped matches for some multi-part awards are recorded as candidates; their submission scopes still need review before application.

Date contradictions are retained as contradictions. Call 6701-13-3083 appears in two council contexts; the Gal Power context passes complete compound proof, but Northern Generator's original initial award is $904,673.00 against $897,918.00 in the publisher. The group cannot be admitted merely because one side passes. Source references can preserve those geographic scopes if an independent bridge eventually resolves the second identity.

## Production-copy verification

A fresh read-only backup of production was replayed and exported under the merged #295 baseline. Production itself was not changed.

| Measurement | Before | After |
| --- | ---: | ---: |
| Awarded City solicitations | 6,451 | 6,451 |
| With bids | 2,692 | 2,701 |
| Without bids | 3,759 | 3,750 |
| Cached observations needing attribution review | 56 | 47 |
| Reviewed identity proofs | 162 | 172 |
| Reviewed bid proofs | 50 | 59 |

Exactly 39 bid rows are added, with 23 prices. Every existing bid fact, ID and first-seen value survives, as do existing City canonical IDs. Raw extraction caches, PDF records/text, council items, awards and agency facts remain unchanged. Repeating resolution preserves IDs and proof counts. There are no ambiguous references or canonical attribution mismatches; SQLite quick/foreign-key checks pass. Normal JSON/schema/CSV/Parquet export succeeds, including portable replay without local original files.

The full offline suite passes: **1,604 tests**. Regression coverage exercises default type refusal, explicit original/current ambiguity across methods, altered source/publisher types, dates, full names and exact values; crossed suppliers, options and combined award scopes; literal wording/date/call typography; hourly units and whole-cohort/source-completeness accounting. The original #295 report remains an immutable historical review, with append-only follow-up projections tested separately.

Private reproduction artifacts are under `.validation-city-identity-holds-2026-11-05/`: `before.sqlite`, `inventory.json`, `investigate.py`, source packs, independent research/probes, `build-proofs.py`, `build-bids.py`, `build-report.py`, `rehearse.py`, and `rehearsal/result.json`. The committed registries and complete case review retain the portable source evidence and decisions.

After merge, back up production under the shared writer lock, resolve with the held originals, rebuild suppliers, export and publish. Revalidate the expected nine recovered solicitations and preserve all original facts. Any changed source, cache, publisher identity or ambiguity must stop application for explicit re-review. Issues #164/#167 remain broader coverage work; this pass does not close them.
