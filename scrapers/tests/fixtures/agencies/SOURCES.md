# Agency fixtures

Provenance and notes for real and synthetic agency test fixtures (#130, #135, #137, #138, #140, #175, #224, #226, #257, #258, #259).

## TRCA PDF recovery (#259)

Captured anonymously in headed Chromium on 2026-10-21:

- `trca_20812.pdf`: `https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20812` (June 16, 2026 Partners in Project Green Executive Management Committee minutes).
- `trca_20636.pdf`: `https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20636` (April 14, 2026 minutes; the PDF omits the date, confirmed against HTML minutes and resolution PPG 7/26).

Reviewed links, hashes, and evidence live in `toronto_bids/data/trca_document_gaps.json`.

## Agency identity recovery and refusal caches (#257, #258)

- **`identity_recovery_2026-10-21.json` (#258):** 10 cached extraction results read offline on 2026-10-21 (`recoveries` are 0-based indices into the full contract arrays). Supporting passages live in `toronto_bids/data/agency_identity_evidence.json`, checked against `pdftotext -layout` of 12 verified PDFs (compound solicitation/contract IDs, Ontario-framework/TRCA adoption IDs, dated meeting-item links, and the printed `10042722-01` suffix). In DocumentId=19889, a shared $1,035,000 VOR ceiling is withheld from individual awards while keeping bids; DocumentId=19982 supports vendor memberships with `NULL` amounts.
- **`identity_refusals_2026-10-21.json` (#257):** 3 cached extraction results read offline on 2026-10-21 (cached model outputs, not independently verified facts; 0-based refusal indices):
  - TRCA DocumentId=4831: mixes valid contracts with an unknown-reference contract.
  - TRCA DocumentId=19635: carries distinct solicitation and contract numbers.
  - Zoo `backgroundfile-76755`: multi-contract result without references, including one empty contract.
- **`ttc_award_decisions_2026-11-22.json` (#341):** 16 real held `ttc_board` staff reports and paired `ttc_archive_decision` PDFs from the local 2014–2024 TTC archive covering standard single-award (`N1-24`), metadata-reviewed multi-award (`G85-404`), 3-contract single report (`S45-11`, `S45-12`, `D45-14`), short-form supplier (`E85-24`), `report_url_fallback`, Board-amended withholding (`P32PL17757`, 440→325 buses), amount-omitted withholding (`M60-88`), and `Notice of Award` received-for-information (`W45-11`).

## Board award reports (`pdftotext -layout`, 2026-07-18)

| Fixture | Source URL | Notes |
|---|---|---|
| `trca_armour_stone_2023.txt` | `https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=14809` | Four bidders with opening prices |
| `trca_vor_appraisal_2021.txt` | `https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=7921` | Vendor of Record (multiple winners, no per-bid prices) |
| `zoo_energy_retrofit_2019.txt` | `https://www.toronto.ca/legdocs/mmis/2019/zb/bgrd/backgroundfile-124313.pdf` | Winner named publicly |
| `zoo_red_panda_2025.txt` | `https://www.toronto.ca/legdocs/mmis/2025/zb/bgrd/backgroundfile-257571.pdf` | Financials in confidential attachment (`value_confidential = 1`) |
| `zoo_perimeter_fence_2025.txt` | `https://www.toronto.ca/legdocs/mmis/2025/zb/bgrd/backgroundfile-260182.pdf` | Financials in confidential attachment (`value_confidential = 1`) |

## Legacy TRCA parser edge-case fixtures (#138)

Real TRCA FileStream reports captured 2026-07-18 that exposed bugs in early regexes:

| Fixture | DocumentId | Edge case |
|---|---|---|
| `trca_rfq_spelled_out_2019.txt` | 4474 | Reference spelled out as "Request for Quotation No." |
| `trca_contract_label_2019.txt` | 3250 | Reference uses `Contract #10008808` (`#` instead of `No.`) |
| `trca_multiline_winner_2021.txt` | 4837 | Winner spans a line break ("W.F. Baird & Associates / Coastal Engineers Ltd.") |
| `trca_overcapture_2021.txt` | 4831 | "at a total annual cost" caused greedy capture across 268k chars |

## TRCA eSCRIBE HTML and calendar fixtures (#135, #137, #175)

- **`trca_getcalendarmeetings_2023q1.json` (real, #137):** Captured 2026-07-18 from `POST https://pub-trca.escribemeetings.com/MeetingsCalendarView.aspx/GetCalendarMeetings` (`2023-01-01` to `2023-03-31`), returning 5 meetings with `ID`, `MeetingName`, `StartDate`, and `HasAgenda`. Used to build `Meeting.aspx?Id=<guid>` URLs.
- **`trca_escribe_2023.html` (synthetic, #135):** Hand-written stand-in for server-rendered `Meeting.aspx` detail pages to test `escribe_document_urls` anchor extraction. (Year pages themselves are JS FullCalendar widgets, which is why discovery uses `GetCalendarMeetings`.)
- **`trca_escribe_meeting_assets.html` (real, trimmed, #175):** Excerpts from `Meeting.aspx?Id=61119747-5c38-4080-a3f8-0d45952a03b7&Agenda=Agenda&lang=English` (2026-07-28). Includes eSCRIBE stylesheet (`DocumentId=3253`, CSS) and logo (`DocumentId=3251`, JPEG) tags that must be excluded, plus a real document `<a>` link where `href` is not the first attribute.

## TRCA Laserfiche history (#224)

`trca_laserfiche_{series,1957,2018}.json`: captured anonymously on 2026-10-20 from `https://laserfiche.trca.ca/WebLink/FolderListingService.aspx/GetFolderListing2` (`repoName=LASERFICHE`, `folderId` 1030915 / 212734 / 1477012, random `__breach` padding omitted). Covers the 1957-2022 annual minute-book series, scanned 1957 files with no extension, and nested 2018 folders with a native PDF. Entry types from `app/dist/browse/main.js`: Document `-2`, Shortcut `-1`, Folder `0`, RecordSeries `1`.

## Exhibition Place board reports (#130)

Real reports captured 2026-07-18 from legdocs via TMMIS `EP` committees (plus one synthetic collective-agreement refusal case):

| Fixture | Type / edge case | Source |
|---|---|---|
| `ep_award_with_table_2023.txt` | Award + Table 1 bid prices (`RFT No. EP110-2023`) | `/legdocs/mmis/2023/ep/bgrd/backgroundfile-240943.pdf` |
| `ep_confidential_decision_2025.txt` | Confidential award (winner named, value withheld) | `/legdocs/mmis/2025/ex/bgrd/backgroundfile-258727.pdf` |
| `ep_confidential_agreement.txt` | Counterparty redacted ("a Consumer Show Client") -> winner `None` | `backgroundfile-230177` (2022-2026 EP) |
| `ep_non_award_wsib_report.txt` | Non-award: WSIB safety report with `$` amounts -> `None` | `backgroundfile-230165` |
| `ep_non_award_procurement_status.txt` | Non-award: status update ("currently in procurement") -> `None` | `backgroundfile-230442` |
| `ep_non_award_collective_agreement.txt` | Non-award (synthetic): collective-agreement refusal regression | Test fixture (#130) |
| `ep_award_2019_sole_tender.txt` | 2019: multiline winner, `Contract No. 19-085-98518`, sole bid ($1,139,944) != award ($969,415) | `bgrd-131331` (`2019.EP2`) |
| `ep_award_2019_multi_bidder.txt` | 2019: location suffix ("of Cambridge, Ontario"), 5 bidders, trailing `*` price footnote | `bgrd-137241` (`2019.EP6`) |

## bids&tenders portal fixtures (#124, #140, #226)

- **`bids_tenders_record_sample.json` (real, #140):** Unmodified TRCA grid record (`5082161c-f80a-4cd8-aaac-8388a237e99e`, bid `10044383`) from `https://trca.bidsandtenders.ca/Module/Tenders/en` (2026-10-22).
- **`bids_tenders_legacy_synthetic.json` (synthetic, #135):** Retained only to test backwards-compatible field aliases (`ReferenceNumber`, `ClosingDate`, `DatePosted`, `StatusText`).
- **`bids_tenders_trca_2026-10-20.json` (real, #226):** 4 records from the 249-record TRCA grid sweep (`status=""`, `limit=50`, `sort=DateClosing DESC,Id`; tokens/cookies stripped). Detail pages confirmed title-prefix bid numbers:
  - `10044383`: `https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/5082161c-f80a-4cd8-aaac-8388a237e99e`
  - `10042501`: `https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/d58d0f1a-696d-4ee2-8f01-ac13ba93b07b`
  - `10022400_33`: `https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/6de5fbf3-f9d6-4698-a27d-f0b882e51469`
  - `10044944-1`: `https://trca.bidsandtenders.ca/Module/Tenders/en/Tender/Detail/3c6879d8-b712-42cb-ba23-cd1ea1ea8ad0`
- **`bids_tenders_zoo_2026-10-21.json` (real, #226):** All 122 public Toronto Zoo listings captured off-peak (05:10 America/Toronto, 09:10 UTC), sorted by page ID (no cookies/tokens). Covers varied title prefixes (`127`, `13`, `108`, `Q32202208`, `NRFP-08`, dotted and spaced forms) and 8 references reused across years (114 unique normalized references across 122 pages). Positive controls: `6fc2f901-6847-469e-b630-d09fc5e75cb9` (RFQ-50, Awarded, 2022-11-04 16:00 UTC) and `ae4980d4-38f2-44cb-b5c4-0fe9ccc634b2` (RFP-03, Terminated, 2026-02-06 17:00 UTC; distinct from a 2023 RFP 03 page).
- **`bids_tenders_trca_identity_2026-10-22.json` (real, #124):** All 249 TRCA portal page records (selected identity/status/date fields copied verbatim, 7 reused references). Combined with the 122 Zoo pages, exercises 371 portal page identities.
