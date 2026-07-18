# Agency board-report fixtures (#135)

pdftotext -layout output of real board award reports, captured 2026-07-18.

| fixture | source URL |
|---|---|
| trca_armour_stone_2023.txt | https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=14809 |
| trca_vor_appraisal_2021.txt | https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=7921 |
| zoo_energy_retrofit_2019.txt | https://www.toronto.ca/legdocs/mmis/2019/zb/bgrd/backgroundfile-124313.pdf |
| zoo_red_panda_2025.txt | https://www.toronto.ca/legdocs/mmis/2025/zb/bgrd/backgroundfile-257571.pdf |
| zoo_perimeter_fence_2025.txt | https://www.toronto.ca/legdocs/mmis/2025/zb/bgrd/backgroundfile-260182.pdf |

The two 2025 Zoo reports route financials to a CONFIDENTIAL ATTACHMENT — they are the
value_confidential=1 cases. The 2019 Zoo report names its winner publicly. The TRCA
armour-stone report tabulates all four bidders with opening results; the VOR report is
the vendor-of-record shape (multiple winners, no per-bid prices).

## TRCA parser precision/recall fixtures (#138)

Real reports, captured live 2026-07-18 from `pub-trca.escribemeetings.com` (FileStream), each
chosen because it broke the first-cut `_AWARD`/`_REFS` regexes — the two originals happened to
be the clean case, so the offline suite was green while ~69% of the real corpus mis-parsed.

| fixture | why it matters |
|---|---|
| trca_rfq_spelled_out_2019.txt (DocumentId=…4474) | ref labelled "Request for Quotation No." spelled out — the abbreviation-only matcher dropped it |
| trca_contract_label_2019.txt (…3250) | ref labelled `Contract #10008808` (a `#`, not "No.") |
| trca_multiline_winner_2021.txt (…4837) | winner name spans a pdftotext line break ("W.F. Baird & Associates / Coastal Engineers Ltd.") |
| trca_overcapture_2021.txt (…4831) | the run-on trigger: the winner clause uses "at a total **annual** cost", so the old `(.+?)` skipped it and captured 268,757 chars to a distant plain "cost" phrase |

## trca_getcalendarmeetings_2023q1.json — REAL (#137)

Live capture (2026-07-18) of the eSCRIBE calendar page-method that actually drives
discovery: `POST https://pub-trca.escribemeetings.com/MeetingsCalendarView.aspx/GetCalendarMeetings`
with body `{"calendarStartDate":"2023-01-01","calendarEndDate":"2023-03-31"}` → `{"d":[…]}`,
five agenda'd meetings for Q1 2023, each with `ID` (GUID), `MeetingName`, `StartDate`,
`HasAgenda`. `meeting_detail_urls` turns these into `Meeting.aspx?Id=<guid>` URLs, whose
detail pages carry the real `FileStream.ashx?DocumentId=N` report links (verified live:
22 on the 2023-02-17 Board of Directors meeting). This is the fix for #137 — the reason
the old static year-page walk found zero.

## trca_escribe_2023.html — SYNTHETIC (#135), a detail-page stand-in

Not a real capture. `curl -sL -A "Mozilla/5.0" "https://pub-trca.escribemeetings.com/?FillWidth=1&Year=2023"`
was run live on 2026-07-18 and returned a real 286KB page, but its shape doesn't match a
static-HTML walker: the page is a JS-rendered FullCalendar widget, and its two literal
"Meeting.aspx" occurrences are inside a JS template string assembled at click time, not
`<a href="Meeting.aspx?...">` anchors — so `escribe_document_urls`'s regexes correctly
find nothing on the real *year* page. (Discovery no longer walks the year page at all —
it POSTs GetCalendarMeetings, above.) `escribe_document_urls` still runs on the meeting
*detail* pages, which ARE server-rendered with `FileStream.ashx` anchors; this
hand-written fixture exercises that extractor on the anchor shapes it must find.

## bids_tenders_record_sample.json — SYNTHETIC (#135)

Hand-built, NOT a real capture. As of 2026-07-18 both permitted bids&tenders portals (TRCA,
Zoo) are empty (total=0, all statuses), so no real listing record exists to record. This
fixture matches the field names documented in the portal's grid JS
(Module/Tenders/Resources/scriptsV2/home/index.js: Id, Title, ClosingDate, Documents,
Addendums, PlanTakers) and exercises parse_listing's mapping mechanics only. `parse_listing`
is PROVISIONAL until `tb enrich-agencies --portal --record` captures a real record and replaces
this fixture (#135 deferred item).
