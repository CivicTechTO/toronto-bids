# Systematic City bid-report discovery — 2026-10-31

The strongest demonstrated route is a **hybrid of complete committee/Council agenda capture and targeted public full-text search**. It removes the current dependency on a recorded vote and a ten-digit document number in an agenda title. A normal anonymous headed-browser session is demonstrated; plain stateless HTTP did not work on the tested TMMIS pages or app endpoints. PDF downloads and meeting-schedule metadata remain ordinary HTTP.

This is discovery evidence, not a recovery-count forecast. No new procurement identities, bid rows, paid model requests or production changes were made.

## What changed from the earlier ceiling

The existing `sources/committee_awards.py` selects voting-record CSV rows whose title contains a ten-digit document marker. That subset cannot discover consent items absent from voting records or reports identified only by historical Call Number. The City instead offers complete meeting agendas and a public search spanning titles, summaries, recommendations, decisions, and staff-report attachments. The [official TMMIS help](https://www.toronto.ca/legdocs/tmmis/help/help1.htm) documents the meeting/committee navigation and basic/advanced search concepts. The [current official search UI](https://secure.toronto.ca/council/#/advancedSearch) supplied the actual live search controls and requests tested below.

## Complete agendas: measured breadth with one browser navigation

| Tested complete agenda | Item headings | Unique `backgroundfile` PDF URLs | Demonstrated result |
| --- | ---: | ---: | --- |
| [2015.CC6](https://secure.toronto.ca/council/report.do?meeting=2015.CC6&type=agenda) | 204 | 406 | Historical call-level report discovery without ten-digit document titles |
| [2024.GG14](https://secure.toronto.ca/council/report.do?meeting=2024.GG14&type=agenda) | 27 | 37 | Modern competitive awards, amendments, noncompetitive items and supplementary reports in one page |

Both returned HTTP 200 in the ordinary headed-browser flow. A separate [2024.GG15 special meeting](https://secure.toronto.ca/council/report.do?meeting=2024.GG15&type=agenda) correctly contained only a by-law introduction; an agenda's small count alone is not a fetch failure. Its actual meeting date is July 24, 2024, whereas GG14 is July 2. This also shows why a guessed date/PDF filename is insufficient.

The 2015.CC6 page scopes report links under item headings. A prototype walked each `h3` item's following siblings until the next `h3`, skipping comment nodes, and retained distinct source URLs:

- **2015.PW3.4:** the page explicitly lists all nine Call Numbers `6032-14-0189` through `6032-14-0197`. It links [backgroundfile-78231.pdf](https://www.toronto.ca/legdocs/mmis/2015/pw/bgrd/backgroundfile-78231.pdf).
- **2015.PW3.5:** all five Call Numbers `6032-15-0025` through `6032-15-0029` appear. It links [backgroundfile-78247.pdf](https://www.toronto.ca/legdocs/mmis/2015/pw/bgrd/backgroundfile-78247.pdf).
- **2015.PW3.1:** Call `0401-14-3100` links [backgroundfile-78224.pdf](https://www.toronto.ca/legdocs/mmis/2015/pw/bgrd/backgroundfile-78224.pdf) and [backgroundfile-78819.pdf](https://www.toronto.ca/legdocs/mmis/2015/pw/bgrd/backgroundfile-78819.pdf).
- **2015.PW3.7:** Call `6035-15-3000` links [backgroundfile-78222.pdf](https://www.toronto.ca/legdocs/mmis/2015/pw/bgrd/backgroundfile-78222.pdf).

These counts describe identifiers and links actually present; the prototype did not expand a numeric range into invented calls. A single PDF can contain several calls and must still be extracted and joined per contract. A full meeting link inventory is not approval to attach every PDF or every bidder to every call.

The GG14 page similarly scopes **GG14.8** to the held ferry process report [247094](https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-247094.pdf) and additional reports [247344](https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-247344.pdf) and [247345](https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-247345.pdf). This is a demonstrated way to find supplemental evidence missed by a single-report lookup. The linked extra PDFs were not adjudicated here as bidder lists.

## Enumerating meetings without the voting CSV

The [official meeting-schedule dataset](https://open.toronto.ca/dataset/3bfab808-105b-45ab-8d92-cf1916594653/) exposes current and prior-term resources through [CKAN package metadata](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=3bfab808-105b-45ab-8d92-cf1916594653). Its [current 2022–2026 CSV](https://ckan0.cf.opendata.inter.prod-toronto.ca/datastore/dump/08c8aedb-afba-41f5-830e-bbfb305ebbc7) and [readme](https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/3bfab808-105b-45ab-8d92-cf1916594653/resource/29e59900-368f-4c7f-a5e7-60b9458671c4/download/city-council-and-committees-meeting-schedule-reports-readme.txt) returned 200 over ordinary HTTP.

The CSV provides term, committee name, meeting number, date, scheduled times and location. It is an enumeration seed, not an agenda or report list. Current measured rows:

| Body | Dated CSV rows | Distinct meeting numbers |
| --- | ---: | ---: |
| City Council | 78 | 43 |
| Executive Committee | 33 | 33 |
| General Government Committee | 31 | 31 |
| Infrastructure and Environment Committee | 31 | 31 |

Thus this four-body 2022–2026 slice has **138 distinct body/meeting-number combinations**, rather than 173 dated rows. Multi-day Council meetings must be deduplicated, canceled/unpublished meetings retained as explicit states, and committee code/year/reference assumptions verified against fetched source dates. These are counts of scheduled entries, not a claim that 138 complete agendas were fetched or that those four bodies cover every procurement.

The same package advertises 2018–2022, 2014–2018, 2010–2014 and 2006–2010 resources. Only the 2022–2026 CSV was downloaded for this bounded test. No historical calendar walk was run.

## Full-text search: normal UI requests and measured results

The current app bundle loaded by the official UI names a search table at `/multiple/agenda-items.json`, with UI page-size choices 10/50/100. Actual browser requests went to **POST `https://secure.toronto.ca/council/api/multiple/agenda-items.json`**. This is an observed first-party web-app implementation, not a documented/versioned API contract or promise of permanent availability.

A sanitized actual request for the ferry search was:

```json
{
  "includeTitle": true,
  "includeSummary": true,
  "includeRecommendations": true,
  "includeDecisions": true,
  "decisionBodyId": null,
  "meetingFromDate": null,
  "meetingToDate": null,
  "word": "4053424337",
  "termId": 8,
  "includeAttachments": true
}
```

Term `8` was obtained by selecting **2022–2026 in the live UI**, not assumed as a permanent identifier. Historical probes selected **2014–2018** in that same UI. Each search used the real form and its ordinary browser session. No account, alternate endpoint permission, CSRF bypass, token replay or persisted cookie/header data was used.

| Query / scope | Returned records | Distinct relevant item evidence |
| --- | ---: | --- |
| `4053424337`, title only, 2022–2026 | 2 | GG14.8, once as committee and once as Council consideration |
| `4053424337`, all five fields including attachments | 7 | GG14.8 plus later ferry-related items and an Order Paper record |
| `6032-14-0189`, title only, 2014–2018 | 2 | PW3.4 committee + Council wrappers |
| `6032-14-0189`, all five fields including attachments | 3 | PW3.4 wrappers plus an Order Paper record |
| `6032-14-0193`, all five fields including attachments | 2 | PW3.4 wrappers, despite this middle identifier not appearing literally in the range title |

Every observed search response reported `TotalPages=1`; pagination beyond the first page **was not tested**. A production adapter must reconcile `TotalRecordCount`, `PageSize`, `PageNumber`, `TotalPages`, distinct IDs, repeat pages and changing populations; the visible page-size options are not a completeness guarantee.

Responses expose `reference`, `agendaItemId`, `councilAgendaItemId`, `meetingId`, `decisionBodyId`, consideration status, title, summary/recommendations, and `backgroundAttachmentId`. They do **not include direct PDF URLs in the observed Records schema**. The results' ordinary links resolve to `agenda-item.do?item=<reference>`; item/full-agenda resolution is still needed to retrieve actual report links.

A search hit can be an incidental historical reference, later maintenance procurement, Order Paper index, or repeated consideration of one item. The seven ferry records are not seven solicitations or seven bidder lists. Deduplicate committee/Council wrappers for discovery, retain their consideration provenance, and verify the actual document/call and bid-table context before storing facts.

## Browser requirement: what is and is not established

Plain `httpx` requests to `report.do?meeting=2024.GG15&type=agenda`, `agenda-item.do?item=2024.GG15.1`, `/council/api/home.json` and `/council/api/csrf.json` each returned **403**. The corresponding app and its public JSON requests worked in an anonymous headed Chromium session. Existing code uses the same headed-browser approach for TMMIS.

Accordingly, a **browser lane is presently proven necessary for the tested anonymous access path**, while stateless direct-HTTP access is unproven. This does not prove every possible official API is gated, nor that a new standalone headless strategy cannot work. No supported public replacement API documentation was found. Reuse one normal browser context for many paced requests/UI actions; do not launch one browser per item. Keep session state ephemeral and raw public page/results separate from auth/session metadata.

One guessed static agenda PDF URL returned 404 and supplied no evidence of an alternate bulk-PDF route. Do not build filename guessing around that failure.

## Recommended systematic pilot

1. Use official schedule metadata to seed complete agendas for relevant bodies/terms, cache each agenda once, and extract item-scoped source links and printed modern/historical identifiers. Reconcile meeting dates and maintain an explicit coverage ledger.
2. Deduplicate report bytes by SHA-256 and avoid refetching Council copies of committee reports. Do not send every meeting attachment to a model: screen captured agenda items, reuse held text/cache, then use the established extraction contract for procurement reports.
3. Use targeted all-fields search for uncovered document numbers or source-proven historical Call Numbers that the agenda index has not resolved. Cache search-result identities and inspect source links, rather than approving matches from search text.
4. Validate a stratified pilot across modern committee awards, historical multi-call awards and amendments/noncompetitive counterexamples. Measure **newly covered procurements**, not only names, pages or reports; report false-association refusals and explicit count-only/no-name evidence separately.

This replaces the recorded-vote/title bottleneck. It does not promise recovery of all 4,105 missing solicitations or prove that staff-delegated/early-era bidder lists are publicly available.

## Reproducible local evidence

All bounded scripts and captured public responses are under `.validation-systematic-bids-2026-10-31/discovery/`:

- `probe_http.py` and metadata `.body` files: stateless statuses and official schedule schema.
- `browser.py`, `browser2.py`, `browser3.py`; `cc6.html`, `gg14.html`; corresponding item inventories: full-agenda mechanism and source URLs.
- `search_browser.py`, `search-module.js`, `search-network.json`: public form and app-route observation.
- `search_once.py`, `search-request.json`, `search-result.json`: exact sanitized first query and response.
- `search_compare.py`, `ferry-title-*`, `winter-title-*`, `winter-all-*`, `winter-middle-all-*`: comparable scoped probes.

These files contain public payloads and criteria only; browser cookies, request headers and CSRF values were not persisted. The scripts are research probes, not production adapters. The report records only successful measured mechanisms and explicit failed probes; there was no broad crawl or model extraction.
