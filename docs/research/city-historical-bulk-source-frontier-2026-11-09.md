# Historical City bulk-source frontier — 2026-11-09

## Result

Implementation follow-up: the combined workload subsequently verified **380 newly covered solicitations** on a fresh production copy. See [the complete validation and recovered-document list](city-archived-award-validation-2026-11-09.md). The recon counts below remain source inventories, not recovery counts.

**A subsequent bounded Wayback check found a real bulk primary-source route: archived original City award views and award-detail pages.** The first retrieved 2016, 2018 and 2019 bulk views contain hundreds of native-call award records each. An independently archived detail page explicitly publishes the native call, exact issue/closing dates, full awarded supplier and exact award value. This can provide evidence missing from Council's differently scoped or rounded recommendation; no digit reconstruction is required. Recoveries still require the workload's full identity and bidder-source checks. See the archive section below.

The live-source checks alone yielded no verified recoveries. Retired CKAN resources point to unavailable Lotus Notes backends rather than downloadable historical snapshots. The City MERX mirror contains only **3 closed notices, 2 result-bearing notices and 0 awarded notices**; both result-bearing notices publish conspicuous QA supplier data, including one with a real-looking historical title. None should enter production.

These findings do **not** establish that historical bid records never existed or cannot be disclosed. The City's own routine-disclosure plan explicitly provides a route for requesting past solicitation results. That is a potential bulk acquisition route requiring an actual request and a response; this research neither contacted staff nor assumes any delivery volume.

## Retired CKAN / original Lotus Notes sources

Read the complete [CKAN package list](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_list): **556 dataset names**. Procurement-related names expose the retired call/award/noncompetitive datasets and the current TOBids datasets; no separately named historical procurement archive surfaced. Also inspected [contract package search](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_search?q=contracts&rows=100) (**11 results**) and [solicitation package search](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_search?q=solicitations&rows=100) (**5 results**). This is catalogue recon, not a claim that differently named or unpublished archives cannot exist.

| Official resource | Advertised content / observed access | Demonstrated new records |
| --- | --- | ---: |
| [Retired Competitive Contracts](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=competitive-call-award-results), resource `27ea9544-eda2-48d0-b503-2bdd4bb563cd` | One XML resource, `datastore_active=false`, `cache_url=null`; links to `https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?ReadViewEntries`. It describes supplier awards, not a losing-bidder table. | 0 |
| [Retired call documents](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=call-documents-for-the-purchase-of-goods-and-services), resource `2160e5d4-6c6c-4895-95ce-9831b3553dc6` | One XML resource, no active datastore/cache; official URL is `https://wx.toronto.ca/inter/pmmd/calls.nsf/002FC5CABA39CB078525783300553575?readviewentries`. | 0 |
| [Old award resource datastore request](https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/datastore_search?resource_id=27ea9544-eda2-48d0-b503-2bdd4bb563cd&limit=1) | HTTP 404; no retained datastore body exposed through this supported current-record endpoint. | 0 |
| CKAN package activity history | `package_activity_list` returns HTTP 400, explicitly reporting an unknown action. This deployment did not expose that version-history mechanism. | Unknown |

One request each to the advertised `wx.toronto.ca` award view and the legacy calls `posted` view failed with **No route to host** in this environment. The prior [publisher-key audit](city-call-publisher-keys-2026-11-01.md) already documented a separate `wi.toronto.ca` legacy-record DNS failure and inspected all original fields in the historical migrated feed. This pass did not perform host substitutions or URL guessing. A browser cannot be assumed to repair a network route failure.

No original publisher record establishing a general native-call-to-migrated-number construction rule was discovered. The earlier complete-feed and 119-held-snapshot negative findings remain applicable; they were not repeated as a new recovery experiment. See [the original audit](city-call-publisher-keys-2026-11-01.md) and [November 7 follow-up](legacy-publisher-identifier-evidence-2026-11-07.md).

