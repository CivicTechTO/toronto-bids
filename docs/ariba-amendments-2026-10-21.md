# Ariba amendment preservation (#256)

The five failing 2026-10-21 nightly retries held all current attachments as partial files,
but publication refused to replace older bundles: amended listings no longer contained
some old filenames. Renamed withdrawals would never return on another retry. A separate
regression test also reproduced same-name replacement losing older bytes.

## Resulting contract

Each publication retains every old outer archive member byte for byte and merges acquired
current attachments. Existing paths remain stable. A different payload under an occupied
name receives `_versions/<sha256>/<name>`; recurring/reverted versions reuse the existing
matching stored version. Duplicate labels remain disambiguated independently of content.
Retained nested ZIPs preserve their complete original payload, rather than just their
current leaf counts.

A provenance manifest travels atomically with the ZIP. It records document identity,
current source keys and labels, their stored paths (or null for missing current files),
content SHA-256 hashes, accumulated observed source keys, and known observation times.
A stored path absent from the current listing is historical. Legacy source keys/dates
remain unknown instead of being inferred from filenames. The manifest lives in the ZIP
comment when it fits; a larger manifest lives in a marked metadata member referenced by
the comment. Recursive attachment indexing recognizes that marker and excludes only its
metadata member; ordinary source documents with similar names are not blanket-filtered.

Current omissions come from the freshly traversed listing, allowing withdrawn old named
gaps to retire. Historical bytes never count as proof of a missing current revision.
Recorded collapsed-link uncertainty remains a failure until separately audited. Both the
sidecar and embedded missing-current-file evidence keep a bundle eligible for retry.

The candidate is checked against every historical member hash and every new member hash,
then against the strict recursive index limits, before replacement. A failed atomic swap
leaves the previous ZIP/provenance and gap evidence intact; acquired files remain resumable.
Changed/unreadable partial fingerprints retire working directories instead of reusing
stale slots. Their complete files join a subsequent successful merge as historical evidence,
and their original snapshots/interrupted residue remain retained separately.

Closed salvage without a fresh listing keeps its conservative guard. Complete bundles
remain skipped by the existing selector: this change reconciles amendments when capture
runs and does not introduce polling of already complete events.

## Verification and production recovery

The two initial red tests reproduced amended-publication refusal and same-name content
loss. The final offline suite passed **965 tests**. Regression coverage includes changed
names, same-name revisions, duplicate labels, reversion, large embedded manifests,
recursive indexing, stale partials after interruption, real current gaps despite held older
bytes, corruption, collision evidence, and atomic replacement failures. Older tests were
updated to assert retention of historical bytes rather than refusal merely because a
verified new listing is shorter.

Live validation logged in through the existing authorized supplier workflow, visited all
five Sourcing events, and asserted each page's stated document number before constructing
its file source. All five captured successfully in an isolated store: zero failures,
156.02 seconds. Each fresh listing fingerprint exactly matched its corresponding saved
partial fingerprint; all 51 current attachments were already held and reusable.
The final implementation then replayed those verified listings independently, requiring
zero new downloads, validating every historical member hash, and passing ZIP CRC checks.

| Document | Posting | Current held/listed | Historical files retained | Indexed leaves |
| --- | --- | ---: | ---: | ---: |
| 5861259648 | 1110024604 | 17/17 | 3 | 47 |
| 5899231915 | 1110026285 | 5/5 | 1 | 6 |
| 5824279749 | 1110027334 | 4/4 | 1 | 5 |
| 5815425347 | 1110026258 | 9/9 | 1 | 32 |
| 5881339695 | 1110027694 | 16/16 | 1 | 39 |

After a SQLite backup, the five verified bundles replaced their matching original
production bundles and only their attachment indexes were rebuilt. All original archive
member hashes were verified first. Total indexed attachments increased **3,031 → 3,058**;
all **2,929 unrelated index rows** were compared verbatim and preserved. Every target now
passes the shared completeness predicate with no omission sidecar, and none remains in
the near-close warning. The urgent Doc5861259648 is therefore recovered before its reported
2026-10-22 close date.

Rollback ZIPs, original gaps/partials, the SQLite backup, source-identity checks and final
recovery manifest are under `/home/alex/tb-data/operations/256/`. Isolated validation and
replay scripts/evidence are under `/tmp/tb-256/`. Source credentials and authenticated
session artifacts are not committed. The local export was refreshed from the recovered
index; this work did not publish a release, submit a bid, invoke model extraction, or rerun
the entire nightly.
