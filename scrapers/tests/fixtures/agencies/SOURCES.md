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
