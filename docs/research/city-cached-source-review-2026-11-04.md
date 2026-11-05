# Cached City bid-source review — frozen November 4 inventory

This source investigation reviewed all **106 missing-coverage solicitations and 109 cached report associations** in the frozen `inventory.json`. It used held original report text/PDFs, the original council agenda bytes and the pinned original OData population. No database, cache, registry or production code was changed; no model or network requests were made. Identity findings do not by themselves authorize every cached bidder, price or stage.

The implementation subsequently approved an explicit literal-name projection for Vipond Inc in 2023.BA37.2. Its final admission decisions and whole-inventory recovery measurements are recorded in [the November 5 validation](city-cached-bid-validation-2026-11-05.md); the conservative research counts below describe the initial source review.

## Whole-inventory result

| Source identity finding | Solicitations | Report associations | Meaning |
| --- | ---: | ---: | --- |
| Existing compound evidence gate produces a proposal | 32 | 33 | Original call, typed source, issued/closed dates, full winner identity and initial net award uniquely match the original publisher population. Two PDFs share one council relationship. |
| Original report explicitly identifies the target as its primary Ariba Doc | 29 | 29 | Primary heading/Solicitation Number identifies the report subject. Bid table roles still need review; one report has a conflicting bid-table Doc. |
| Ancestor RFSQ mention only | 2 | 4 | The audit correctly marked these as printed mentions, not stored equivalences. Later tenders do not give their bids to the ancestor RFSQ. |
| Source context remains unresolved | 43 | 43 | Existing associations are not independently proved by this pass; a failed gate is not proof they are wrong. |
| **Total** | **106** | **109** | |

The 61 relationships in the first two groups are an **identity-review upper bound**, not a recovery count. No plainly wrong persisted link was established merely by the two ancestor examples: both are already `printed_document_mention_only`, with no `solicitation_link` for the target/reference pair. No unrelated old link should be deleted just because it fails the stronger current proof gate.

The 43 unresolved pairs comprise 21 global compound-witness failures, 12 source/publisher type disagreements, six absent exact date witnesses, two ambiguous labelled Call Number roles, one competing council context, and one title without a single recognized native call. These need source-specific review; digit stripping, old enriched titles and generic supplier/value matches cannot promote them.

## Primary-source identity checks

The original OData capture contains **7,812 records**, retrieved `2026-11-04T16:54:00.872009+00:00` from the [City solicitation publisher](https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/feis_solicitation_published). Exact capture-file SHA-256: `cb30d8bf23cc81060f04f55393b63104f727bf74e38b9e5e128a2948c9cc9769`. The captured population is dated evidence, not a claim that the live feed is unchanged; deployment must revalidate current publisher context.

