# Completed City recovery deployment review — November 11, 2026

Review base: production/main `88c4784d55c700402f394f661563dd046a469a6b`.
Scope: the cumulative 88-event delivery, without further discovery or a claim that
the remaining awarded City solicitations are unrecoverable.

## Standards

The independent standards review found one documented source-role violation in
`linking/city_bulk_receipts.py`: source rows were bounded by model-returned party
names, so an omitted party could lend its monetary or No Bid outcome to another
name. The reproductions failed before correction. Admission now requires each
party's immediately following nonempty source line to contain its response and
bounds that row witness to the response line. Both regression cases pass.

The reviewer independently replayed the six actual tender-table sources against
the authoritative read-only database after correction. All 21 submitted parties
remain supported, and both explicit No Bid parties remain excluded. No affected
claim was found in the delivered cohort. No remaining material standards blocker
was identified.

A judgment-call smell remains: native single-submission and reviewed-receipt paths
share similar PDF/cache provenance checks. Consolidation is optional refactoring,
not a deployment requirement.

## Spec

The independent spec review found one partial requirement: the new native
received-list path could admit more names than a source receipt count when the
model omitted its count. Native list lead counts now bound names independently of
model output. The reviewer's exact reproduction is refused after correction.

Applying that new guard indiscriminately to old modern lists would rewrite an
existing valid immutable proof for Doc4833008494. Its source contains one On-Site
bidder and two Consignment bidders; an earlier receipt header belongs to another
response scope. The source supports the stored participants. The reviewed bounded
change therefore applies the new list-count guard to historical/native admission
and preserves existing modern proofs. General lead-count validation for modern
received lists remains deferred and is documented explicitly; modern table counts
continue to be checked.

No scope creep or additional material spec defect was identified. The reviewers
confirmed the source-row and native-count corrections. All 35 previous modern
proposals reproduce byte-for-byte. The full offline suite passes with 1,832 tests
and one existing skip. Final production-copy rehearsal and artifact verification
are recorded with the deployment preparation artifacts.

Findings: one Standards violation addressed, one Spec gap addressed within the
bounded delivery; no outstanding review blocker for the verified 88-event cohort.
