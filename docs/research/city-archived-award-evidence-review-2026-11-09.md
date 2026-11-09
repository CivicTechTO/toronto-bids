# Archived City award evidence: independent review — 2026-11-09

## Finding

The new original-publisher route supplies materially stronger identity evidence than inferred number encodings or approximate Council award amounts. I independently checked the **366 proposed archive links** against the original archived HTML and **7,817 freshly captured original publisher records**, and reviewed a **40-item stratified sample** of the associated Council source context. I found no failed source-row hashes, ambiguous fresh publisher matches, cancelled/test candidates, post-capture award dates, or missing held bidder observations in this proposed cohort.

This supports a **dedicated archived-publisher identity proof**, provided implementation preserves its exact source evidence and keeps contradictory facts visible. It does **not** establish that Council's initial recommendation, supplier spelling, solicitation method or issued/closed dates agree with the migrated record. Those are separate facts; replacing them or labelling the new proof as the previous exact compound-date/initial-amount proof would be unsafe.

The recovery count remains provisional until ingestion, bid-source validation and a net coverage recount. This research did not mutate production, implementation or GitHub.

## Evidence and independently repeated checks

Inputs are `.validation-high-yield-recovery-2026-11-09/archive-qualified.json`, the original `historical-frontier/wayback-*.body` files and matching provenance sidecars, the original BA/BD agenda HTML, and `current-publisher/snapshot.json`. The latter pins all eight original response pages, capture times and hashes from the [City's source feed](https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/feis_solicitation_published), retrieved on November 9. Review output is retained privately as `independent-archive-review.json` beside those inputs.

For every candidate, independently reopened the exact source `<tr>` by ordinal, checked the whole original response hash, and read its call, division, description, supplier, amount, award-date and record-link cells. All source responses state that listed award amounts exclude HST. The earlier [2016 original view](https://web.archive.org/web/20160129203250id_/https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView) and [2018 original expanded view](https://web.archive.org/web/20180727185105id_/https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView&Count=1000&ExpandView) illustrate the actual headers and record roles.

I repeated the exact award-date/full-supplier/amount lookup against the complete fresh population, without filtering by guessed document number, Council date, issue/closing date or solicitation method. **All 366 identify exactly one fresh publisher record.** Full archive and matching publisher supplier strings are identical in 363 cases; the other three involve trailing whitespace or the apostrophe in Lima's/Limas. No new trading-name or spelling alias is required between these two publishers. Existing punctuation/legal-suffix normalization remains an explicit matching boundary, not literal byte equality.

All 366 fresh records have `Status=Awarded` and `Awarded_Cancelled=No`. Inspected source row images are blank placeholders, without a cancellation indicator. No proposed call contains a Notice/Summary wrapper. Every candidate has at least one retained bid row under its Council reference. None has an original award date later than its archive timestamp. This matters because erroneous year 3013 and a future-dated notice occur elsewhere in the acquired population; they did not enter these 366 candidates.

The native-call bridge is the same explicitly printed call in the original award row, Council item title and labelled Call Number field. It is not the legacy Lotus Notes record identifier and not a transformed current numeric document key. Qualification also refuses reused calls/multiple Council contexts and competing current documents; those refusals must remain enabled. A [calibration detail page](https://web.archive.org/web/20180727040245id_/https://wx.toronto.ca/inter/pmmd/callawards.nsf/fa687bbbf211bf4a8525791100515d51/85258049005dea63852581af006a8f62?OpenDocument) independently demonstrates how an original record prints call, dates, supplier and value together. The bulk rows' record links preserve access to that more detailed source where captured.

## Stratified source-context review

The 40 reviewed contexts cover all eight source captures contributing candidates, with three distributed positions from each capture and additional amount/name discrepancies. **26** have a different first Council recommendation amount; **18** have a differently expressed recommendation string, including multi-supplier lists. These are not estimates of 40 independent company aliases. Original contexts and original publisher fields were compared; enriched local titles were not used as independent evidence.

Reviewed references:

| Source capture | Council references inspected |
| --- | --- |
| January 2016 | `2015.BD24.1`, `2015.BD21.2`, `2015.BD59.5`, `2015.BD26.5`, `2015.BD9.6`, `2015.BD26.10`, `2014.BD188.7`, `2015.BD30.4`, `2014.BD188.2`, `2014.BD170.1`, `2015.BD52.6`, `2015.BD8.3`, `2015.BD18.7`, `2015.BD61.9`, `2015.BD30.2`, `2015.BD35.1`, `2015.BD41.1`, `2015.BD58.9`, `2015.BD50.3` |
| September 2016 | `2016.BD87.8`, `2016.BD91.1`, `2016.BD82.4` |
| January 2017 | `2016.BD103.4`, `2016.BD103.9`, `2016.BD98.4` |
| July 2018 | `2018.BA67.2`, `2018.BA77.6`, `2018.BA77.3` |
| January 2019 | `2019.BA4.8`, `2018.BA78.6`, `2017.BA33.1` |
| June 2019 | `2019.BA22.20`, `2019.BA10.8`, `2018.BA110.2` |
| November 2019 | `2019.BA39.3`, `2019.BA23.6`, `2019.BA41.5` |
| August 2020 | `2019.BA36.1`, `2019.BA34.4`, `2019.BA56.6` |

## Real discrepancies and scope risks

| Original source example | Observed difference | Safe interpretation |
| --- | --- | --- |
| [2015.BD24.1](https://secure.toronto.ca/council/agenda-item.do?item=2015.BD24.1), Tender 18-2015 | Council initial net recommendation $3,917,605.50; original portal and fresh feed $6,917,605.00. | This is substantial disagreement, not rounding. The original portal names the same native call and full award facts; retain both published amounts and do not claim an explained amendment. |
| [2015.BD30.2](https://secure.toronto.ca/council/agenda-item.do?item=2015.BD30.2), RFQ 3405-15-3040 | Council says Inforica Inc.; original portal and current publisher say Informica Inc. | Native-call identity can be evidenced without inventing a supplier spelling alias. Keep the conflicting raw supplier names; this proof must not merge their supplier identities automatically. |
| [2015.BD50.3](https://secure.toronto.ca/council/agenda-item.do?item=2015.BD50.3), REOI 3910-15-5003 | Native call is 2015 in title/field, but Council's printed issue/closing dates are in 2013, unlike the 2015 feed dates. Four supplier award scopes are present. | Do not certify date agreement or collapse the award scopes. The independent portal binds the native call to its published award. |
| [2015.BD35.1](https://secure.toronto.ca/council/agenda-item.do?item=2015.BD35.1) and [2015.BD58.9](https://secure.toronto.ca/council/agenda-item.do?item=2015.BD58.9) | Council/feed closing dates differ: June 1/3, and July 10/14 respectively. | Do not silently treat either as an established extension. Preserve the discrepancy. |
| [2015.BD59.5](https://secure.toronto.ca/council/agenda-item.do?item=2015.BD59.5), CH2M Hill | Council recommendation is $773,816.67; matched original award row is $302,338.00; the current record has three CH2M award entries. | This is solicitation identity, not proof that the chosen row equals the whole Council contract or all original award lots. |
| [2019.BA4.8](https://secure.toronto.ca/council/agenda-item.do?item=2019.BA4.8), building automation | Twelve awarded technology groups and multiple suppliers; a specific Ainsworth row matches $104,526.90. | Preserve group-level prices, award scopes and bidder rows. Do not manufacture a single winning bid or aggregate from one matched row. |

Across the full proposed cohort, **272** first Council amounts differ from the archive amount and **35** current publisher records contain multiple awarded-supplier entries. A simple title-based method comparison also finds **33** Council/publisher method disagreements. The source-role proof must store these facts rather than labelling them agreed. Some archive descriptions are generic/truncated or contain different quantities from the final Council recommendation; description-token overlap is not the new identity evidence.

The matching feed date is the **nested supplier `Date_Awarded`**, not a Council decision date, top-level empty date field or derived date. The [official CKAN field documentation](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/datastore_search?resource_id=e211f003-5909-4bea-bd96-d75899d8e612&limit=1) distinguishes historical contract-execution dates from newer solicitation-award dates. Do not present a historical date match as independent agreement on approval timing. Similarly, call the amount the **published award amount as of the archive capture**, not a guaranteed latest contract value today.

## Required proof limits

Retain original HTML/hash/row/link, archived capture timestamp, actual source award date, fresh publisher snapshot/record/hash, normalized matching inputs and their raw originals, Council context/hash/native-call field, and bid-source references. Revalidate against the entire fresh population and reject competing native calls/current records. Keep the new proof class separate from the previous dates/type/initial-net-value class.

Use this evidence only to establish the solicitation/call identity. It must not resolve disputed supplier spellings, rewrite issue/closing dates, convert a later award into a submitted bid, collapse multi-award lots, or infer any missing bid price. No arithmetic amount tolerance, fuzzy supplier alias, numeric encoding, QA row or cancellation was needed for the inspected candidates. Final bid counts and recovery totals still require the parent workload's implementation and ingestion checks.