## City of Toronto MERX mirror: bounded full listing and result check

The [City buyer's closed page](https://www.merx.com/cityoftoronto/solicitations/closed-bids) returned HTTP 200 through a normal anonymous request using this project's public archive User-Agent. The web browsing tool returned HTTP 403 for the same page, so that failure alone would have incorrectly ruled out this route. Existing `PublicSession` then fetched both actual detail pages and only their advertised result tabs, with its existing five-second pacing, bounded request budget and response capture. **Playwright was unnecessary.** No login, bid action or private endpoint was used.

The visible heading gives **3 closed notices**, all present on one page, spanning publication dates **2016-06-13 through 2019-08-07**:

| Native number / publication | Notice | Result observation |
| --- | --- | --- |
| `123-2019XX`, 2019-08-07 | [Request for Test Sourcing Event](https://www.merx.com/cityoftoronto/solicitations/Request-for-Test-Sourcing-Event/0000157260?origin=2) | Explicit test event. Advertised [result tab](https://www.merx.com/public/solicitations/1115189108/abstract/bid-results) has 3 QA-labelled suppliers with $1, $2 and $3. |
| `District Energy`, 2016-11-19 | [Development of Low carbon Thermal Energy Networks (Disttrict Energy) in Toronto](https://www.merx.com/cityoftoronto/solicitations/Development-of-Low-carbon-Thermal-Energy-Networks-Disttrict-Energy-in-Toronto/0000044136?origin=2) | Not in the result-bearing listing. No result or award facts claimed. |
| `958847`, 2016-06-13 | [Civic Theatres Toronto RFP for Executive Search Services](https://www.merx.com/cityoftoronto/solicitations/Civic-Theatres-Toronto-RFP-for-Executive-Search-Services/0000035659?origin=2) | Advertised [unofficial-result tab](https://www.merx.com/public/solicitations/185479326/abstract/bid-results) has the same 3 QA-labelled suppliers with $1m, $2m and $3m. Its results listing gives a 2019-08-07 publication date. Real-looking notice metadata does not make these test supplier facts usable. |

The [Bid Results listing](https://www.merx.com/cityoftoronto/solicitations/bidresults-bids) gives **2** entries: the test event and Civic Theatres. The [Awarded listing](https://www.merx.com/cityoftoronto/solicitations/awarded-bids) explicitly gives **0**. Each inspected result has the names `5.1.0 QA Test Org 2 (KK)`, `5.1.0 QA Test Supplier`, and `5.2.0.1.0 QA Test Supplier`. There are **6 mechanically parseable rows, zero verified procurement bidder facts**. The tiny City buyer mirror offers no demonstrated route to the requested 340 recoveries.

This is a useful source-quality counterexample for future bulk discovery: successful parsing and buyer-scoped hosting do not by themselves establish genuine procurement results. No generic MERX runtime change was made as part of this research.

## Authoritative disclosure frontier

The [City's November 2020 PMMD Routine Disclosure Plan](https://www.toronto.ca/wp-content/uploads/2020/10/9717-PMMD-Routine-Disclosure-Plan.pdf), pages 5–6, distinguishes unofficial solicitation results from supplier award announcements. It provides supplier names and bid totals for RFQs/Tenders, names for RFPs, and directs requests for past material to PMMD. Its online award repository covered two years; older award records could be requested by solicitation number. This establishes a disclosure route, **not an online complete backfill or a guarantee all older records survive**.

The [2025 portal transparency report](https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-256906.pdf) says procurement records are retained internally through contract termination/expiry plus seven years, while public portal retention is shorter, and describes a plan for older Open Data datasets. That supports asking for retained originals or an actual historical export. It does not establish that a public dataset exists today or that every 2010–2016 file remains retained.

A genuinely different bulk workload would require an acquired publisher export containing native solicitation number, independently scoped issue/closing dates, supplier/award values and, ideally, the complete original supplier-result table. Requesting only awarded suppliers could aid identity but would not recover losing participation. Usable volume, the years retained, machine-readable format, and any native/migrated crosswalk remain **unknown until the publisher supplies them**. No outreach was performed.

## Evidence and limits

Raw catalogue responses, access outcomes and MERX listing HTML are retained privately in `.validation-high-yield-recovery-2026-11-09/historical-frontier/`. `merx-captures/` contains the existing collector's immutable response bodies, hashes and request diagnostics for all four detail/tab requests. Recon was read-only and bounded; it made no production, identity, model, runtime or GitHub changes.

There is no defensible positive count toward 340 from the live CKAN/MERX probes. Their limits should prevent another implementation cycle on this tiny/test MERX mirror or metadata-only retired resources. The following archive finding changes the frontier and deserves a separate complete recovery measurement.

## Wayback follow-up: original publisher evidence survives at bulk scale

The initial exact `ReadViewEntries` [availability request](https://archive.org/wayback/available?url=https%3A%2F%2Fwx.toronto.ca%2Finter%2Fpmmd%2Fcallawards.nsf%2Fpostedawards%3FReadViewEntries&timestamp=20150101) returned an empty `archived_snapshots` object. This establishes absence of a returned snapshot for that exact request, **not absence of archived award data**. A bounded CDX wildcard query found alternate original `OpenView` pages, including expanded bulk views.

The retained [award-view CDX index](https://web.archive.org/cdx/search/cdx?url=wx.toronto.ca%2Finter%2Fpmmd%2Fcallawards.nsf%2Fpostedawards%2A&output=json&filter=statuscode%3A200&collapse=digest&fl=timestamp%2Coriginal%2Cmimetype%2Cstatuscode%2Cdigest&limit=10000) returned **1,520 distinct-content HTML view captures**, below its 10,000 limit. These are snapshots/query variants, not 1,520 independent solicitations. The [broader award-path CDX index](https://web.archive.org/cdx/search/cdx?url=wx.toronto.ca%2Finter%2Fpmmd%2Fcallawards.nsf%2F%2A&output=json&filter=statuscode%3A200&collapse=urlkey&fl=timestamp%2Coriginal%2Cmimetype%2Cstatuscode&limit=10000) returned **7,023 distinct indexed URLs**, including **4,405 PDFs** and **2,448 HTML pages**. Deduplicating the final 32-hex Lotus Notes document identifier yields **1,530 source document identifiers**, **1,516 with an HTML capture**. These figures are index evidence, not a completeness claim or verified recovery count; attachments, alternate views and duplicate snapshots must be reconciled.

Three early populated captures were successfully retrieved:

| Archived primary source | Dated source rows / distinct printed calls | Source award years and limits |
| --- | ---: | --- |
| [2016-01-29 award view](https://web.archive.org/web/20160129203250id_/https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView) | 586 / 585 | 536 rows dated 2015, 48 dated 2016, and 2 with erroneous literal year 3013. 552 monetary rows. Do not silently correct source dates. |
| [2018-07-27 expanded award view](https://web.archive.org/web/20180727185105id_/https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView&Count=1000&ExpandView) | 455 / 455 | 236 dated 2017, 219 dated 2018; 416 monetary rows. |
| [2019-01-29 expanded award view](https://web.archive.org/web/20190129053432id_/https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView&Count=1000&ExpandView) | 413 / 413 | 395 dated 2018, 18 dated 2019; 345 monetary rows. Overlaps the previous view. |

Counts use actual table rows with a source award-date cell; cancellation/nonmonetary rows remain separate. The views explicitly label the original columns: native Call Number, Client Division/Contact, Description, Successful Bidder, Contract Amount, Date Awarded. Both inspected 2016 and 2018 originals explicitly state that listed award amounts **exclude HST**. This is historical publisher tax-basis evidence, unlike an inference from a modern template. Source errors and differing award scopes still require review.

The [archived original detail for Tender 229-2017](https://web.archive.org/web/20180727040245id_/https://wx.toronto.ca/inter/pmmd/callawards.nsf/fa687bbbf211bf4a8525791100515d51/85258049005dea63852581af006a8f62?OpenDocument) is a successful calibration example. The same original record states native call `229-2017`, Bond Paving & Construction Inc., award **$391,771.00**, award date **January 16, 2018**, issue date **October 4, 2017**, and close **October 23, 2017**. Its stable source document identifier is `85258049005dea63852581af006a8f62`. The source publication dates are distinct from its **2018-07-27 archive capture** and **2026-11-09 retrieval**. It links original solicitation/addendum PDFs. It does not expose a losing-bidder table or explicitly assert a migrated numeric document number.

The viable proof route is therefore: an original native-call award record establishes independently printed dates/full supplier/exact awarded amount; those facts must uniquely and consistently identify the migrated publisher record while the Council document independently names the same native call. Do not equate a Lotus Notes identifier, padded number, or stripped native call to a current document key without this evidence. Also do not confuse the original published awarded amount with an initial Council recommendation or the submitted bid price.

Additional **already indexed** views were selected for sparse temporal coverage—September 2016, January 2017, June/November 2019 and August 2020—rather than fetching every view or guessing record URLs. Each response has a sidecar with source URL, archive timestamp, retrieval time, byte count and SHA-256; later captures also record final replay URL. The parent workload is measuring their deduplicated intersection with current missing documents and independently retained bidder sources. Those intersections are not interchangeable with recovered coverage and are not finalized in this recon note.

The requested follow-up views all returned HTTP 200 at their exact archive timestamps. A final July 2022 expanded view was added for the later rolling window, then acquisition stopped pending measured yield. All six also retain the original explicit HST-exclusion header:

| Archive timestamp / retained body stem | Dated rows / distinct calls / monetary rows | Literal award years |
| --- | ---: | --- |
| `20160914221140` | 631 / 630 / 562 | 2015: 186; 2016: 443; erroneous 3013: 2 |
| `20170102161633` | 604 / 603 / 535 | 2016: 599; 2017: 3; erroneous 3013: 2 |
| `20190615155937` | 380 / 380 / 292 | 2018: 265; 2019: 115 |
| `20191127003121` | 314 / 313 / 239 | 2018: 26; 2019: 287; future 2020: 1 |
| `20200812040946` | 480 / 473 / 403 | 2019: 212; 2020: 268 |
| `20220709221534` | 183 / 180 / 108 | 2021: 116; 2022: 67 |

File names are `wayback-{timestamp}-awards.body` with matching `.json` provenance sidecars. `archive-view-sizing.json` records the local row measurements and exact historical HST header text. Repeated calls can represent supplier/award scopes; repeated snapshots must be deduplicated by original evidence, not counted as incremental recoveries. A source award date later than its archive capture is an explicit anomaly, not a reason to rewrite the date.

There is not yet evidence of a bulk losing-bid-result export: the original 2018 award page directs tender-result enquiries to the hotline/buyer, and no bidder-result-named attachment URL surfaced in the indexed award-path filename scan. That is a bounded negative observation, not proof that no attachment contains bidders. The archive's demonstrated immediate value is **bulk original identity/award corroboration**, which could unlock already held bidder records once fully verified.

Evidence files are `wayback-cdx-*.body` (JSON arrays with header rows), `wayback-2016-awards.body`, `wayback-2018-awards-expanded.body`, `wayback-2019-awards-expanded.body`, timestamped additional views, and `wayback-2018-award-detail-229-2017.body` beneath the same private frontier directory. The first unexpanded 2018 capture contains only collapsed categories and must not be counted as a full source population. No archive-save request, bulk speculative page crawl, outreach, model call or production mutation was performed.
