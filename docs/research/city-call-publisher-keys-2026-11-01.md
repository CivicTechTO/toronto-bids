# Historical City Call Number publisher evidence — 2026-11-01

## Finding

The current original City feed does **not** expose a broad hidden punctuated historical Call Number that the scraper merely needs to preserve. All 4,980 pre-2020 records already publish their `Solicitation_Document_Number` without a recognized native Call Number shape. Five records contain a genuine printed call in another original publisher field; only one is in the refreshed missing-with-held-bids candidate inventory. Encoded-key similarity remains a discovery hypothesis, not proof of identity.

The raw feed's issue/closing dates, solicitation type, awarded suppliers and award values are independent evidence suitable for a separately described corroboration gate. A link accepted on those compound facts must retain that proof class; it must not claim that the publisher directly asserted an identifier equivalence.

## Primary evidence captured

The [City's original OData solicitation feed](https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/feis_solicitation_published) returned HTTP 200 on 2026-11-01. We captured it once through the scraper's existing `$skip`/`$top=1000` paging: eight nonempty pages and a terminal empty page, **7,804 raw records**, between **02:09:56 and 02:10:17 UTC**. Requests were serial with at least 1.2 seconds between completed requests. Each exact response body has its retrieval time, full URL and SHA-256 retained privately; no production database writes or paid model calls occurred.

Pinned evidence is under `.validation-city-crosswalk-2026-11-01/publisher-keys/`:

- `odata-full.json`: combined untouched source rows in `rows`, with the per-page provenance manifest in `pages`.
- `odata-page-00000.body` through `odata-page-07000.body`, plus terminal `odata-page-07804.body`: exact publisher response bytes.
- `capture-feed.py`, `analyze.py`, `field-analysis.json`: reproducible acquisition and analysis.
- `ckan-package.json`, `ckan-one.json` and their `.body` responses: current dataset/resource metadata and one original CKAN row.
- `3707190005-legacy-callaward.json`: bounded legacy publisher URL result.

Example exact source-page SHA-256 values: page 0 `0730782e89c34b789c988788f54e548e5e2b2811d70ba323e1e2a0126d52a903`; page 1000 `b5ef08e86455ef7314b6f3da3c7d44cb92f5bd8fe12a60d30e97174475a85f1c`; page 2000 `685c368211d3d91468eff7682df12b432dc02b0c20412257906c5314f9538ba3`.

A targeted normal-looking OData string filter returned HTTP 400 with an `Edm.Boolean` / `Edm.String` type error. We did not treat an unsupported filter or an empty search as source-record absence. The complete supported paging path provides the actual record population.

## What the historical publisher records contain

Every row has an issue date, so the 4,980-record historical cohort is precisely `Issue_Date < 2020-01-01`. Of those records, 4,881 have a generic original `Posting_Title` beginning `Doc-`; 99 have another title. Normalized or enriched database titles must not replace these original values when proving identity.

| Original field | Historical nonempty rows | Rows with a recognized call-shaped string |
| --- | ---: | ---: |
| `Solicitation_Document_Number` | 4,980 | **0** |
| `Contract_Number_Purchase_Order` | 0 | 0 |
| `Solicitation_Document_Details` | 0 | 0 |
| `Staff_Notes` | 0 | 0 |
| `uploadedFilesStaff` | 0 | 0 |
| `Solicitation_Award_Details` | 33 | 3 |
| `Posting_Title` | 4,980 | 2 |
| `Solicitation_Document_Description` | 96 | 2, both unsuitable as this solicitation's call |

Forty-three historical raw document-number values contain non-digit characters, but these are not lost punctuated native calls. Examples include `Notice9134187160`, `Summary9117177362`, `3710106+0111`, and `390513test`. The current [document-number normalizer](../../scrapers/toronto_bids/linking/document_number.py) strips characters before checking length, while the [OData adapter](../../scrapers/toronto_bids/sources/odata.py) does not retain these original fields. Preserving original values is useful for provenance, but this population disproves the expectation that raw punctuation recovery alone unlocks hundreds of historical links.

The source has 7,804 unique row `id` values and 7,792 distinct `Parent_Id` values. All repeated parent IDs in this snapshot belong to modern records; at least one shared parent covers two different document numbers (`5851888337` and `5792333645`) with different issue/closing dates. Do not assume `Parent_Id` is a unique solicitation identity or use it as an unqualified alias join.

## Direct native-call examples and role counterexamples

| Original document key | Source field | Printed evidence | Interpretation |
| --- | --- | --- | --- |
| `3303123110` | `Solicitation_Award_Details` | `3303-12-3110` followed by multiple supplier/schedule awards | Direct publisher call/document co-occurrence; award scopes must remain distinct. |
| `9118197041` | `Solicitation_Award_Details` | `Call number: 9118-19-7041` | Direct publisher call/document co-occurrence. |
| `3707190005` | `Solicitation_Award_Details` | `Call number: 3707-19-0005` | Direct publisher call/document co-occurrence; Parts A/B values are explicit. |
| `Notice9134187160` | `Posting_Title` | RFSQ No. `9134-18-7160` | Native-call evidence for a notice, not proof of an awarded bidder stage. |
| `9117167066` | `Posting_Title` | `9117-16-7066` | Direct publisher call/document co-occurrence. |
| `6038093213` | `Solicitation_Document_Description` | `2009-2010 Winter Season` | A year range accidentally matches the tender-call regex; **not a Call Number**. |
| `3907095237` | `Solicitation_Document_Description` | `Request for Prequalification No. 3907-09-5022` | Names an earlier prequalification, **not this RFQ's native call**. |

All table examples are in source page 0 except `9117167066` (page 2000). These examples demonstrate why regex shape and textual co-occurrence need identifier-role review.

For the refreshed candidate `3707190005` → `2019.BA18.4`, the publisher row has `id=efa850dc-be2f-40f6-b0c2-4ef29ac7f763`, `Issue_Date=2018-12-20`, `Closing_Date=2019-02-08`, type RFQ, and supplier Municipal Maintenance Inc. The same original row states the native call and Part A/B values **$335,800** / **$269,840**, with an aggregate awarded supplier amount of **$605,640**. A reviewed report match can cite that original field directly; it does not depend on reconstructing a call from digits.

Its [source-published legacy award URL](https://wi.toronto.ca/inter/pmmd/callawards.nsf/76eed99c01c3800b852579e500719662/85258049005dea6385258394005305ec?OpenDocument) failed DNS resolution in one bounded HTTP request at 02:12:25 UTC. We made no retries or host substitutions. This is an observed accessibility failure, not evidence that the underlying historical record never existed. The surviving original OData award-details field remains usable evidence independently of that failed legacy page.

## Existing counterexamples remain material

The original feed confirms the following values without relying on enriched local titles:

- `1802014000`: source title `Doc-1802014000`, issue **2014-05-26**, close **2014-06-12**, Toronto Water, Active Mechanical Services, amount **576265.00**. The separately held report for `2014.BD196.1` prints Tender **189-2014**, issued **2014-06-12**, closed **2014-07-04**. The same supplier/award amount does not reconcile that Call Number and those date pairs. This remains a source discrepancy, not a newly approved alias.
- `1692016000`: source title `Doc-1692016000`, issue **2016-07-21**, close **2016-08-22**, Exhibition Place, Clean Water Works Inc., amount **7230965.00**, award date **2016-07-20**. The separately held `2016.BD92.4` report prints Tender **69-2016**, issued **2016-04-25**, closed **2016-05-24**. The source even places its award before its issue date. Matching supplier/value/year does not prove that the source key and this report describe the same solicitation.
- `9118177022`: source title `Doc-9118177022`, issue **2017-02-02**, close **2017-02-21**, Montgomery Sisam Architects Inc., amount **2923200.00**. Its held report prints **9118-17-7002**. Retain the discrepancy; substituting the council-derived local title would hide rather than resolve it.

The complete held report URLs, PDF hashes and source excerpts for these comparisons are in the earlier `historical/identifier-disagreements.json` evidence and [historical investigation](historical-bid-headroom-2026-10-31.md). The two already repaired wrong-period/wrong-supplier links (`2014.BD174.2` and `2015.BD25.1`) also remain essential negative controls.

## CKAN and held legacy snapshots

The [current CKAN dataset metadata](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=tobids-awarded-contracts) resolves to awarded resource `e211f003-5909-4bea-bd96-d75899d8e612`; [its one-row datastore response](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/datastore_search?resource_id=e211f003-5909-4bea-bd96-d75899d8e612&limit=1) reports 7,796 rows and 14 fields. It has a `Document Number` and narrative description, but no separate native `CallNumber` column. The first source record's key is already `3303123110`.

The publisher's field notes define Document Number as its tracking reference, without documenting digit stripping or tender-number padding. They also state that the pre-October-2025 award-authority date captured **contract execution**, while newer records use **solicitation award**. That date must not be blindly equated with a council meeting/report decision date. The historical amount and date context must be bound to each accepted proof.

A complete read of all **119** held `legacy/azure/open_data/*.json` snapshots found **5,378** non-null `CallNumber` observations and **zero** recognized historical native call shapes. These are 2023–2024 open-notice captures, mostly `Doc...` keys and construction-contract labels. They do not provide the missing historical publisher crosswalk.

## Recommended proof boundary

1. **Direct publisher evidence:** the same original publisher record explicitly names the native call in the correct procurement role and its document key. Match that typed call to the actual council/report contract, retain source row ID, source-page/record hash, field name and exact quotation, and reject source/report contradictions. Notice, parent procurement, earlier prequalification, award lot and actual bidding stage remain separate roles.
2. **Corroborated compound evidence:** if direct publisher-call evidence is absent, label the proof class honestly. Require an unambiguous printed report call and contract context, exact independently published issued/closed dates, compatible solicitation type, full supplier identity, and an explicitly scoped matching award value/basis. Establish uniqueness against the entire source population and preserve the compared facts. Any unexplained call, date, supplier, scope or type conflict keeps the candidate unresolved. Source migrations and extensions can explain a date discrepancy only when their own evidence is retained, not through broad tolerance rules.
3. **Discovery only:** digit stripping, tender padding, local derived titles, old link agreement, generic supplier-token overlap and equal rounded prices cannot by themselves admit an equivalence. A collection of such dependent signals does not become independent proof by being numerous.

Store accepted equivalences with their proof class and immutable source evidence. Preserve the original feed/cache payloads, refused candidate context and canonical identity history. An updated publisher record or changed reviewed report must trigger revalidation rather than silently reusing proof for different facts.

## Subsequent source-contradiction check

The direct assertion for `3707190005` is not an approved equivalence in this pass. The [original 2019.BA18 agenda](https://secure.toronto.ca/council/report.do?meeting=2019.BA18&type=agenda) gives item `2019.BA18.4` an issued date of **February 1, 2019** and closing date of **February 19, 2019**; the pinned original OData row gives **December 20, 2018** and **February 8, 2019**. The identical printed call, supplier and matching Part A/B values do not explain that contradiction. It remains held for source/reissue review, rather than treating the explicit call as permission to ignore conflicting publication facts. The original item text and publisher row are retained among the primary-source regression controls.
