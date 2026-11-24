# Pre-2019 TRCA Minute Books extraction pilot

Extract bounded, source-reviewed agenda items from the pre-2019 TRCA Minute Books. The live evaluation found 61 of 61 expected bidder–tender relationships at the item level. It also showed why publication needs separate checks for lot boundaries, prices, and decision authority. Start with the items that have printed procurement references and their bid or decision pages available. Keep the full PDFs in `trca_history` and limit batch inputs to source-reviewed cuts. The evaluation ran without an agency backfill. [Extraction issue #316](https://github.com/CivicTechTO/toronto-bids/issues/316) · [Preservation closeout #224](https://github.com/CivicTechTO/toronto-bids/issues/224#issuecomment-6369716569) · [Active corpus and publication rules](../../scrapers/toronto_bids/extraction.py)

## Archive census

The November 23, 2026 local run read `background_pdf` where `kind = 'trca_history'` and opened every held PDF using Poppler 26.01.0. The 825 records point to 822 distinct SHA-256 hashes and account for 96,154 pages and 7,856,996,897 bytes when counted per record. All 825 database text fields were null. The six access-denied shortcuts and the separately preserved Word original are outside the PDF census. [Preservation closeout](https://github.com/CivicTechTO/toronto-bids/issues/224#issuecomment-6369716569) · [Archive capture code](../../scrapers/toronto_bids/sources/trca_history.py)

A page counted as raster-backed when `pdfimages -list` reported a single raster covering at least 80% of the page on each axis, using its pixel dimensions, resolution, and the page dimensions from `pdfinfo`. The remaining pages include native/vector content and may include smaller or tiled images. A page counted as having usable text when `pdftotext -q -layout` returned at least ten alphabetic words of three or more ASCII letters. This threshold identifies text for screening. It does not measure transcription accuracy.

| PDF makeup | PDFs | Pages | PDFs with usable text on every page | PDFs with usable text on some pages | PDFs with no usable text |
| --- | ---: | ---: | ---: | ---: | ---: |
| Raster-backed on every page | 707 | 86,610 | 506 | 201 | 0 |
| Mix of raster-backed and other pages | 41 | 6,760 | 4 | 37 | 0 |
| No page-sized raster detected | 77 | 2,784 | 61 | 14 | 2 |
| Total | 825 | 96,154 | 571 | 252 | 2 |

In all, 93,420 pages had usable embedded text, including 84,701 of 87,117 raster-backed pages. The two zero-text records are identical copies of a one-page direct deposit form. There are 2,734 pages below the text threshold, of which 2,416 are raster-backed. Most historical scans already have a searchable layer and can be screened locally before considering fresh OCR.

## Procurement screen and source review

The [candidate inventory](issue-316-trca-procurement-candidates.tsv) lists the **240 PDFs and 2,434 one-based PDF pages** that matched the screen. Each row has a Laserfiche document ID, archive reference, and matched page numbers. The two review columns identify pages adjudicated in the pilot, where a relevant page contains a specific tender opening, quotation, award recommendation, or decision. A dash indicates an empty review column. Matches alone warrant review. For example, results can include an index entry, purchasing policy, retrospective audit, or planned award date.

The run normalized whitespace within each extracted page and used this case-insensitive expression. It kept the original PDF page boundaries.

```regex
\b(?:award(?:ing|ed)? (?:of (?:the )?)?(?:contracts?|tenders?)|(?:contracts?|tenders?) (?:be |were |was )?(?:awarded|award)|tenders? (?:were |was )?(?:called|received|opened)|bids? (?:were |was )?(?:received|opened)|tender (?:results|summary)|request for (?:proposals|quotations|tenders)|procurement results)\b
```

- **Screening precision was 35 of 42 pages (83%)**, representing 30 confirmed PDFs. The sample drew six random matched pages from each decade from the 1950s through the 2010s. Positive counts by decade were 4, 4, 5, 5, 6, 6, and 5. The samples were allocated equally by decade to check the earlier minute books as well as the larger later cohorts.
- **Local OCR returned zero additional phrase matches on 35 low-text pages** from 30 PDFs. The sample drew five per decade from PDFs with a mix of usable and low-text pages. Poppler rendered the pages in grayscale at 150 dpi and Tesseract 5.5.0 ran in English with automatic page segmentation (`--psm 3`). Seventeen pages reached the same text threshold after OCR. Sampled output included maps, divider pages, correspondence, and some poorly recognized tables. Recognition limits and the sample size leave open the possibility of missed items elsewhere.
- **A separate spot check of 14 cue-negative pages** with usable embedded text found no tender or award decisions. It drew two pages per decade and included an index page with a purchasing topic. Nearby pages can contain essential evidence without a cue. For instance, the stone bid table and carried motion in the 1986 Executive Committee minutes continue onto page 258 from a match on page 257. Neither spot check establishes corpus-wide recall. [1986 meeting minutes](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=202224&dbid=0#page=257)

For reproducibility, the pilot sorted records by archive reference and URL, ordered pages numerically, and grouped by the decade of the reference's first four digits. It used Python `random.Random(316)` to sample matches and then low-text pages within each decade, iterating decades in ascending order. The cue-negative spot check used seed 317.

### Agenda item cuts

These cuts were manually reviewed in the extracted text to estimate input size and assess provenance. PDF page numbers are one-based. Where a page carries more than one item, the cut uses the item heading and the next item or end of its report as boundaries. The 1957, 1961, and 1977 cuts include a small amount of meeting-date context elsewhere in their package.

| Archive year | Source item | PDF pages for the item | Words in cut |
| --- | --- | --- | ---: |
| 1957 | Goodwood Scheme contracts, with two awards listed together | [38](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=212735&dbid=0#page=38) | 81 |
| 1961 | Woodbridge soils investigation, with a tender list and carried motion | [140](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=212897&dbid=0#page=140) | 124 |
| 1977 | Kortright Centre paving, with bids and acceptance | [98](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=212535&dbid=0#page=98) | 357 |
| 1986 | Bluffers Waterfront Park stone supply, with bid table and split award | [257–258](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=202224&dbid=0#page=257) | 221 |
| 1996 | Don Valley Brick Works hydro service, carried resolution E58/96 | [182–183](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=211822&dbid=0#page=182) | 677 |
| 2002 | 2003 paper supplies, carried resolution B195/02 with a second supplier on the continuation page | [872–873](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=213027&dbid=0#page=872) | 303 |
| 2015 | Staff clothing re-tender, carried resolution A167/15 | [411–412](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1328270&dbid=0#page=411) | 703 |
| 2017 | Fishleigh Drive erosion control, three separately priced contracts in A110/17 | [239–242](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1452956&dbid=0#page=239) | 1,432 |

The Kortright result also appears in Authority minutes, where the supplier is legible as D. Crupi & Sons and the amount is again $70,500. The Executive Committee's carried motion has a garbled supplier rendition in the embedded text. Conversely, the 2015 index points to printed Authority page A407, which is PDF page 411. The Authority awarded contract 10000813 to Dufferin Apparel after re-tendering. The similarly titled 2014 Executive Committee recommendation concerns contract 10000128 and Needham Promotions. These are examples of why both a repeated item and a new tender need adjudication before a row is created. [1977 Authority minutes](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=212533&dbid=0#page=201) · [2015 index](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1273339&dbid=0#page=27) · [2015 carried resolution](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1328270&dbid=0#page=411) · [2014 recommendation](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1327362&dbid=0#page=536)

## Estimated cost before the live run

| Workload | Estimated input | Sonnet 5.5 Batch estimate |
| --- | ---: | ---: |
| Eight agenda item cuts, including the current structured prompt and schema per request | 18,324 tokens | $0.048–$0.098 with 750–2,000 output tokens per item, including reasoning |
| The eight parent PDFs as layout-normalized text | 2.70 million tokens | $2.70 for input alone |
| All locally extractable archive text, approximately 180 million characters after whitespace collapse | 45.1 million tokens | $45.11 for text input alone, before prompt/schema, extra OCR, and output |

The estimates use one token per four characters, with the serialized request body for the first two rows. They use the verified Sonnet 5.5 Message Batch rate of USD $1 per million input tokens and $5 per million output tokens and assume no cache savings. The existing budget would reserve $1.44 for the eight cuts together because it budgets against the request size bound and the 32,768-token output ceiling. The shared default budget is $20 per UTC month. Direct PDF submission would also bill visual tokens for every page. At an illustrative average of 1,000 visual tokens per page, those alone would cost $96.15 across the corpus. [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing#batch-processing-pricing) · [Anthropic PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support) · [Local request, prices, and reservation](../../scrapers/toronto_bids/claude.py)

The earlier 0.313 figure for TRCA was supplier-name agreement against the incumbent parser across shared eSCRIBE documents in a GPT-5.6-Luna comparison. It had 449 matched names and 984 extra names, and the report does not isolate whole-meeting packages. Independent eSCRIBE review found legitimate bids absent from incumbent labels. The 83% from the local screen measures relevant-page selection. [Original validation report](https://github.com/CivicTechTO/toronto-bids/blob/3c152f1b7d8cd50bd68aaaf2201c3eb05e86c465/docs/extraction-validation/report.json#L100-L124) · [TRCA ground-truth adjudication](../ground-truth/trca-bid-labels/README.md)

## Live extraction precision

Claude Sonnet 5.5 at high effort processed the eight agenda item cuts and five controls through the existing structured Messages client. The controls were an index, a purchasing policy, a progress update with approval pending, a carried committee recommendation, and a tender-opening report with two bid tables and no decision. An independent reviewer labeled the 13 frozen cuts from the source pages before seeing model output. Reconciliation matched suppliers to the distinct tender item and role, accepting documented name variants such as legal suffixes and scanned spelling variants. The test measures accuracy given a preselected cut. Archive-wide recall remains unmeasured. In the evaluation file, each source range is a one-based PDF page number followed by a zero-based start and exclusive end within the nonempty lines of its layout text. The per-cut hashes identify the frozen inputs. [Source ranges, labels, model observations, and usage](issue-316-trca-extraction-evaluation.json)

| Check | Precision | Recall |
| --- | ---: | ---: |
| Submitted bidder linked to tender item | 61/62 (98.4%) | 61/61 (100%) |
| Submitted bidder linked to the specific lot when the source separates lots | 54/62 (87.1%) | 54/67 (80.6%) |
| Award recipient supported by a decision in the supplied cut | 11/14 (78.6%) | 11/11 (100%) |
| Award recipient candidate supported by an award or recommendation in the supplied cut | 14/14 (100%) | 14/14 (100%) |
| Explicit no-bid or untimely response | 13/13 (100%) | 13/14 (92.9%) |

All three controls without extractable contracts produced empty outputs. The false submitted-bid relationship was a supplier that did not submit its quote by the deadline. The model combined two stone lots into one tender record, preserving seven supplier names while losing the 13 lot-level relationships. The three award claims unsupported by a decision in their cuts were recommendations. The original records confirm all three recipients elsewhere. For the 1957 Goodwood items, the enclosing motion to adopt the committee report is on PDF pages 35 and 40. For the 2014 clothing contract, the subsequent Authority award is on page 536 of a separate package. Those cases show which decision pages a publishable cut needs. [1957 Authority adoption](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=212735&dbid=0#page=35) · [2014 Authority award](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1327312&dbid=0#page=536) · [1986 stone lots](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=202224&dbid=0#page=258)

The current numeric parsers recovered **41 of 50** comparable submitted bid prices and **4 of 9** priced awards supported by the supplied cuts. The other values in those denominators returned null under the existing strict parsers, mostly from spaces and garbled currency symbols in scan text. The comparison uses single total or annual prices and excludes the composite stone rates and the 2003 paper unit price. The latter can parse as a number and still be mistaken for a total. The Goodwood scan reads $6,455, represented in embedded text and model output as `66455. 00`. The 1996 hydro award expressly includes GST, which the model labeled as including HST. The model also supplied a committee resolution number as if it were a tender reference for the 2003 paper item. [Source and output comparison](issue-316-trca-extraction-evaluation.json) · [Numeric amount parsers](../../scrapers/toronto_bids/amount.py)

All 13 requests completed with valid structured output and settled at **USD $0.137493** through the shared guard. The provider reported 51,800 input tokens, including cache reads and writes, and 9,780 output tokens. The pilot used the synchronous client so the measurement could run immediately. The production procurement database was read-only throughout.

## Bounded extraction scope

The 2015 clothing re-tender and the 2017 Fishleigh Drive report are the strongest initial candidates from the pilot. Together they have four printed contract references, 26 correctly linked bid observations, and four carried awards. All their compared bid and award prices passed the existing numeric parsers. A read-only check found no exact reference overlap for the six printed procurement references from the pilot in the current TRCA solicitation, bid, and award tables. That check covers exact references. A backfill still needs to verify cross-source identity and retain the original PDF hash and pages for each observation. [2015 Authority minutes](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1328270&dbid=0#page=411) · [2017 Authority minutes](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1452956&dbid=0#page=239)

For items with reliable tender references, use locally split reports and attach the original decision pages before admitting any award. Review apparent lots, no-bid responses, price basis, and scan damage against the PDF. Keep historical records under separate source scoping and verify that existing eSCRIBE canonical IDs survive a dry run before publication. The older, reference-less and multi-lot cases should remain source evidence until their identities and amounts are reviewed. [Archive source kind](../../scrapers/toronto_bids/sources/trca_history.py) · [TRCA extraction and source scoping](../../scrapers/toronto_bids/extraction.py) · [Publication stage](../document-extraction.md)

## Import and verification

The bounded import uses the four reviewed references below. The importer verifies the two archived PDFs against their reviewed SHA-256 hashes. It writes only these agenda items and their links under the `trca_history` fact source. It uses the standard agency procurement keys and checks for reference collisions before inserting. A repeat run verifies the exact stored facts and returns the same IDs.

```bash
cd scrapers
uv run python -m toronto_bids.sources.trca_history_pilot --database /path/to/bids.sqlite
```

For the November 23, 2026 live local run, the importer first ran against a SQLite copy. The live run used the pipeline lock and a verified SQLite backup. Comparison against the backup found the four new canonical procurements and PDF links with no changes to preexisting agency rows or canonical keys. The export projection included all 34 new facts.

| Reference | Procurement ID in the live local run | Bid rows | Award date | Award amount |
| --- | ---: | ---: | --- | ---: |
| 10000813 | 24927 | 4 | 2015-09-25 | $296,000.00 |
| 10005031 | 24928 | 7 | 2017-06-23 | $120,650.00 |
| 10005032 | 24929 | 7 | 2017-06-23 | $246,729.00 |
| 10005033 | 24930 | 8 | 2017-06-23 | $74,284.00 |

The clothing figures are annual bid estimates and a two-year award estimate. The other bid prices are total contract values and the awards are ceilings. All are before HST. The Authority records contingencies separately from those figures. The Fishleigh bid tables abbreviate CDR Young Aggregates Inc. as CDR Young Aggregates. The stored raw names preserve that difference, so the supplier dimension keeps distinct keys for the two spellings. The source review ties them to the same carried outcome on each of the two references. [2015 award](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1328270&dbid=0#page=411) · [2017 awards and bids](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1452956&dbid=0#page=239)

## Follow-up import

The November 24, 2026 follow-up added two procurements, 11 bids, and one award. The importer now verifies previously loaded references and adds missing reviewed references in the same transaction. The earlier four procurements retained their IDs. Both the dry run and live import preserved all existing agency rows and canonical keys.

| Reference | Procurement ID | Submitted bids | Award evidence |
| --- | ---: | ---: | --- |
| 10000128 | 24931 | 5 | Needham Promotions Inc., $270,000 over two years before HST and contingency, carried December 12, 2014 |
| RSD13-096 | 24932 | 6 | Tender opening, referred to staff |

The 2014 clothing bids come from the Executive Committee table, while the award comes from the carried Authority resolution A215/14. The bid rows include the one disqualified submission, which has no price. The supplier that did not submit by the deadline is excluded. This original contract and the 2015 re-tender are separate procurements. Port Union's explicit no-bid responses are excluded, and its award and award status are blank. The tender-opening record does not establish a winner. [2014 bid table](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1327362&dbid=0#page=537) · [2014 carried award](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1327312&dbid=0#page=536) · [2013 opening minutes](https://laserfiche.trca.ca/WebLink/ElectronicFile.aspx?docid=1046313&dbid=0#page=18)

Across both imports, the local export projection had six solicitations, 37 submitted bids, and five awards under the `trca_history` source.
