# TRCA public document recovery (#259)

Investigation: October 21–22, 2026. Production recovery: October 22, 2026.

The bounded pass recovered **3 original PDFs**, confirmed **8 separately held public
alternatives**, and left **11 original URLs queued**. Three originals have no verified
alternative. Both the isolated rehearsal and production command exited **1**, correctly
reporting incomplete original coverage. None of the observed 404 responses establishes
why the source cannot serve the file.

## Outcomes for all fourteen original gaps

Each original is linked from the public agenda recorded in the reviewed registry.
The dated [machine-readable report](trca-document-recovery-2026-10-22.json) retains
full agenda context, alternative provenance, actual response status/URL/type/length,
response-body SHA-256, HTML title and validated PDF SHA-256/page counts.

| Original DocumentId | Browser response | Held outcome |
| --- | --- | --- |
| [5558](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=5558) | 404 | No verified alternative; original queued |
| [7234](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=7234) | 404 | Separate [7294](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=7294) public minutes/package; original queued |
| [6819](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=6819) | 404 | Separate [6980](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=6980) public minutes/package; original queued |
| [6818](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=6818) | 404 | No verified alternative; original queued |
| [6817](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=6817) | 404 | Separate [6884](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=6884) public minutes/package; original queued |
| [9229](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=9229) | 404 | Separate [9224](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=9224) public minutes/package; original queued |
| [13785](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=13785) | 404 | Separate [18834](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=18834) public minutes/package; original queued |
| [15601](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=15601) | 404 | Separate [16792](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=16792) public minutes/package; original queued |
| [20197](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20197) | 404 | Separate [20636](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20636) public minutes/package; original queued |
| [20289](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20289) | 404 | Separate [20515](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20515) public minutes/package; original queued |
| [20288](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20288) | 404 | No verified alternative; original queued |
| [20812](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20812) | 200 | Original PDF; 5 pages, 93,294 bytes |
| [21024](https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=21024) | 200 | Original PDF; 4 pages, 212,489 bytes |
| [21025](https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=21025) | 200 | Original PDF; 3 pages, 223,162 bytes |

## Verification of public alternatives

Original agenda items identify the intended meeting or report. The publisher’s calendar
`GetCalendarMeetings` response then identifies the corresponding public minutes PDF.
The PDF text was checked for meeting identity, not merely a reference to the minutes.
Each accepted alternative has a reviewed SHA-256 and its own source URL. These packages
are not asserted byte-identical to the unavailable originals.

Four alternatives were already held (7294, 6980, 6884 and 20515); their indexed rows and
bytes were preserved. Four were newly archived (9224, 18834, 16792 and 20636).

PDF 20636 omits its meeting date. The calendar explicitly publishes it for the
April 14, 2026 PPG meeting; the dated HTML minutes for the same meeting ID match its
PPG 7/26 resolution approving the January 20 minutes. The registry records this
limitation and both formats’ source provenance rather than inventing a PDF date.

Three gaps remain without a verified public alternative:

- **5558:** open-session Special Executive Committee minutes, February 21, 2020.
  The calendar publishes no document for that meeting. The April 24 Board package
  contains a receipt resolution; the February 21 Board package is a different meeting.
- **6818:** Outdoor Education Task Force minutes, January 25, 2021. The public agenda
  labels these “Meeting #5/20”; no matching full minutes/calendar entry was verified.
  A Board receipt resolution cannot stand in for the task-force minutes.
- **20288:** May 22, 2026 Board item 10.5.1, a verbal update on Bill 97 negotiations
  referring to RES.#B29/26. Its negotiation-related context does not establish that
  this FileStream URL is intentionally restricted. No public alternative was verified.

## Reproducible capture and review

```sh
TB_DATA_DIR=/path/to/data tb recover-trca-documents
# A bounded subset, for example:
TB_DATA_DIR=/path/to/data tb recover-trca-documents --only 20812
```

The command uses one ordinary headed anonymous Chromium session, loading the public
calendar before fetching same-origin document bytes at one-second intervals. It uses
no account, stealth modification or verification bypass. Browser PDF viewer HTML is
not the document response; `%PDF-` bytes, `pdfinfo` structure/page checks and SHA-256
validation determine acceptance. Responses are bounded to 64 MiB and a 60-second fetch.
Only reviewed TRCA FileStream URLs are accepted; redirects outside the requested
document are rejected. HTML/error responses retain diagnostic metadata, not full pages.

`scrapers/toronto_bids/data/trca_document_gaps.json` is the reviewed scope. To add or
change an alternative, verify the original agenda’s intended record, the publisher’s
meeting/calendar link, and the actual PDF’s identity; record its hash and evidence.
A changed PDF hash requires review before acceptance. No numeric-ID guessing is used.

Held files are hash-checked and reused; a corrupt held file is reported without
overwriting its index. Newly recovered originals fill only SHA-NULL rows. Alternatives
remain separate rows; they never mark an original held. Reports are written atomically
to `agencies/trca/document-recovery.json`, with immutable dated run content under
`agencies/trca/recovery_runs/`. Committed document captures are resumable after interruption.
Every unresolved original produces an explicit `download:trca` failure and a nonzero exit.
The normal standalone/nightly download queue and failure propagation remain in place.

## Production preservation and validation

Before capture, production was backed up to
`/home/alex/tb-data/operations/259/bids.before-recovery.sqlite`. That directory retains
a frozen package, code-hash manifest, investigation evidence, command log and verification.
The full browser rehearsal used `/home/alex/tb-data/operations/259-rehearsal/`.

- Production `background_pdf` rows: **11,696 → 11,700**. Three original rows gained
  validated bytes; four new alternative rows were added. All 11,693 other existing
  rows remained identical. Original row IDs, URLs and first-seen timestamps were retained.
- All eleven held originals/alternatives have verified local file hashes. Every other
  database table remained identical to the backup; SQLite integrity check returned `ok`.
- **1,083 offline tests passed**, including real-PDF success, replacement hash mismatch,
  viewer/verification/truncated PDF rejection, wrong-document/login/external redirects,
  transport failure continuation, held corruption, repeat-run preservation, standalone
  incomplete status, the actual pending queue and existing nightly failure propagation.
- A live bounded pass through the normal pending downloader on the isolated database
  returned **11 explicit `download:trca` failures and zero downloads**, preserving
  the remaining queue separately from browser/alternative capture.

Capture invoked no paid extraction, agency rebuild, export or publication. The next
normal export can include the updated local index. General calendar-package expansion
and the six pre-2019 Laserfiche policy shortcuts remain separate work (#224).
