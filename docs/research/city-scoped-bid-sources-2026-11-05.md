# City scoped bid source review — November 5, 2026

This review accounts for all eleven already-identified City procurements held for unrepresented lot/category/alternative scope. It excludes Doc4566099607, whose heading and bid table contradict each other on document identity. Sources are the held original City report PDFs, the immutable `background_pdf.text` and cached extraction results; no runtime code, registries, cache or production data were changed, and no model or network requests were made.

`source-packs.json` preserves all eleven original report records/cache payloads. `scoped-source-proposals.json` is an **offline research proposal**, not admitted production data: it describes 321 source-cell observations, with 300 scoped submitted responses, 20 explicit No Bid cells and one unscoped noncompliance outcome. Those are response observations, not 321 distinct firms or necessarily 300 independently numbered offer documents. The 104 price cells comprise 102 currency amounts and two percentage discounts. Every qualification below must survive implementation; flattening a cache does not settle any source relationship.

| City document / source | Source observation accounting | Participation and scope |
| --- | --- | --- |
| 0902130156 / [2014.BD163.6](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-66394.pdf) | Category 1 seven prices; Category 2 six prices; 13 responses, 13 currency prices | Eight distinct printed name forms; true firm count is 7–8 until Five Star/Five Start source spelling is resolved. |
| 1003133051 / [2013.BD131.8](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-59993.pdf) | Seven priced offers, including POI Bid 1 and Bid 2 | Six firms; source-labelled alternatives belong to POI, not separate companies. |
| 2104163095 / [2016.BD110.1](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-98278.pdf) | Six scoped responses: Part A two discounts and one noncompliance; Part B three currency prices | Three firms. Part A is percentage off MSRP; Part B is a one-year bid price. |
| 3405163142 / [2016.BD110.5](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-98282.pdf) | Onx Part A and Part B prices; one KTI unscoped noncompliance outcome | Two firms; do not manufacture one KTI response per part. |
| 3707163238 / [2017.BA15.5](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-102278.pdf) | Four parts × two firms = eight responses, seven prices, one noncompliance | Hank prices have prompt-payment qualification. Lima noncompliance applies only to Part A. |
| 3782552477 / [2023.BA17.6](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-234755.pdf) | Seven sparse lot rows, ten populated price cells | Three firms. Blank cells are not explicit No Bid assertions. CAD is explicit; tax basis is not. |
| 3974631288 / [2023.BA42.4](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238662.pdf) | Two firms × two categories = four prices | Two firms. Apinds row has explicit noncompliance and enormous literal prices; retain both facts without fixing the source. |
| 4044208417 / [2024.BA61.3](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-241829.pdf) | 70 physical name cells: A17/B26/C20/D6/Others1 | 32 supported supplier-key identities plus three unresolved party contexts. Source is not 70 distinct firms. |
| 4355949486 / [2024.BA85.2](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-246969.pdf) | Four geographic rows × seven firms = 28 prices | Seven firms; each area is a separate response scope. Tax basis is not printed. |
| 4815572097 / [2025.BA133.2](https://www.toronto.ca/legdocs/mmis/2025/ba/bgrd/backgroundfile-255420.pdf) | 124 named responses across eleven leaf scopes; no bid prices | 27 printed name forms after footnote removal, 26 supplier-key identities. Category B cancellation does not erase its twelve submissions. |
| 1002133108 / [2013.BD139.8](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-60989.pdf) | Eight price tables × six offer blocks = 48 cells: 28 submitted currency prices and 20 explicit No Bid cells | Four firms; Amovo has three labelled options, creating six source offer blocks. No Bid cells must not inflate submitted-response totals. |

## Per-source decisions

### 0902130156 — toys versus creative supplies

The two literal scope labels are `Category 1 (Toys)` and `Category 2 (Creative Supplies)`. The price header includes HST **and 30% miscellaneous**; these are not unqualified base bid prices. Spectrum Educational Supplies is explicitly marked non-compliant in both categories. Other firms' compliance is not asserted.

The source prints `Five Star Enterprise of Canada` in Category 1 and `Five Start Enterprise of Canada` in Category 2. Both complete source strings are retained and tagged with an identity ambiguity group in the research proposals. A model typo correction or generic supplier-key merge cannot silently establish that the extra “t” is erroneous. The source has 13 category responses; participation can be bounded at seven to eight identities, rather than reporting eight proven firms.

### 1003133051 — POI's two source-labelled bids

Seven entries include `POI Business Interiors Inc. (Bid 2)` and `(Bid 1)`. They are two alternative offers from the same named participant. Six entries explicitly print a noncompliance parenthesis; SCI has no such source qualifier, which is not affirmative proof of compliance. All prices are headed `Total Bids (incl all Option Years)` and `net of all applicable taxes and charges`; preserve that contract-period and tax basis rather than compare them as initial-year prices.

### 2104163095 — discounts and cash are different measures

Part A is headed `Percentage Off MSRP:` and publishes `10%*`, `10%`, and Tyco's `Non-Compliant`. The asterisk states that a coin toss on October 18, 2016 broke tied bids; it is not a correction or noncompliance marker. Part B is headed `Bid Price (incl. HST) for 1 year:` and publishes three amounts. Tyco's Part A noncompliance must not be copied onto its Part B offer.

### 3405163142 — preserve the unscoped outcome

The source prints Onx once with Part A $1,566,387.86 and Part B $2,943,772.73, under `Total Bid Price (Excluding HST)`. KTI appears once with `Non-compliant`, without a separate Part A/Part B allocation. Store two Onx scoped price observations and one event-level KTI outcome; declaring four responses would invent the source structure.

### 3707163238 — part-specific status, discount and award disposition

Hank has a price in each Part A–D, with the condition `incl. 5% prompt payment, net 15 days`. The Part D value is literally `$1447.80`. Lima is non-compliant in Part A and priced in Parts B–D. The double-asterisk says Part C was not awarded due to budgetary constraints; that is a scope award disposition, not an absence of submitted bids or a finding that all Part C offers were non-compliant.

### 3782552477 — sparse lot matrix

The original PDF's row/column geometry binds ten prices: Rush has populated cells in all seven lots; MID-ONTARIO has Lot 3 and Lot 4; Premier has Lot 5. The footnotes deem Premier Lot 5 non-compliant and Rush Lots 1, 2, 4, 5 and 7 non-compliant. Rush's Lots 3 and 6 do not inherit that status. Blank cells elsewhere are unreported, not explicit No Bid observations. Every monetary value explicitly includes `CAD`; the bid-table tax basis is absent. Preserve the printed Lot 7 spelling `Elodrado Bus`.

### 3974631288 — literal source outliers

KR publishes $241,520.54 in Category A and $514,947.00 in Category B. Apinds publishes the literal values $756,131,500.00 and $247,804,800.00. The table heading is `Total Price (net of HST)`. Its supplier marker is explained by the misspelled source footnote `*non-complaint`; retain the explicit noncompliance qualifier and both enormous prices without deleting zeros or inferring a corrected value.

### 4044208417 — the actual PDF grid resolves a count trap

PDF pages 7–9 contain 70 physical name cells: A17, B26, C20, D6 and Others1. The Others cell contains the compound string `2302726 Ontario Inc. Enqueue Staffing*` in one bordered cell. Splitting its two name-looking fragments would create an unsupported 71st record. Its legal/trading-name/joint-party relationship is not stated, so it remains one unresolved party context. The asterisk attaches to the Enqueue fragment and must not become a firm-wide finding against an invented combined identity.

Two Category B cells also require unresolved identity evidence: `Consulting LLC *` lacks a Cincotek prefix; `IAN Martin Information Technology` lacks the `Consulting Inc` suffix invented in the flattened cache. The 67 other physical cells resolve to 32 supplier-key identities (34 literal name forms before typographic/legal-form normalization). This is not a claim of complete firm identity for all 70 responses.

Other cache issues: Category A's actual `Mandrake Management Consultant Corp.` became `Mandrake Design Group` in the cache; Category B's `Canadian Executive Search Group Inc.` is omitted. I4C and IAN Martin lack source asterisks; cached noncompliance on those names is unsupported. Nonstarred names are not thereby affirmatively compliant.

### 4355949486 — geographic price matrix

Four literal area rows are North West, South West, North East and South East. Each has seven firm columns. The 28 values must be bound by both area and firm column; the cache's four-price concatenation per firm is not a scalar financial amount. The source heading says only `including bid price`, with no HST statement. In particular, P.S. Painting's North West $664,328.80 differs from South West $664,262.80 and from the two eastern $664,509.80 cells; retaining the grid matters.

### 4815572097 — 124 responses across eleven scopes

Leaf-scope counts are 21, 15, 16, 10, 5, 5, 6, 10, 10, 14 and 12, reconciling to the declared 124. They cover Category A subcategories 1, 2, 3.1–3.5, 4, 5 and 6, plus Category B. After removing only trailing footnote stars, 27 printed name forms reduce to 26 supplier-key identities; the Concentric `Inc`/`Inc.` variant is punctuation, not another firm.

Asterisk noncompliance is scoped to each listed response. Curated Leadership is starred in 2SLGBTQ+ Education and unstarred in other categories; an event-wide supplier status would be false. Unstarred names are not source-affirmed compliant despite the cache's classifications. Category B's cancellation footnote is an outcome for that category; the source still lists its twelve submissions.

### 1002133108 — no-bid cells and labelled offer alternatives

Each of SCI, Specialty Furniture, Amovo Option 1, Amovo Option 2, Amovo Option 3 and Dor-Val has a block of eight price-table cells. SCI has eight prices, Specialty one, each Amovo alternative five, and Dor-Val four: 28 prices. The other twenty cells explicitly say `No Bid`/`No bid`; those are outcomes, not submitted offers. The source reports four firms and six offer blocks, not 48 bidders.

The table prices are for the initial-year period including HST and applicable charges. A final `Price Corrections net of HST` paragraph gives SCI Table 1 from $50,073.56 to $52,476.08, which does not directly reconcile to the printed initial-year including-HST $108,762.47. Preserve that correction as separate source evidence; do not overwrite the table cell or invent a tax/quantity conversion.

## PDF witnesses and reproducibility

- `build-source-proposals.py` assembles the manually reviewed 321 source observations as private research notes. It is not runtime extraction code and must not be installed as a source parser.
- `add-geometry.py` runs generic `pdftotext -bbox-layout` over held PDFs and constructs exact page/rectangle/text witnesses. The cropped text includes words whose centres lie in the rectangle, grouped by XML source line and ordered y/x.
- All 194 cells in the 70- and 124-entry tables have party/row and scope-header witnesses. The 124 rows also retain their full hierarchy of source headings.
- All 38 price responses in the sparse-lot and geographic matrices have party-column, scope-row, response-row and individual price-cell witnesses. Six sparse-lot noncompliance responses also carry the exact source footnote witness. These matrix records retain column rectangles so crossed supplier/value assignments can be refused.
- `complete-geometry.py` subsequently adds exact page/rectangle/text witnesses for the other 89 observations. All 321 now have complete source crops, composed name parts where necessary, party areas, and disjoint leaf-scope areas. All prices have individual value/header witnesses; alternatives, explicit outcomes and conditional bases have separate source witnesses. Corporate Express demonstrates why multi-line names sometimes require composed crops: a single bounding rectangle would include its trailing noncompliance annotation. The completed witnesses pass the runtime generic crop verifier and party/response/scope/header containment checks.
- Original PDF bytes, held text, raw extraction JSON and generated layout text have separate recorded hashes. The source data and cache remain immutable. Matrix geometry is reviewed against the original PDFs, not inferred from the flattened cache.

Rendered page7/page8 screenshots for Doc4044208417 are retained privately; they establish the single compound Others cell and the actual truncated names. All per-case source links above point to the first-party report that owns the facts.
