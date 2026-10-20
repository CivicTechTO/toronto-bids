# TRCA historical PDF preservation (#224)

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
directory. It remains independent of subsequent branch checkouts. This is an ongoing
preservation run, not evidence that all historical bytes have been held. Inspect its
actual completion and resolve/report remaining gaps before closing #224.

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
