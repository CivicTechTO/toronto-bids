# Independent TTC sources for historical procurement results

Research date: 2026-10-24. Scope: TTC's own public reports and meeting records, without obtaining gated MERX content. This is a workable supplementary source, not evidence of complete historical coverage.

## A concrete recovery: Wilson Carhouse North Expansion, C1-38

The [27 March 2013 TTC staff report](https://cdn.ttc.ca/-/media/Project/TTC/DevProto/Documents/Home/Public-Meetings/Board/2013/March-27/Reports/Procurement_Authoriz.pdf?hash=336EDF47FFB9E73695D735CA9A1DDAF9&rev=0d82a1f0bfff4780b9d04fbbd6b802d8) independently publishes the C1-38 procurement history and seven bid prices. Appendix B (PDF page 4) contains:

| Bidder | Bid price, including all taxes |
|---|---:|
| Buttcon Limited | $37,700,190.00 |
| Elite Construction Inc. | $38,081,000.00 |
| Aplus General Contractors Corp. | $38,938,670.00 |
| Aquicon Construction Co. Ltd. | $39,404,230.00 |
| Pomerleau Inc. | $39,747,750.00 |
| WCC Construction Canada ULC. | $39,911,293.77 |
| METTKO Construction Inc. | $41,492,249.65 |

The report records 58 companies obtaining documents, seven timely bids, and four additional late bids returned without consideration. The first three bids were considered acceptable. Its recommendation is an award to Buttcon at $37,700,190 inclusive of taxes. The [meeting page and minutes, item 4(a)](https://www.ttc.ca/All-public-meetings/board/2013/board-meetings-mar-27) independently record approval of that award and link the report.

**Identity and date boundaries:** the report gives contract **C1-38**, but does not expose **T11PT12870** in its indexed text. Link to MERX T11PT12870 / index 879704954 only if retained public listing evidence itself exposes the shared exact C1-38 identifier; otherwise retain an unlinked candidate. The TTC report says documents were posted 7 November 2012, whereas the parent investigation observed a MERX listing publication date of 8 November. Preserve both source-specific dates. The meeting page's narrative and heading identify 27 March, while one metadata field says 26 March; do not silently reconcile the discrepancy.

## Additional independent historical examples

- [21 November 2012: Wilson Yard Modifications, Site Services Stage 1, A18-15](https://cdn.ttc.ca/-/media/Project/TTC/DevProto/Documents/Home/Public-Meetings/Board/2012/November-21/Reports/TYSSE_Wilson_Yard_Mo.pdf?hash=E508A19A8710D56F51EBF34DA633185F&rev=dd1764d988c5424681873cd4ed52cb16). Appendix A (PDF page 3) lists Bondfield $25,808,070; Aplus $41,437,100; Buttcon $42,489,702; Dufferin $44,365,000, including all taxes. It also lists an **engineer's estimate of $41 million, which is not a bidder**. The report recommends Bondfield, records 33 document recipients and four bids, and says the notice was posted on TTC and MERX on 27 July 2012.
- [12 July 2017: Glencairn Skylight Replacement, A11-5](https://pw.ttc.ca/-/media/Project/TTC/DevProto/Documents/Home/Public-Meetings/Board/2017/July-12/7_Procurement_Authorization_Glencairn_Skylight_Replacement_C.pdf). Appendix B (PDF page 6) lists Bondfield $10,703,360; Maystar $13,673,000; Matheson $14,454,898.38, including all taxes. It records 13 document recipients, three bids and a recommendation to award to Bondfield. Notice posted 4 April 2017; closing 31 May 2017. No MERX identifier bridge is asserted here.
- [24 June 2014: C1-38 amendment](https://cdn.ttc.ca/-/media/Project/TTC/DevProto/Documents/Home/Public-Meetings/Board/2014/June-24/Reports/Procurement_Authorization_Amendment_Wilson_Carhouse_North_Ex.pdf?hash=07A5BA1F7A03E0A9299AAB54AD32FF4E&rev=3e215b4bc2b443ee8a6c80d27841a03c) is separately published. Treat amendments as changes to an existing contract, not additional original awards.

These report facts were checked in the web tool's extracted primary PDF text; the archive landing was additionally fetched directly as HTTP 200 and retained locally.

## Feasible systematic enumeration

The official [TTC Board archive](https://www.ttc.ca/All-public-meetings) covers **2000–2024**, with meeting type/year/month filters. It states that Board and Board committee materials moved to [TMMIS](https://secure.toronto.ca/council/) from 20 January 2025, and older material remains on the TTC site until further notice. The landing's initial text contains an empty-results placeholder; this is not proof that no meetings exist.

A direct, identified HTTP GET of the archive landing returned 200. Raw source and response metadata are retained at:

- `.validation-merx/ttc-board-archive.html`
- `.validation-merx/ttc-board-archive-meta.json`

The published page source discloses Sitecore SXA UI search configuration:

```json
{
  "endpoint": "https://www.ttc.ca//sxa/search/results/",
  "v": "{F9A088B4-AFC4-4EE7-8649-0ACA83AB2783}",
  "s": "{44F1ECF1-8A36-4F2D-9F87-06D501C3DE06}",
  "l": "",
  "p": 20,
  "defaultSortOrder": "ContentDateFacet,Descending",
  "sig": "",
  "itemid": "{1450DB42-0543-4C73-B159-421DF22D9460}",
  "autoFireSearch": true
}
```

Published facet endpoint: `https://www.ttc.ca//sxa/search/facets/`; facet names `MeetingType`, `ContentYearFacet`, `ContentMonthFacet`. **Only discovery of these public UI settings is verified here; a successful results request, filter serialization and complete pagination have not been tested.** This gives a concrete next reconnaissance step rather than requiring guessed meeting slugs: reproduce the UI's public search calls, enumerate meeting pages by year, then follow agenda/report links. Do not infer that these endpoints constitute a documented bulk procurement API.

On each meeting page, inspect procurement authorization, contract-award notice and amendment items; follow the actual report hyperlinks rather than guessing generic PDF filenames (many different meetings use the same filename). Deduplicate by source URL plus content hash, keep meeting/report/appendix provenance, and preserve staff recommendation separately from the decision recorded in minutes. The March 2013 meeting demonstrates this workflow end to end for one record. No complete monthly contract-award table series was verified during this bounded research.

## Coverage limits and procurement semantics

The [TTC Procurement Policy](https://www.ttc.ca/transparency-and-accountability/policies/Materials-and-Procurement-Policies/procurement-policy), sections 4.5–4.6 and 8, distinguishes formal/public requests from informal purchases, tender price disclosure from RFP name disclosure, and award publication from bid opening. It describes formal public requests above $100,000, subject to exceptions; this is **not** a verified Board-report publication threshold. It refers authorization levels to a separate expenditure policy. Do not assign a historical Board threshold from the current public-advertising threshold.

Inference: Board reports can recover a valuable subset of historical bids and awards, but cannot establish all-purchase completeness. Smaller/delegated purchases, unsuccessful/cancelled procurements, restricted information and RFP prices may not appear in the report corpus. Governance and disclosure rules can also change over time. No quantified historical report-publication threshold was established in this investigation.

The TTC [Selling to the TTC guidance](https://www.ttc.ca/doing-business-with-the-ttc/Procurement-and-Category-Management/about-ttcs-Procurement-and-Category-Management-department/selling-to-the-ttc) describes publishing tender responses, normally within one day, and distinguishes RFP disclosure. That generic statement does not establish an operational anonymous historical export. No verified TTC procurement bulk export/API or complete old tender-response archive was found in this bounded investigation. Toronto's City procurement datasets should not be presumed to cover TTC purchases.

## Recommended practical workaround

Combine ordinary publicly enumerated MERX listing metadata (parent investigation) with independent TTC Board report extraction. Retain exact contract identifiers and join only on source-supported identity. The C1-38 example recovers seven bidders and the approved award without opening the gated MERX detail. Report coverage explicitly as a subset until enumeration and reconciliation prove otherwise. An official structured data export could improve remaining gaps, but no request to TTC or another person has been sent.
