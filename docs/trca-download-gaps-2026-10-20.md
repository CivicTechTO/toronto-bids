# TRCA download gaps and public minutes recovery (#244)

Investigation date: 2026-10-20. The three original URLs remain unavailable. They are
published minutes links, and ordinary Chromium returns 404 for all three. Their original
bytes cannot be recovered from those URLs. Two corresponding public minutes packages have
been recovered under their own calendar-published URLs. No intentional restriction or
permanent removal was established for any of the original files.

## Trace and browser comparison

The production index had all three URLs with `sha256 IS NULL`, no local path and no council
reference. Neighboring report dates narrowed the calendar search; live agenda HTML then
established the actual link context:

| Original DocumentId | Published agenda and item | Intended record | Outcome |
| --- | --- | --- | --- |
| [5558](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=5558) | [Board, 2020-04-24](https://pub-trca.escribemeetings.com/Meeting.aspx?Id=c7ce8330-5fa2-4997-89ce-50ce5fbeed44&Agenda=Agenda&lang=English), item 9 | Special Executive Committee minutes, 2020-02-21; link explicitly labelled Open Session Meeting Minutes | 404 in ordinary Chromium; no verified public replacement |
| [7234](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=7234) | [Board, 2021-04-30](https://pub-trca.escribemeetings.com/Meeting.aspx?Id=18346524-dedb-45ff-b76e-952f37ea05bc&Agenda=Agenda&lang=English), item 2 | Board minutes, 2021-03-26 | 404 in ordinary Chromium; calendar publishes package 7294 for that meeting |
| [6819](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=6819) | [Board, 2021-02-26](https://pub-trca.escribemeetings.com/Meeting.aspx?Id=45f88df9-507a-4dc7-84b4-d62248c88c60&Agenda=Agenda&lang=English), item 6 | Board minutes, 2021-01-29 | 404 in ordinary Chromium; calendar publishes package 6980 for that meeting |

A repeated `HttpClient(retries=0).get_bytes(url)` probe produced changing 403/404 responses.
The 403 bodies identify themselves as eSCRIBE browser-verification pages. Plain HTTP is
therefore not enough to distinguish a missing document from a browser-verification response.
Direct navigation in normal headed Chromium, followed by clicking the exact links from
their agenda pages, establishes the browser outcome; no authentication or challenge bypass
was used. The browser also provides positive controls: the alternative public package links
load successfully.

The agenda notes say that closed-session minutes are circulated separately. That is evidence
about the separate closed records, not proof that these public links should be restricted.
The original Special Executive Committee calendar entry is labelled Closed Session and
publishes no document links. It provides no supported replacement for 5558. The separately
held February 21 Board package 5559 is a different meeting and cannot substitute for it.
Source-side publication, removal and storage causes behind the 404s remain unverified.

## Recovery

The public calendar page-method is
`POST https://pub-trca.escribemeetings.com/MeetingsCalendarView.aspx/GetCalendarMeetings`
with `calendarStartDate`/`calendarEndDate` covering 2021. Its `MeetingDocumentLink` entries
publish these Minutes with Attachments packages:

| Package | Original meeting | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| [6980](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=6980) | [Board, 2021-01-29](https://pub-trca.escribemeetings.com/Meeting.aspx?Id=42fd48ae-4c8a-460c-a45a-97f21e692c9d&Agenda=Agenda&lang=English) | 23,318,081 | `dd0bf043625a54fac1005fb9f0d3ef9a758db3ed45e38cabb79a1ed14017ed76` |
| [7294](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=7294) | [Board, 2021-03-26](https://pub-trca.escribemeetings.com/Meeting.aspx?Id=464b42f1-71c8-4c67-be0a-67e6f415eb65&Agenda=Agenda&lang=English) | 47,330,718 | `9879370ff7f25b6685c7005026a76afaf9185987787fd6531fc9f4f044872d53` |

Both returned browser HTTP 200 and actual `%PDF` bytes. `pdftotext -layout` confirms that each
begins with the corresponding Board meeting's minutes. Plain HTTP hit browser verification
for both, so recovery saved the normal same-origin browser PDF responses. Chrome's viewer
HTML was rejected rather than mistaken for a PDF.

Both packages are now archived in `/home/alex/tb-data/agencies/trca/<sha256>.pdf` and indexed
in the production `background_pdf` table as `agency_board`, with text and hashes. The store
was backed up first to `/tmp/tb-244/bids.before-recovery.sqlite`. Byte hashes and stored text
were checked after archiving, and all 10,860 prior report rows retained their ids, URLs,
hashes, local paths, text and first-seen timestamps.

These are separate packages, not asserted byte-for-byte replacements for the unavailable
files. All three original URLs retain their unheld rows and remain retryable. No paid model
extraction, backfill, export or publication ran during recovery.

## Operational correction

The reporting bug was reproducible offline: the real agency capture function returned an
empty failure list after the downloader caught a 403/404/503 or non-PDF response. Successful
return of the download count made the agency step appear complete.

The TRCA downloader now accepts the existing shared failure list. Document fetch errors,
non-PDF replies, calendar failures and meeting-page failures append `download:trca` entries.
Remaining documents and cached extraction still run. `enrich-agencies` exits nonzero; the
nightly records the agencies step as failed in `sync_run`, includes the affected URLs in the
summary, and continues to export. No status-code allowlist treats a gap as confidential,
permanently removed or successful.

Validation: **931 offline tests passed**, including nine new cases. They exercise HTTP,
transport, non-PDF and discovery failures; successful and previously held files; extraction
continuation; standalone exit status; and the real downloader through nightly summary,
history and export. A final live run through the updated pending-PDF loop reported all three
original URLs as failures and left all three queued.

## Remaining coverage limit

The existing discovery walks agenda HTML. It does not index every calendar-published PDF
package. Across the fetched 2019–2026 calendars, 418 of 631 distinct calendar PDF URLs had no
matching production URL before this recovery (comparison ignores URL case). These packages
can overlap already held individual reports; this is not a count of unique missing records.
The two verified packages above are the bounded recovery for this issue. A general calendar
package capture would be a separate expansion and could add substantial bytes and extraction
work. The unavailable original 5558 link and the cause of all three source 404s remain open
coverage limitations, now visible as failures rather than a complete successful capture.