The existing `city_crosswalk.propose` gate was run against every associated historical council context, using original held agenda bytes and global population uniqueness; the unique-council-call condition was also checked. Every proposed relationship’s printed call is present in its held report. Example proposals include [2015.BD16.3](https://secure.toronto.ca/council/report.do?meeting=2015.BD16&type=agenda), RFQ0722-14-0174, and [2013.BD131.8](https://secure.toronto.ca/council/report.do?meeting=2013.BD131&type=agenda), call1003-13-3051. These proposals must still be committed/revalidated through the existing mechanism before identity application.

### Ancestor mentions that must not become links

- **3512316731:** [2024.BA98.3 report248875](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-248875.pdf) expressly distinguishes RFT4641030969 from RFSQ3512316731. Its bid-table caption assigns the six bids to4641030969. The same RFSQ appears in the Contract No field of [the West Scarborough summary](https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/78gcUT5S9V5--CjnM08S3g), whose actual Ariba field is5400241558, and [the Paramedic garage summary](https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/dIaRQc4PV4R1Wmf8TaMU9w), whose actual Ariba field is5475812981. Their seven/nine tender bids belong to those downstream tenders.
- **4906085019:** [the East York Curling Club summary](https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/wxCW353EDCbcriHYwZ9NyA) names RFSQ4906085019 in an eligibility phrase but explicitly identifies actual Ariba Doc5519898277. Its three tender bids must not be assigned to the earlier RFSQ.

## BA source-participation review

The frozen set includes **48 BA report associations across 47 council references**. Two held PDFs share2018.BA62.2; replay must not count their four observations twice. Main-agent routing investigation, independently of this source review, found no normalized `ba_report` rows despite these held caches. Fixing that routing without source admission would expose the defects below.

| Conservative source disposition | Report associations |
| --- | ---: |
| Unique literal submitted-participant lists, prices withheld in the conservative projection | 29 |
| Explicit simple price rows reviewed | 6 |
| Participation supported, qualified prices withheld | 3 |
| Hold pending source/scope/name review | 10 |
| **Total** | **48** |

The 38 non-held source projections contain176 report observations, or172 unique `(council reference, literal supplier)` pairs after the duplicate BA62.2 source is removed. They contain23 reviewed literal prices. These are **source-role projections**, not permission to attach all38 sources to their candidate City document keys. Some historical identities remain unresolved. All inferred compliance/rank fields are withheld in the conservative projections.

### Required holds and qualifications

| Council item | Primary source finding |
| --- | --- |
| [2017.BA45.2](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-107644.pdf) | Seven first-stage RTP submission entries include two Black & McDonald technologies; cached prices come from award section, not submission table. Cannot treat role annotations as separate supplier identities. |
| [2024.BA98.3](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-248875.pdf) | Primary tender and bid table are Doc4641030969, not ancestor RFSQ Doc3512316731. This inventory case is a mention-only candidate, not a stored false link. |
| [2017.BA15.5](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-102278.pdf) | Parts A-D, prompt-payment-qualified evaluated amounts and different compliance by part; flat bid row cannot represent those scopes. |
| [2023.BA17.6](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-234755.pdf) | Ten lot-specific cached observations belong to three submitting firms; repeated firm names, amounts and outcomes differ by lot. |
| [2023.BA37.2](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238448.pdf) | One cache bidder Vipond Inc. is not literal in bid table (Vipond Inc without terminal period). Other source roles clear; require explicit source-name projection rather than broadening literal guard. |
| [2023.BA42.4](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238662.pdf) | Category A/B amounts flattened into compound price strings; supplier-level price/status projection needs separate reviewed scope. |
| [2024.BA61.3](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-241829.pdf) | Source declares seventy category submissions; cache has thirty-three flattened names and two names are not literal in bounded source table. Whole report needs category review. |
| [2023.BA58.4](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-241466.pdf) | Header excludes20%miscellaneous items; retain seven named submissions, withhold six qualified prices. Seventh source outcome is Pricing not submitted. |
| [2024.BA85.2](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-246969.pdf) | Regional four-area table, seven suppliers; cache concatenates four prices per supplier. No single global bid price is supported. |
| [2024.BA91.5](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-247994.pdf) | Primary heading/SolicitationNumber say Doc4566099607 but Table2 explicitly names4539266153; bid-to-document identity remains source-contradictory. |
| [2025.BA133.2](https://www.toronto.ca/legdocs/mmis/2025/ba/bgrd/backgroundfile-255420.pdf) | 124 category/subcategory observations repeat only28literal names with category-dependent statuses; cannot publish124distinctbidders or collapse statuses silently. |
| [2018.BA107.2](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-121529.pdf) | Bid header excludes25%miscellaneous costs and includes optional pricing; two separate price forms/names remain source observations. Withhold unqualified prices. |
| [2017.BA21.9](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-103596.pdf) | Report splits mobile/fixed-site services and qualifies Roy Foss evaluated price with2%prompt payment. Retain named participation; do not turn qualified price into unqualified amount. |

**Submission stage is separate from qualification.** RFSQ reports [2021.BA163.4](https://www.toronto.ca/legdocs/mmis/2021/ba/bgrd/backgroundfile-172883.pdf), [2023.BA34.3](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238025.pdf), and [2023.BA37.2](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238448.pdf) explicitly list19/eight/five submitted respondents. Several did not reach the pricing stage; they are neither nineteen/eight/five successful qualifiers nor downstream tender bidders. Participation in the named RFSQ can be retained only with that scope understood. Technical-threshold failure must not silently become a generic non-compliance claim.

**Price role matters even when name and amount both occur.** [2017.BA45.2](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-107644.pdf) lists seven unpriced RTP submissions, including two Black & McDonald technology submissions. The cache assigns two award-section amounts to bid rows. Global literal presence would pass those values but does not establish bid-price evidence.

**One source contradicts itself on identity.** [2024.BA91.5](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-247994.pdf) identifies Doc4566099607 in its heading and Solicitation Number, but its bid table names4539266153. The two cached prices cannot be assigned to4566099607 merely because its heading is clear. A separate original source must resolve that discrepancy.

**The source sometimes prints extreme numbers.** [2022.BA184.2](https://www.toronto.ca/legdocs/mmis/2022/ba/bgrd/backgroundfile-224572.pdf) really prints `$82,820,035,000.00` for Trio Roofing Systems Inc. It must never be silently corrected to a plausible amount; retain the source token/warning if admitted. Its table does not explicitly state the tax basis.

**Tax basis cannot come from award totals.** [2017.BA30.9](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-105509.pdf) explicitly labels bid prices including HST; [2022.BA177.3](https://www.toronto.ca/legdocs/mmis/2022/ba/bgrd/backgroundfile-222245.pdf) labels each price excluding HST; [2018.BA110.6](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-121994.pdf) states including HST. The reviewed price tables in [2018.BA77.7](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-115011.pdf) and [2018.BA78.11](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-115280.pdf) do not state that basis; the model’s inferred including-HST metadata is withheld.

## Reproducible private review artifacts

Under `.validation-city-cached-2026-11-04/`:
- `inventory.json` and `baseline.sqlite`: frozen106-case audit and read-only source state.
- `source-pair-review.json`: all109 report/source gate results, original witnesses and rejection reasons.
- `source-identity-classification.json`: all109 pair classifications reconciling to106 solicitations.
- `ba-source-review.json`: all48 BA source decisions, full bounded bid-table passages, report/result hashes and conservative per-bid projections. No source names or amounts were invented.
- `source_review.py` and `build-ba-research-review.py`: offline research reproduction. They write review artifacts only.

## Complete frozen case classification

This table accounts for every solicitation once. Duplicate held sources are shown on the same row. “Compound proposal” is the existing evidence gate result, not an automatic committed identity. “Primary Doc asserted” identifies the report subject, not necessarily every table within it.

| City document | Source context(s) | Identity assessment |
| --- | --- | --- |
| 0105160062 | [2016.BD115.7](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-99294.pdf) | unresolved |
| 0722140174 | [2015.BD16.3](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-77680.pdf) | compound proposal |
| 0902130156 | [2014.BD163.6](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-66394.pdf) | compound proposal |
| 1002133108 | [2013.BD139.8](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-60989.pdf) | unresolved |
| 1003133051 | [2013.BD131.8](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-59993.pdf) | compound proposal |
| 1101155017 | [2015.BD44.14](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-82921.pdf) | unresolved |
| 1201125097 | [2013.BD123.2](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-58161.pdf) | unresolved |
| 1201155002 | [2015.BD25.16](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-79751.pdf) | unresolved |
| 1205175043 | [2017.BA45.2](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-107644.pdf) | unresolved |
| 1212160101 | [2016.BD97.1](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-95398.pdf) | unresolved |
| 1222013000 | [2013.BD128.9](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-59376.pdf) | compound proposal |
| 1320160000 | [2016.BD85.5](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-93510.pdf) | compound proposal |
| 1372013000 | [2013.BD133.10](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-60160.pdf) | compound proposal |
| 1402013000 | [2013.BD130.4](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-59788.pdf) | compound proposal |
| 1442014000 | [2014.BD185.5](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-70615.pdf) | compound proposal |
| 1520130000 | [2013.BD116.11](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-56717.pdf) | compound proposal |
| 1692017000 | [2017.BA30.9](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-105509.pdf) | compound proposal |
| 1802013000 | [2013.BD133.3](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-60153.pdf) | compound proposal |
| 1842016000 | [2016.BD95.1](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-95197.pdf) | compound proposal |
| 1878555097 | [2019.BA52.3](https://www.toronto.ca/legdocs/mmis/2019/ba/bgrd/backgroundfile-139273.pdf) | primary Doc asserted |
| 2079914799 | [2020.BA67.6](https://www.toronto.ca/legdocs/mmis/2020/ba/bgrd/backgroundfile-146181.pdf) | primary Doc asserted |
| 2104163095 | [2016.BD110.1](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-98278.pdf) | compound proposal |
| 2252015000 | [2015.BD41.4](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-82403.pdf) | compound proposal |
| 2647034315 | [2020.BA112.9](https://www.toronto.ca/legdocs/mmis/2020/ba/bgrd/backgroundfile-158946.pdf) | primary Doc asserted |
| 2682219130 | [2021.BA127.4](https://www.toronto.ca/legdocs/mmis/2021/ba/bgrd/backgroundfile-165022.pdf) | primary Doc asserted |
| 2692012000 | [2013.BD116.10](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-56716.pdf) | compound proposal |
| 2789685328 | [2021.BA132.9](https://www.toronto.ca/legdocs/mmis/2021/ba/bgrd/backgroundfile-166039.pdf) | primary Doc asserted |
| 2792012000 | [2013.BD117.1](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57003.pdf) | compound proposal |
| 2792015000 | [2015.BD55.3](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-85401.pdf) | compound proposal |
| 2938706981 | [2021.BA163.4](https://www.toronto.ca/legdocs/mmis/2021/ba/bgrd/backgroundfile-172883.pdf) | primary Doc asserted |
| 2942014000 | [2015.BD1.5](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-73885.pdf) | compound proposal |
| 3122348800 | [2022.BA184.2](https://www.toronto.ca/legdocs/mmis/2022/ba/bgrd/backgroundfile-224572.pdf) | primary Doc asserted |
| 3201300000 | [2013.BD115.4](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-56609.pdf) | compound proposal |
| 3202177127 | [2017.BA43.1](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-107361.pdf) | compound proposal |
| 3251380249 | [2022.BA177.3](https://www.toronto.ca/legdocs/mmis/2022/ba/bgrd/backgroundfile-222245.pdf) | primary Doc asserted |
| 3401133097 | [2013.BD131.9](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-59992.pdf) | unresolved |
| 3405153133 | [2015.BD62.9](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-87063.pdf) | unresolved |
| 3405163142 | [2016.BD110.5](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-98282.pdf) | compound proposal |
| 3442615863 | [2022.BA202.7](https://www.toronto.ca/legdocs/mmis/2022/ba/bgrd/backgroundfile-229228.pdf) | primary Doc asserted |
| 3512316731 | [award summary](https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/78gcUT5S9V5--CjnM08S3g); [award summary](https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/dIaRQc4PV4R1Wmf8TaMU9w); [2024.BA98.3](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-248875.pdf) | ancestor mention only |
| 3621156462 | [2023.BA9.6](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-230777.pdf) | primary Doc asserted |
| 3701150197 | [2015.BD62.6](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-87062.pdf) | unresolved |
| 3707163238 | [2017.BA15.5](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-102278.pdf) | compound proposal |
| 3743124423 | [2023.BA39.2](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238551.pdf) | primary Doc asserted |
| 3782552477 | [2023.BA17.6](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-234755.pdf) | primary Doc asserted |
| 3817210853 | [2023.BA34.3](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238025.pdf) | primary Doc asserted |
| 3820130000 | [2013.BD121.3](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57671.pdf) | compound proposal |
| 3841985700 | [2023.BA37.2](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238448.pdf) | primary Doc asserted |
| 3905140125 | [2015.BD9.3](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-74999.pdf) | unresolved |
| 3940063088 | [2023.BA43.3](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238776.pdf) | primary Doc asserted |
| 3974631288 | [2023.BA42.4](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238662.pdf) | primary Doc asserted |
| 3988526049 | [2023.BA45.10](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-239108.pdf) | primary Doc asserted |
| 3992823329 | [2023.BA50.3](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-239902.pdf) | primary Doc asserted |
| 4044208417 | [2024.BA61.3](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-241829.pdf) | primary Doc asserted |
| 4102130037 | [2013.BD124.3](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-58279.pdf) | unresolved |
| 4110457678 | [2024.BA77.1](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-245273.pdf) | primary Doc asserted |
| 4125607314 | [2023.BA58.4](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-241466.pdf) | primary Doc asserted |
| 4173136336 | [2024.BA97.8](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-248624.pdf) | primary Doc asserted |
| 4201300000 | [2013.BD117.7](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57009.pdf) | compound proposal |
| 4291231363 | [2024.BA74.2](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-244740.pdf) | primary Doc asserted |
| 4305145033 | [2014.BD183.3](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-69757.pdf) | unresolved |
| 4305155039 | [2015.BD52.4](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-84578.pdf) | unresolved |
| 4306140111 | [2014.BD201.4](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-73381.pdf) | unresolved |
| 4343390536 | [2024.BA85.1](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-246962.pdf) | primary Doc asserted |
| 4355949486 | [2024.BA85.2](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-246969.pdf) | primary Doc asserted |
| 4395121516 | [2024.BA84.6](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-246510.pdf) | primary Doc asserted |
| 4556210399 | [2024.BA112.3](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-251742.pdf) | primary Doc asserted |
| 4566099607 | [2024.BA91.5](https://www.toronto.ca/legdocs/mmis/2024/ba/bgrd/backgroundfile-247994.pdf) | primary Doc asserted |
| 4815572097 | [2025.BA133.2](https://www.toronto.ca/legdocs/mmis/2025/ba/bgrd/backgroundfile-255420.pdf) | primary Doc asserted |
| 4906085019 | [award summary](https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/wxCW353EDCbcriHYwZ9NyA) | ancestor mention only |
| 4920180000 | [2018.BA77.7](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-115011.pdf) | unresolved |
| 5620160000 | [2016.BD76.5](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-91553.pdf) | compound proposal |
| 6026133006 | [2013.BD118.2](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57206.pdf) | unresolved |
| 6026180062 | [2018.BA107.2](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-121529.pdf) | unresolved |
| 6033127227 | [2013.BD109.2](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-55486.pdf) | unresolved |
| 6033163118 | [2016.BD104.3](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-96959.pdf) | unresolved |
| 6035143039 | [2014.BD180.8](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-69235.pdf) | compound proposal |
| 6038147100 | [2014.BD201.2](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-73350.pdf) | unresolved |
| 6102123249 | [2013.BD108.1](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-55340.pdf) | compound proposal |
| 6102153095 | [2015.BD44.10](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-82866.pdf) | unresolved |
| 6102153139 | [2015.BD62.7](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-87058.pdf) | unresolved |
| 6125133054 | [2013.BD127.2](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-58775.pdf) | unresolved |
| 6129163181 | [2017.BA21.9](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-103596.pdf) | unresolved |
| 6141133082 | [2013.BD131.6](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-60016.pdf) | unresolved |
| 6145133057 | [2013.BD131.5](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-60015.pdf) | unresolved |
| 6606137147 | [2013.BD141.2](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-61311.pdf) | unresolved |
| 6627137102 | [2013.BD145.2](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-61927.pdf) | unresolved |
| 6701133083 | [2013.BD130.10](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-59807.pdf) | unresolved |
| 7201300000 | [2013.BD117.4](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57006.pdf) | unresolved |
| 8420160000 | [2016.BD76.6](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-91555.pdf) | compound proposal |
| 8420180000 | [2018.BA78.11](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-115280.pdf) | unresolved |
| 9115167234 | [2016.BD115.13](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-99324.pdf) | unresolved |
| 9117165089 | [2017.BA8.5](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-101326.pdf) | unresolved |
| 9117167066 | [2016.BD93.10](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-95135.pdf) | unresolved |
| 9117175085 | [2018.BA63.1](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-112672.pdf) | unresolved |
| 9117177105 | [2017.BA23.12](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-104025.pdf) | unresolved |
| 9117177253 | [2018.BA62.2](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-112388.pdf); [2018.BA62.2](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-112554.pdf) | compound proposal |
| 9117187018 | [2018.BA96.3](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-120325.pdf) | compound proposal |
| 9118127211 | [2014.BD162.4](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-66261.pdf) | compound proposal |
| 9118187175 | [2018.BA109.1](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-121843.pdf) | unresolved |
| 9119167124 | [2017.BA13.4](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-101978.pdf) | unresolved |
| 9119177205 | [2017.BA33.4](https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-105803.pdf) | unresolved |
| 9120130000 | [2013.BD122.7](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-57826.pdf) | compound proposal |
| 9135157185 | [2015.BD58.1](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-86006.pdf) | unresolved |
| 9148157311 | [2016.BD77.4](https://www.toronto.ca/legdocs/mmis/2016/bd/bgrd/backgroundfile-91716.pdf) | unresolved |
| 9148187135 | [2018.BA110.6](https://www.toronto.ca/legdocs/mmis/2018/ba/bgrd/backgroundfile-121994.pdf) | unresolved |
