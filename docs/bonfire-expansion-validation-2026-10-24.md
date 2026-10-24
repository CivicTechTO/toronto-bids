# Remaining Bonfire buyers (#134) — 2026-10-24

Enable Waterfront Toronto, Exhibition Place and The Atmospheric Fund through the
existing public Bonfire adapter. Together with the already deployed Library and
shared TCHC/TSHC portal, this covers all buyers listed in #134. The implementation
adds configuration, the TAF buyer, attribution/permission records and real-source
tests. Adapter, schema, extraction prompt and frontend code are unchanged.

## Verified sources and identity

| Buyer | Official procurement page | Public origin | Portal title |
| --- | --- | --- | --- |
| Waterfront Toronto | [Current Opportunities](https://www.waterfrontoronto.ca/opportunities/procurement/current-opportunities) | `waterfrontoronto.bonfirehub.ca` | Toronto Waterfront Revitalization Corporation |
| Exhibition Place | [Procurement Opportunities](https://www.explace.on.ca/about/procurement-opportunities/) | `explace.bonfirehub.ca` | Exhibition Place |
| The Atmospheric Fund | [Careers and Procurement](https://taf.ca/about-us/careers-and-procurement/) | `taf.bonfirehub.ca` | The Atmospheric Fund |

Each official page links directly to its portal. A Waterfront tracking-wrapper
link was ignored in favour of its direct link. Its legal portal name is matched
without renaming or duplicating the existing Waterfront Toronto buyer. The City's
[agency profile](https://secure.toronto.ca/pa/decisionBody/36.do) identifies TAF as
a City agency serving the Greater Toronto and Hamilton Area; no funding percentage
is inferred.

The existing permission covers these three explicitly recorded inclusions. Only
anonymous public listing metadata is enabled. A successful nightly Bonfire step
now makes 15 serial GETs across five portals, with the existing one-second pacing,
bounded backoff and persisted Retry-After deadlines. No documents, login,
submissions, private-route requests or per-project requests are introduced.

## Held live responses

Each new portal used three ordinary public GETs: landing, Open and Past. The nine
unchanged bodies, including TAF's explicit empty Open collection, are committed as
fixtures with exact source hashes. Official-page snapshots and raw response
manifests are also retained locally.

| Buyer | Listings | Open | Closed | Awarded badge | Cancelled | Earliest captured close |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Waterfront Toronto | 78 | 1 | 77 | 0 | 0 | 2020-07-07 |
| Exhibition Place | 94 | 1 | 4 | 77 | 12 | 2022-12-15 |
| The Atmospheric Fund | 16 | 0 | 6 | 8 | 2 | 2025-03-24 |
| Added total | 188 | 2 | 87 | 85 | 14 | |

Repeated Exhibition Place reference `EPRFP2025-7745` remains two distinct portal
postings and procurement identities. Awarded badges create no winning-supplier or
contract-value award facts. Waterfront's 508 existing source-backed published
awards remain alongside its new listings as separate records.

These are the public collections returned by each portal, not a claim of complete
procurement history before their earliest captured close. In particular, the
Exhibition Place portal does not establish coverage back to the 2019 City-feed
cutoff; the old buyer note implying that timeline was removed. The older own-site
opportunities and award-report corpus are separate sources.

## Archive and frontend round trip

The existing configured capture was replayed twice per portal into an isolated
production-archive snapshot, using held original responses rather than repeating
source requests. All 188 postings acquired distinct procurement identities.
Repeated capture preserved listing facts, IDs, identities and first-seen dates.

Every pre-existing row in all 14 procurement fact tables, plus existing procurement
and procurement-key rows, was compared by its actual primary key and full payload;
all were unchanged. Only 188 portal rows and their 188 identities/keys were added.
SQLite integrity and foreign-key checks pass. The export contains 1,934 total
portal postings and preserves all existing City, agency and publication records.

The final JSON export SHA-256 is
`0d6e24b35d1c856f49c904f2ccbd4a1f5ce71171249234263a9c7916128f328f`.
The already merged frontend builds against it, and its complete internal-link crawl
and data-independent assertions for every buyer's exported records pass. No
frontend change is required for the new TAF page or mixed-source buyer pages.

The full scraper suite passes **1,254 tests**. Its combined nightly integration
uses all five portals and verifies 1,561 fixture listings in JSON, Parquet and CSV,
plus successful step history. Three new profile regressions failed before the
configuration change and pass afterward.

Evidence, snapshot, validation script and report are under
`/home/alex/tb-data/operations/134/`. Frontend build evidence is in the workbench's
`.data/134-build.log`. Validation made no production archive writes, paid model
requests or publication. The new profiles require the merged checkout before the
next scheduled nightly can capture them.
