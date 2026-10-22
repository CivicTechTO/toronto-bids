# Agency board-report fixtures (#135)

## TRCA public PDF recovery fixtures (#259)

`trca_20812.pdf` and `trca_20636.pdf` are complete public PDF responses captured
anonymously in ordinary headed Chromium on 2026-10-21 from
`https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20812` and
`https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20636`.
The first contains the June 16, 2026 Partners in Project Green Executive Management
Committee minutes. The second is the calendar-published April 14, 2026 minutes;
its PDF omits the meeting date, so identity was cross-checked against the dated HTML
minutes and matching PPG 7/26 resolution. The reviewed source links, hashes and
identity evidence live in `toronto_bids/data/trca_document_gaps.json`.
These fixtures exercise actual byte/structure validation, stored text, exact-hash
replacement acceptance and resumability. Viewer/verification HTML is tested separately.

## Agency identity recovery fixtures (#258)

`identity_recovery_2026-10-21.json` retains ten complete, unchanged cached extraction
results read offline on 2026-10-21. Source URLs and PDF SHA-256 values identify the
original public records. The `recoveries` indices are zero-based positions in the
complete contract arrays, including empty/unrelated contracts.

Source passages and identifier roles are recorded in
`toronto_bids/data/agency_identity_evidence.json`. Each quoted passage was checked
against a fresh `pdftotext -layout` rendering of the held PDF after verifying its
SHA-256. Twelve distinct PDFs support the ten relationships: compound solicitation /
contract identifiers, Ontario-framework / TRCA-adoption identifiers, dated meeting-item
links between minutes and staff reports, and the source-printed `10042722-01` suffix.
The last is a correction of our earlier truncation, not a general parent/lot equivalence.

DocumentId=19889's cached result assigns a shared $1,035,000 VOR ceiling to each
supplier. Its bidder facts are supported; those individual award amounts are withheld.
DocumentId=19982 independently supports the vendor memberships with NULL amounts.
Original extracted claims remain unchanged in the fixture/cache and inspectable ledger.

## Agency identity refusal cache fixtures (#257)

`identity_refusals_2026-10-21.json` contains three complete, unchanged held extraction
results read offline on 2026-10-21. Each records the published source URL, source PDF
SHA-256, original extraction version and result digest. These are cached model outputs,
not claims that every extracted fact has been independently verified against the PDF.
Expected refusal positions are zero-based indices in the complete contract array.

TRCA DocumentId=4831 mixes valid contracts with an unknown-reference contract;
DocumentId=19635 carries distinct solicitation and contract numbers. Zoo backgroundfile-76755
has a reference-less multi-contract result, including one empty contract. The fixtures
exercise retention and refusal reporting without paid extraction or new source requests.

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

## trca_escribe_meeting_assets.html — REAL, TRIMMED (#175)

Verbatim fragments of a real meeting detail page,
`Meeting.aspx?Id=61119747-5c38-4080-a3f8-0d45952a03b7&Agenda=Agenda&lang=English`
(Board of Directors), fetched 2026-07-28; the live page is 131,700 bytes and only the
surrounding markup was cut away. This is the fixture the synthetic
`trca_escribe_2023.html` above could not be: it carries the two shapes that broke the
extractor, exactly as eSCRIBE emits them —
`<link rel='stylesheet' type='text/css' href='/FileStream.ashx?DocumentId=3253'>` and
`<DIV class='AgendaHeaderLogo' ><img  src='filestream.ashx?DocumentId=3251' ...>`. Both
were fetched live the same day: 3253 returns `text/css`, 3251 returns `image/jpeg` (the
TRCA logo, the same 22,692 bytes on all 230 meeting pages). It also carries a document
anchor whose `href` is NOT the first attribute (`<a class='Link' tabindex='15' href=...>`),
which is the ordinary shape on these pages.

## bids_tenders_record_sample.json — SYNTHETIC (#135)

