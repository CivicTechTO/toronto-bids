# Historical City bid recovery: systematic headroom — 2026-10-31

This is a read-only investigation of the post-recovery production snapshot at `.validation-systematic-bids-2026-10-31/production.sqlite`. No source facts, identities, model prompts or caches changed; no paid model calls were made. Thirteen public staff-report PDFs were fetched serially with at least 1.1 seconds between requests and retained only in the private investigation directory.

## The bulk of the gap is historical

Of **4,105 awarded City documents without captured bids**, **3,592 (87.5%)** have solicitation issue dates through 2019: 701 through 2012, 2,467 in 2013–2018 and 424 in 2019. This is the City-spine population only, not the independent agency or composite Call Number populations. These measurements use the merged audit's source-key and canonical-ID reconciliation, not a direct-document-only bid count. [Audit implementation](../../scrapers/toronto_bids/bid_coverage.py), [previous audit definitions](../audits/bid-coverage-2026-10-30/README.md).

The old framing that all historical spine document numbers are unrelated Ariba identifiers is too broad. The source pipeline normalizes `Solicitation_Document_Number` by stripping non-digits; many historical 10-digit values look exactly like division/year/sequence Call Numbers without punctuation. Tender-number/year values also exhibit a right-padding pattern. These patterns are useful discovery hypotheses but need original publisher-key confirmation before becoming identity rules. [OData normalization](../../scrapers/toronto_bids/sources/odata.py), [document-number normalization](../../scrapers/toronto_bids/linking/document_number.py), [Call Number normalization](../../scrapers/toronto_bids/linking/call_number.py).

## Two larger, measurable routes

### 1. Candidate linkage to bids already held

A complete local scan compared missing historical documents with printed Call Numbers in held council-item titles. A trial encoding removes punctuation from division/year/sequence calls, or right-pads tender-number/year digits to ten places. Against **1,284 existing recorded pre-Ariba links**, this agrees in **1,279** cases and disagrees in **five**. Existing links are not an independent gold set: source review below proves two are wrong. The 99.6% agreement must not be presented as matcher precision.

Among missing historical documents:

| Candidate state | Documents |
| --- | ---: |
| Call shape found in current title or description | 443 |
| That printed call also appears in a held council-item title | 282 |
| Those reference(s) have held bid observations | 12 |
| Trial document encoding identifies a held printed call | 1,112 |
| Encoding candidate reference(s) already have bid observations | 669 |

The 669 candidates span **3,312 stored bid observations**. **330** use a division/year/sequence Call Number whose digit count is already ten; **339** use the padded tender-number/year hypothesis. None conflicts with a different currently recorded document association on the candidate reference. Nineteen have multiple candidate references for the same printed call, requiring amendment/repetition handling. None has multiple printed-call strings under this trial encoding.

These are **candidate sets, not approved recoveries**. All 12 cases with both a current printed title/description call and held bids have derived `council_pre_ariba` titles; none has the needed independent original-feed title/description corroboration in the snapshot. Reusing those derived titles as fresh identity proof would be circular. The potentially large route is preserving and validating the original publisher Call Number and joining on that typed identifier, rather than widening winner/price matching.

### 2. Acquire already-known but deliberately skipped staff reports

There are **586 queued `bgrd` PDFs whose council references have no stored bids**. Every one is from the 2013–2016 Bid Committee series:

| Report reference year | Queued PDFs with no bids |
| --- | ---: |
| 2013 | 187 |
| 2014 | 123 |
| 2015 | 158 |
| 2016 | 118 |

**389 missing historical City documents** have a trial Call Number candidate in those queued references. Two overlap the 669 already-held-bid candidates, leaving **387 additional document-acquisition candidates**. This overlap correction gives 1,056 distinct candidate documents across the two routes, not a prediction that 1,056 are safely recoverable.

The extraction exclusion is explicit: `ba_report` admits only `%.BA%`, `composite` admits 2009–2012, and the code comment excludes 2013–2016 BD reports because their agendas supposedly tabulate their bids. The queued/no-bid evidence shows that assumption is not universal. Downloading these PDFs without admitting the 2013–2016 BD staff-report corpus would still leave them unprocessed. [Corpus membership](../../scrapers/toronto_bids/extraction.py), [staff-report source](../../scrapers/toronto_bids/sources/bid_award_panel.py).

I tested the acquisition route on a deterministic stratified sample: two queued reports per year, selected with `random.Random(163 + year)`. **All eight public PDFs downloaded and all eight contain named bidder/proponent information**. This small pilot supports building systematic acquisition and extraction, but is not an extrapolated recovery rate for all 586 reports.

| Official report | Direct primary-source finding |
| --- | --- |
| [2013.BD115.4](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-56609.pdf) | Kingston Road tender: priced firms plus separate informal submissions; retain their distinctions. |
| [2013.BD136.9](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-60506.pdf) | Page 4 names six learning-management-system proponents, including losing proponents. |
| [2014.BD198.1](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-73230.pdf) | Two named bidders; one explicitly non-compliant. |
| [2014.BD209.1](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-73808.pdf) | Names AON and Marsh and describes both proposals passing the technical threshold. |
| [2015.BD6.3](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-74551.pdf) | Eight firms across three parts, with different prices and no-bid outcomes by part. A single firm-level price/status would be misleading. |
| [2015.BD44.16](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-82901.pdf) | Three named salt suppliers with bid prices and an explicit price-scope note. |
| [2016.BD84.12](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-93223.pdf) | Three named firms; the lowest priced firm is non-compliant. |
| [2016.BD115.8](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-99320.pdf) | Publishes one named bid with a prompt-payment-discount note. |

