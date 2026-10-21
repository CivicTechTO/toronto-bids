# Source-supported agency recovery (#258)

Production recovery on 2026-10-21 added **20 distinct agency award rows and 10 bid
rows**. Every original baseline gap has an evidence-linked disposition in
[`agency-identity-recovery-2026-10-21.json`](agency-identity-recovery-2026-10-21.json).
The earlier #257 audit supplies the original contract evidence referenced by each case.

| Original missing observations | Awards | Bids |
|---|---:|---:|
| Restored under a supported identity | 30 | 10 |
| Already held under a supported identity | 8 | 0 |
| Explicitly unresolved | 102 | 2 |
| Total reviewed | **140** | **12** |
| Distinct new stored facts | **20** | **10** |

Two vehicle-framework reports describe the same ten vendors and resolve to the same
TRCA adoption identifier. Their twenty prior observations create ten award rows, not
twenty. The eight plant-material observations were already held under the numbered
staff report's identity, so resolving their minutes adds no duplicate awards.

## Evidence and identity rules

`linking/agency_identity.py` reads a small registry of reviewed source relationships
in `data/agency_identity_evidence.json`. A decision must match the buyer/corpus, held
PDF SHA-256, extraction version and exact canonical contract JSON fingerprint. A changed
supplier, title, amount, reference, document, buyer or extraction version invalidates
the match. The existing conservative normalizer handles everything else.

These are inspectable domain facts with cited source passages, not a new document
parser or a fuzzy matcher. The extraction prompt and original cache results are unchanged.
Each accepted decision retains its original payload, identifier roles and proof in the
ledger. Historical refusals remain available after recovery.

The source relationships establish:

| Published source | Stored solicitation identity | Distinction retained |
|---|---|---|
| TRCA DocumentId=19635 | RFQ **10043863** | The report separately prints contract **10043792**; it is not asserted to be an alias. |
| TRCA DocumentId=19889 | RFP **10042179** | Contract **10040929** remains a separate role. |
| TRCA DocumentIds=5341, 5426 | TRCA adoption RFP **10022400** | Ontario framework **OSS-00634452** is not a TRCA call number. |
| January 30, 2026 Board minutes, item 9.7 / A9/26 | **10042179** | Same dated item as the numbered staff report. |
| April 28, 2023 Board minutes, item 8.4 / A65/23 | **10038886** | Same dated item as staff report DocumentId=14410. |
| June 12, 2026 Executive Committee minutes, item 9.6 / B36/26 | **10044507** | Same dated item as staff report DocumentId=20601. |

Twelve held PDFs support the ten registry entries, including the three history corrections
below. Their bytes were hashed and the recorded passages checked against fresh PDF text
renderings. No new source crawl or paid extraction was needed.

## Unproven award amounts remain unresolved

DocumentId=19889 gives **$1,035,000 as an aggregate VOR ceiling**. Its cached extraction
assigns that entire amount to each of eight vendors. Restoring those eight monetary
claims would multiply a shared ceiling into unsupported individual awards.

The reviewed decision restores its eight bidder facts but withholds the eight individual
award claims. The minutes independently support eight vendor memberships with unknown
individual amounts; those are stored with NULL amounts. The original monetary claims
remain in the cache and ledger with an explicit source-review reason.

Coverage distinguishes accepted facts from withheld facts within one contract. Accepted
and refused contract sets can overlap. `coverage:trca` reports the withheld award claims;
`identity:<corpus>` continues to report missing/ambiguous identifiers. Both make agency
enrichment and nightly incomplete while valid facts still derive. Reconciliation follows
an accepted ledger decision to its exact stored identity even when another observation
becomes the final `report_url` on that row; matching report/supplier/amount alone does not
establish an identity.

The other unresolved cases retain the source evidence and reason in the audit and ledger.
They include multi-contract schedules without identifiers, reports containing unrelated
numbered procurements, City-wide shared-service work without an established Zoo buyer
identity, and a revenue-licence narrative without a supported procurement key. No shared
`unknown` identity or generated surrogate was introduced. The broader identity model is #124.

## History-preserving suffix correction

The production rehearsal found that an ordinary full rebuild would replace two existing
award IDs and four bid IDs: their stored `10042722` reference had previously lost the
printed `-01` suffix. Source PDFs 19465, 19466 and 19544 and their exact cached contracts
all print **10042722-01**.

Reviewed history aliases re-key only those bid/award observations whose old reference,
source report, supplier and amount match the proven relationship. The existing alias
transaction preserves IDs, earliest `first_seen`, non-NULL metadata and canonical
collision handling. Supplier/amount equality scopes the migration; it does not prove
the relationship. Other parent/suffixed calls stay separate. Solicitation rows have no
report provenance and retain the existing backfill-only archive contract.

## Production operation and verification

The operation ran only after an isolated rehearsal passed and the user-level nightly
was confirmed stopped (`failed` from its earlier incomplete run, not running).
`/home/alex/tb-data/operations/258/` holds:

- `bids.before-recovery.sqlite`: full SQLite rollback backup.
- `code/`: frozen tested package, with per-file hashes in `verification.json`.
- `recover.py`, `audit.py`: bounded operation and baseline disposition scripts.
- `ledger-baseline.sqlite`: isolated derivation used to initialize #257's original
  decision history; only its evidence tables were copied, never its derived data rows.
- `recovery-audit.json`, `cli-coverage.json`, `verification.json`, `final-verification.json`:
  complete outcomes, preservation checks and export hash.

The operation used only held inputs. It preserved every existing agency row ID and
`first_seen`; the only existing non-NULL fact changes were the six source-verified
reference corrections. RFQ 10043863's previously NULL posted/closing dates were filled
from the supported cached source. Report URL observations refreshed normally. Original
extraction cache rows and unrelated published source facts stayed unchanged; supplier
links were refreshed by the normal supplier-dimension rebuild.

| Production table | Before | After |
|---|---:|---:|
| Agency solicitations | 607 | 610 |
| Agency awards | 991 | 1,011 |
| Agency bids | 1,447 | 1,457 |

The solicitation increase includes the newly preserved suffixed representation; it is not
a count of three newly discovered procurements. SQLite integrity and foreign-key checks
passed. A second full rebuild on an isolated recovered-production copy preserved row
and ledger IDs and `first_seen`.

Local JSON, Parquet, CSV and schema exports were refreshed. The JSON is 36,654,809 bytes,
SHA-256 `554f28bb9163c62c5eff7307b0816ba08842c846335c36cf7660efbf3d4ac478`.
No release was republished; scheduled publication remains separate.

The full offline suite passes **1,072 tests**, covering real held extraction results,
identifier roles, provenance guards, partial recovery, history migration, duplicate
observations, ledger migration and the existing capture/nightly reporting paths.