Hand-built, NOT a real capture. As of 2026-07-18 both permitted bids&tenders portals (TRCA,
Zoo) are empty (total=0, all statuses), so no real listing record exists to record. This
fixture matches the field names documented in the portal's grid JS
(Module/Tenders/Resources/scriptsV2/home/index.js: Id, Title, ClosingDate, Documents,
Addendums, PlanTakers) and exercises parse_listing's mapping mechanics only. `parse_listing`
is PROVISIONAL until `tb enrich-agencies --portal --record` captures a real record and replaces
this fixture (#135 deferred item).

## Exhibition Place board-report fixtures (#130)

Real EP Board of Governors reports, captured live 2026-07-18 from legdocs (plain HTTP;
discovered via the TMMIS EP committee, `YYYY.EP<n>.<n>`). EP's board handles far more than
procurement, so most reports are NOT awards — the negatives below are load-bearing (they carry
`$` amounts that must NOT be read as awards).

| fixture | shape | source |
|---|---|---|
| ep_award_with_table_2023.txt | AWARD + Table 1 bid prices (Powell Fence, M.J.K., Clearway); RFT No. EP110-2023 | /legdocs/mmis/2023/ep/bgrd/backgroundfile-240943.pdf |
| ep_confidential_decision_2025.txt | confidential award, winner named (Coca-Cola Canada Bottling Ltd), value withheld | /legdocs/mmis/2025/ex/bgrd/backgroundfile-258727.pdf |
| ep_confidential_agreement.txt | confidential agreement, counterparty redacted ("a Consumer Show Client") → winner None | 2022-2026 EP term, backgroundfile-230177 |
| ep_non_award_wsib_report.txt | NEGATIVE: WSIB lost-time-injury safety report; carries `$` (WSIB costs) but no award → must return None | backgroundfile-230165 |
| ep_non_award_procurement_status.txt | NEGATIVE: procurement status update ("currently in procurement"), no award yet → must return None | backgroundfile-230442 |
| ep_non_award_collective_agreement.txt | NEGATIVE: SYNTHETIC refusal regression test for non-procurement guard; collective-agreement/labour-relations report wrongly kept without guard (the live run found 23 such reports) | test fixture (#130) |

EP award clause shape: "award of Contract No. <c> (RFT No. EP###-YYYY) to WINNER for the
<project> in the amount of $AMOUNT" — text sits between WINNER and the amount phrase, so the
winner regex is EP-specific (stops at " for "/amount), not the shared Zoo "to WINNER <phrase> $".

### EP award fixtures — 2019 (year variety, #130)

Live-captured 2026-07-18 (EP 2018-2022 term). Table-awards are rare (4 of 151 reports scanned),
and 2019 EP reports differ from the 2023 seed — the parser must handle both:

| fixture | why it matters |
|---|---|
| ep_award_2019_sole_tender.txt (bgrd-131331, 2019.EP2) | winner spans a line break ("Westbury National / Show System Ltd."); `Contract No. 19-085-98518` ref (no RFT-EP); ONE bidder; award $969,415.00 ≠ the sole bid $1,139,944.00 (distinct columns) |
| ep_award_2019_multi_bidder.txt (bgrd-137241, 2019.EP6) | winner carries a location qualifier ("Sutherland-Schultz Ltd. **of Cambridge, Ontario**") — strip it; 5 bidders; a bid price footnote `*` (revised-price marker) |

Corpus gotchas the parser must handle: (1) winners span pdftotext line breaks → the winner class
must allow internal whitespace, bounded, stopping at for/at/,/in-the-amount; (2) strip a trailing
" of <City>, <Province>" location qualifier so the same firm keys consistently; (3) `native_ref`
is `RFT No. EP###-YYYY` (2023) OR `Contract No. <token>` (2019); (4) both years use "in the amount
of $X"; (5) a bid price may carry a trailing `*` footnote.

## bids_tenders_trca_2026-10-20.json — REAL (#226)

Four unmodified records selected from the public TRCA grid search on 2026-10-20. Request:
`POST https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Search/950af760-3894-40ef-86fd-ef17e2f5104a`
with the landing page's cookie/token pair, empty `status` (All), `limit=50`, `start` offsets,
blank `from`/`to`, `dir=DESC`, and `sort=DateClosing DESC,Id`. No login or documents fetched.
The full rate-limited sweep yielded 249 unique listing IDs against total=249. Tokens/cookies
are not fixture content; tests add `buyer_slug` locally.

Public Bid Number fields on these detail pages independently confirmed the title-prefix keys:

| Bid number | Public detail URL |
| --- | --- |
| 10044383 | https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/5082161c-f80a-4cd8-aaac-8388a237e99e |
| 10042501 | https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/d58d0f1a-696d-4ee2-8f01-ac13ba93b07b |
| 10022400_33 | https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/6de5fbf3-f9d6-4698-a27d-f0b882e51469 |
| 10044944-1 | https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/3c6879d8-b712-42cb-ba23-cd1ea1ea8ad0 |

The synthetic sample remains a compatibility input. It does not establish Zoo coverage.

### TRCA public Laserfiche history (#224)

`trca_laserfiche_{series,1957,2018}.json` were fetched anonymously over HTTP on
2026-10-20 from TRCA's own WebLink installation at
`https://laserfiche.trca.ca/WebLink/FolderListingService.aspx/GetFolderListing2`.
The requests used `repoName=LASERFICHE`, `folderId=1030915`, `212734`, and `1477012`,
respectively, `getNewListing=true`, `start=0`, `end=100`, `sortColumn=Name`,
`sortAscending=true`. Ordinary anonymous session cookies were accepted; no login.
Payload `data` is unchanged; random response padding (`__breach`) is omitted.

The series lists 1957–2022 annual minute-book folders. The 1957 folder contains scanned
index/member/minutes documents with no native extension. The 2018 folder contains nested
folders and a native PDF. Client entry-type values are Document=-2, Shortcut=-1,
Folder=0, RecordSeries=1, as declared by the public `app/dist/browse/main.js`.

### Toronto Zoo public listings — off-peak 2026-10-21 (#226)

`bids_tenders_zoo_2026-10-21.json` contains all 122 recorded public listings from the
permitted 05:10 America/Toronto capture (09:10 UTC), sorted by page ID. Each object is an
unchanged automatically recorded response row, including the locally attached `buyer_slug`
and `status_filter`. No session cookies, antiforgery tokens or document content are included.
The HTTP sweep used the UI's All filter with offsets 0, 50 and 100 and completed at 122/122.
It initially failed only after fetching, while mapping published references for storage.

Every Zoo title has the shape `published identifier - description`. Nine records demonstrate
that the identifier cannot be restricted to RFQ/RFP plus a simple integer: they include
`127`, `13`, `108`, `Q32202208`, `NRFP-08`, dotted identifiers and spaced numeric segments.
The numeric published prefix is retained; a different RFQ number is not inferred from the
description. Whitespace is normalized under the existing agency reference contract.

The exact dated controls are page `6fc2f901-6847-469e-b630-d09fc5e75cb9` (RFQ-50,
Awarded, closing 2022-11-04 16:00 UTC) and `ae4980d4-38f2-44cb-b5c4-0fe9ccc634b2`
(RFP-03, Terminated, closing 2026-02-06 17:00 UTC). Their raw display dates independently
agree with the converted millisecond timestamps. An older RFP 03 listing is a separate
2023 project, not the 2026 control.

The 122 page IDs reduce to 114 normalized reference strings. Eight references are reused
for distinct published listings/projects across years. This fixture preserves those separate
page IDs and must not be read as proof that those procurements can safely be coalesced into
one solicitation row. Wider solicitation identity work is tracked under #124.


## TRCA complete portal identity replay (#124)

`bids_tenders_trca_identity_2026-10-22.json` retains 249 distinct TRCA portal page
observations from the production raw recordings inspected on 2026-10-22. Each selected
field is copied unchanged: Id, Title, optional ReferenceNumber/BidNumber, status, opening
and closing dates, and buyer_slug. Descriptions and unrelated grid counters are omitted.
Source: `https://trca.bidsandtenders.ca/Module/Tenders/en`, the permitted anonymous All
listing sweep captured before this migration. No new portal requests were made for the
identity replay. Seven normalized references are reused across distinct page IDs. Together
with the complete Zoo fixture (122 pages), this tests 371 source identities without treating
any shared reference, title or date as equivalence evidence.
