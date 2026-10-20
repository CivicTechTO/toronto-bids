# Incomplete Ariba bundle retries (#199 / #243)

An existing canonical zip proves bytes are held, not that capture is complete. Nightly
capture and the near-close warning now use `bundle_complete`: a zip must exist and its
durable omission record must show neither missing filenames nor collapsed links.
Malformed evidence stays eligible and fails visibly rather than being erased.

## Replacement and gap preservation

- #199 compares recursive leaf counts from the held zip and the completed temporary
  candidate. A lower count, or a traversal limit that prevents a complete count, cannot
  authorize replacement. Closed-event salvage uses the same guard.
- A retry of a known incomplete archive must also retain every held outer filename.
  Capturing one missing file while losing a previously captured file is refused even
  when the leaf counts match. A changed source filename may therefore need manual
  inspection; the earlier bytes stay held.
- Previously named omissions clear only when that file is captured. A changed listing
  that omits the name does not prove recovery. Remaining old names and new failures
  are recorded together.
- A successful partial improvement is indexed, remains retryable, and still marks the
  nightly attachment step failed. A complete recovery clears its gap record and is
  skipped on subsequent nights.
- Failed/refused retries retain the earlier zip, gap evidence and index. An atomic
  replacement failure restores any changed gap record. Partial downloads remain for
  later attempts.
- Closed or access-denied retry outcomes retain the held archive and report unresolved
  gaps with the observed reason. Ordinary clean skips without recorded gaps remain
  skips. Collapsed-link evidence has no recoverable filename identity, so it remains
  visible until audited rather than being cleared by an unrelated collision-free run.

The policy refuses replacement; it does not merge archives or add a manual promotion
workflow. #243 is stacked on the #199 guard change and must land after it.

## Verification

The 14 retry regression cases exercise real selection, capture publication, and index
rebuilding with an offline browser boundary. They cover complete skips, complete
recovery, errors, closure/denial, poorer candidates, exchanging one gap for another,
changed listings, malformed evidence, near-close warnings, atomic replacement failure,
new partial captures, incremental improvements, and unresolved collisions.

Full suite: **891 passed**.

For live verification, the original `Doc5909867893` zip and its omission record were
copied to isolated staging. A database containing just that event drove the real
`capture_attachments` path, including eligibility, supplier login, capture and indexing.

| Observation | Before | After |
| --- | --- | --- |
| Held files / listed files | 2 / 3 | 3 / 3 |
| Attachment index rows | 2 | 3 |
| Missing Form of Agreement | Recorded missing | Captured |
| Omission sidecar | Present | Cleared |
| Capture failures | — | None |

The run took 45.2 seconds; the recovered zip passes `testzip()` and has three recursive
leaves. Production bytes and the production index were not modified. Original and
recovered bundles were SHA-256 verified into
`/home/alex/tb-data/ariba/_243_recovery_20261020/`, with logs and `verification.json`.

The RFQ's section 1.5 says the City Online Procurement System countdown takes
precedence over other deadline displays. At `2026-10-20T18:06:06.879Z`, the Sourcing
countdown showed `9 days 21:53:53`, consistent with **October 30, 2026 at 16:00 UTC**.
This independently supports the Discovery posting's advertised deadline.