## Correctness gates are needed before scaling

The existing pre-Ariba matcher accepts a unique rounded award amount plus **one shared supplier token**, with no date/period check. Generic tokens including `and` and `construction` survive its supplier tokenization. The five identifier disagreements exposed the following source contradictions. [Matcher implementation](../../scrapers/toronto_bids/sources/bid_award_panel.py).

| Existing reference → document | Primary-source comparison | Assessment |
| --- | --- | --- |
| `2014.BD174.2` → `5092408869` | [2014 report](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-68240.pdf) is Tender 12-2014, McGregor Arena, Integrated Building & Design, $1.72M. Linked OData document is an April 2025 roadway tender awarded to D CRUPI AND SONS for $1.72M. Supplier-token intersection is only `and`. | Incorrect scope established by the primary report and held feed record. This reference has no stored bids, so it does not add false bid coverage by itself; the document already has six bid observations from other evidence. |
| `2015.BD25.1` → `2962047194` | [2015 report](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-79745.pdf) is Tender 14-2015, Facilities Management pavement/curb work, Bond Paving & Construction, $224,400. Linked OData document was issued in May 2021 and awarded to SANSCON CONSTRUCTION LIMITED for $224,400. Shared token is only `construction`. | Incorrect scope established. Its eight stored observations currently supply the linked document's entire captured-bid coverage. They are not eight validated firms; see table alignment defect below. |
| `2014.BD196.1` → `1802014000` | [Report](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-73025.pdf) prints Tender 189-2014; supplier/value match the linked 2014 feed record, but its key resembles 180-2014. | Publisher-identifier discrepancy; not enough evidence to declare the existing link wrong. Four stored bid observations. |
| `2016.BD92.4` → `1692016000` | [Report](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-95064.pdf) prints Tender 69-2016, while the linked 2016 feed key resembles 169-2016 and carries an Exhibition Place division. Supplier/value match. | Identifier/scope discrepancy remains unresolved; three stored bid observations. |
| `2017.BA21.4` → `9118177022` | [Report](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-103613.pdf) prints RFP 9118-17-7002; linked feed key ends 7022. Supplier, value and year agree. | Identifier discrepancy, not proven wrong identity. Three stored bid observations. |

The primary 2015.BD25.1 PDF page 3 also proves a separate **bidder↔price alignment defect** in the stored agenda extraction. The source starts Bond Paving & Construction at $286,536.36, followed by Serve Construction at $359,463.32, and ends Dig-Con at $995,617.27. The eight stored rows start Serve at Bond's price and end with the non-compliance footnote as a bidder at Dig-Con's price. All eight stored bidder/price pairings are shifted. The held official agenda HTML has the correct firm and price ordering; this is reproducible in the current parser, not a price-column shift already present in publisher HTML. `_cell_lines` produces a bidder header, eight names and a footnote, versus a price header, a separate `(including HST)` line and eight prices. After the parser removes only the first header on each side, both arrays have nine entries, so the equal-length guard passes. Bond is paired with the HST heading and skipped as non-price; every later pair shifts, and the footnote consumes the last price. `parse_bid_tables` reproduces all eight wrong relations. This also loses the HST heading context. Thus canonical-ID agreement and row-count equality alone are insufficient quality checks. No data was corrected during this investigation.

This is a reason to improve source attribution and regression fixtures before mass linking, not to abandon the sizeable historical opportunity. At least the two proven bad identity links, generic-token evidence and table-alignment case need explicit regression coverage. Native publisher IDs should carry the identity; source names, dates and values should corroborate or reject contradictions, not become a weak replacement key.

## Composite-era caution

The claim that composite reports never name bidders is also too absolute. [The February 2009 composite report](https://www.toronto.ca/legdocs/mmis/2009/bd/bgrd/backgroundfile-18819.pdf) names Lafarge and Gazzola in district/season-specific rejection notes for RFQ3905-08-0273. That shows exceptions exist, but this example predates the current City-spine missing cohort. Most cached composite bidder arrays largely restate award names and do not independently prove a losing-bid list. Do not treat cached composite bid counts as immediately publishable participation or use them to claim a universal historical floor.

## Reproduction and artifacts

Read-only scripts and complete candidate/counterexample evidence are in `.validation-systematic-bids-2026-10-31/historical/`:

- `probe.py`, `metrics.json`, `missing-historical.json`, `known-link-calibration.json`: complete local linkage and overlap analysis.
- `mismatch_review.py`, `identifier-disagreements.json`: all five source comparisons, exact PDF SHA-256 values and source/feed observations.
- `sample_queued.py`, `queued-sample.json`: deterministic eight-report sample and source hashes.
- `parser-shift-repro.json`: exact official HTML column lines and current parser output for 2015.BD25.1.
- Thirteen PDFs and their `pdftotext -layout` text, retained privately and never inserted into production.

The research scripts read the pinned SQLite using `mode=ro`; they do not run the matcher in write mode. Candidate encoding is intentionally a throwaway measurement, not a new production identity rule. The implementation recommendation is a publisher-key validation gate followed by bulk queued-PDF acquisition, explicit historical BD corpus admission, and one reviewed pipeline over the whole inventory—not another sequence of individual document recoveries.
