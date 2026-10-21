# TRCA historical PDF preservation (#224)

**Final capture:** 825 PDFs held and independently verified; six access-denied policy
shortcuts remain queued. See the final outcome below.

## Public source and scope

The anonymous [TRCA WebLink repository](https://laserfiche.trca.ca/WebLink/) exposes
`0335 LIBRARY / 0335-02 Corporate / 0335-02-04 Minute Books` (folder 1030915).
A plain-HTTP walk on 2026-10-20 found 66 year folders, 1957–2022. This capture follows
all descendants of the 62 folders through 2018; eSCRIBE remains the separate source
for the current board-report corpus.

The initial complete folder walk found 852 descendants: 20 nested folders,
825 PDF/scanned documents (117 native PDFs and 708 documents without a native extension),
six PDF policy shortcuts, and one Word attachment. These are minute-book collections,
including nested meeting minutes, reports, schedules and briefing material; they should
not be described as uniformly meeting agenda packages.

The Word attachment is retained in the discovery inventory as an explicit non-PDF
exclusion. All six shortcuts are queued for public export verification. The sampled
Purchasing-policy shortcut (1546663) returned `Access denied. [9013]` from the public
Download action. Its metadata exposes a target in the staff policy collection; no login
or attempt to bypass access controls was made. A denial remains a visible download
failure with its row still queued, rather than proof of a held document.

## Capture contract

```sh
tb capture-trca-history                 # discover, index, then download
tb capture-trca-history --resume       # retry only the sha256-NULL queue
tb capture-trca-history --limit 3      # bounded successful-download sample
```

Requests use the anonymous session cookie and the public client’s
`FolderListingService.aspx/GetFolderListing2` request shape. They are spaced at least
1.5 seconds apart; export-status polls also wait three seconds. Folder paging checks
identity, total consistency, duplicate IDs and premature empty pages. Unknown collection
or entry shapes fail visibly. A failed walk retains old queue metadata; `--resume`
cannot clear an incomplete-discovery failure without a successful new walk.

Native PDFs use `ElectronicFile.aspx?docid=...&dbid=0`. Scanned documents and shortcuts
use the public UI’s single-entry `StartExport` → `CheckExportStatus` → `GetExportJob`
sequence, with a 30-minute per-export timeout. Single scanned exports were observed to
return a PDF directly. A one-PDF ZIP is also accepted, with its CRC checked. An exploratory
export of the 2018 folder returned only its direct PDF, omitting nested folders; the
capture therefore never relies on folder exports for completeness.

A file must begin with `%PDF-` before being stored. Bytes are written atomically under
`<DATA_DIR>/agencies/trca_history/<sha256>.pdf` before setting `sha256` and `local_path`
on the `background_pdf` row. Queue identity is its stable source URL; `reference` holds
the discovered path and `kind='trca_history'` keeps these records outside the extraction
corpora. No text or model extraction runs. Held documents are never re-fetched because
text is absent. A failed document stays queued while later documents continue.

`inventory.json` preserves entry IDs, names, paths, native extensions, shortcuts,
non-PDF exclusions and discovery completeness. `capture.json` records final held/pending
counts and failures. Pending bytes or any failure makes the command exit nonzero,
including deliberately bounded samples. The command is separate from nightly and does
not publish data.

## Verification and preservation run

The full offline suite passes: **981 tests**, including 28 history-capture tests.
Three unchanged API `data` payloads from the public series, 1957 and 2018 folders are
committed as fixtures with provenance. Random response padding and session cookies are
not committed.

The corrected CLI captured these scans in an isolated database; the implemented HTTP
client also captured the native 2017 PDF. Every hash was recomputed and `pdfinfo` read
each document successfully:

| Source entry | Record | Pages | Bytes | SHA-256 |
| --- | --- | ---: | ---: | --- |
| 212737 | 1957 Index | 25 | 788,485 | ddb9c0e68f8049744443cca0ee8848c4c8b9ce2827c992c52cd5f35b7f311351 |
| 212736 | 1957 Members List | 6 | 592,961 | 0a48551f0c57f7ad8e3d020e85d2a76a8789a6dda5bc5e702175d8ad1019a780 |
| 212735 | 1957 Minutes | 411 | 23,408,650 | a57737aceb489ca1fa037c55ffe69e748403ff21151676dd305df0682fa1eced |
| 1452956 | 2017 Authority | 723 | 50,420,828 | 958e2ab8965f7b2e913cd04be1ff0d5a787704257a92da4a71298c6236b74682 |

These four verified PDFs (75,210,924 bytes) were added to the production archive.
All 10,862 pre-existing `background_pdf` rows were compared verbatim and preserved.
The SQLite backup and sample verification record are under
`/home/alex/tb-data/operations/224/` (`bids.before-capture.sqlite`, `verified-sample.json`).

A full production walk/capture started at **2026-10-20 21:00 UTC** as the user service
`tb-224-trca-history.service`, using frozen tested package code under that operations
directory. It ran independently of subsequent branch checkouts. The completed result and
remaining source-access gaps are recorded below; the earlier snapshots are not claims
that every historical candidate was held.

```sh
systemctl --user status tb-224-trca-history.service --no-pager
tail -n 20 /home/alex/tb-data/operations/224/capture.log
cat /home/alex/tb-data/agencies/trca_history/capture.json
```

While running, held/pending counts can be read directly from `background_pdf` for
`kind='trca_history'`. The final manifest is written at completion. Retry unresolved
bytes with `TB_DATA_DIR=/home/alex/tb-data tb capture-trca-history --resume`; repeat the
full command if discovery is incomplete. Run one capture process at a time for this data
directory. No automatic retry or publication is scheduled by this change.

## Follow-up audit — 2026-10-20 22:23 UTC

The capture remained active. A read-only audit independently recomputed every held file's
SHA-256, checked its expected storage path and PDF header, and required `pdfinfo` to report
at least one page. All **365 held PDFs** passed: **1,830,057,278 bytes / 28,160 pages**.
The inventory and persistent queue agreed on all 831 candidates, with complete discovery.
All 10,862 pre-existing document rows still matched the pre-capture backup verbatim.
466 documents were pending at that audit's snapshot; this is not a completed-capture result.
The report is `/home/alex/tb-data/operations/224/progress-verification.json`.

Every policy shortcut was separately checked through the ordinary anonymous public
Download action. IDs **1546658, 1546659, 1546662, 1546663, 1546664, 1546666** all returned
`Access denied. [9013]`. These cover Administration Regulation, Board Terms of Reference,
Indemnification, Purchasing, Rules of Conduct, and Use of Resources During an Election.
The evidence is `shortcut-access-check.json` in the same operations directory. The six
records remain queued; their inaccessible targets have not been preserved.

`tb-224-trca-history-verify.service` now waits for capture termination and runs the same
whole-archive audit automatically. It also compares the final capture manifest against
the audited queue and held files. It writes `final-verification.json` and
`verification.log` under the operations directory and exits nonzero for missing or
invalid bytes. Inspect those results before considering the preservation complete;
no extra prompt is needed to trigger this audit.


## Final outcome — 2026-10-21 00:44 UTC

Capture finished at 00:44:05 UTC; the automatic audit finished at 00:44:27 UTC.
**All 825 regular PDF/scanned candidates were held and independently verified:**
7,856,996,897 bytes (7.86 GB), 96,154 pages. The audit found no hash, PDF-structure,
source-coverage or final-manifest discrepancies. All 10,862 pre-existing document rows
still matched the backup when that audit ran, before the later nightly refreshed records.

The remaining six queue entries are exactly the policy shortcuts listed above, each with
an explicit public-export access denial. Both capture and verification therefore exited
nonzero as designed; this is not a claim that all 831 candidates were preserved. The Word
attachment remains an explicit non-PDF exclusion. No source-access restriction was bypassed.

Final evidence is in `/home/alex/tb-data/agencies/trca_history/capture.json` and
`/home/alex/tb-data/operations/224/final-verification.json`. The code and verified regular
PDF preservation are ready for review; keep the six source-access gaps visible when
tracking issue completion. Neither extraction nor publication was run by this command.
