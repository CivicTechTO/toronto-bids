# MERX pagination follow-up — 2026-10-24

**The normal TTC public search has a real 1,000-result window. Playwright reproduces
it, but smaller positive keyword queries provide a verified listing workaround.**
A complete 210-listing query has now been retrieved, including legacy and middle-year
records. Whole-archive coverage remains unverified; no production adapter or backfill
has been built. MERX reproduction authorization is already recorded.

## Verified workaround: positive queries plus independent TTC reports

Use ordinary keyword searches **without a wildcard**, then paginate each bounded
query and deduplicate by the published notice identifier. The ordinary
[`keywords=T11` query](https://www.merx.com/ttc/solicitations/closed-bids?selectedContent=BUYER&keywords=T11)
works where `T11*` returned zero. Fresh serial requests, separated by at least
5.2 seconds and using the archive-identifying User-Agent, established:

| Query | Advertised count | Verified retrieval |
| --- | ---: | --- |
| `T11` | 210 | All nine pages: 210 distinct IDs; eight pages of 25 plus ten on page nine; total remained 210 on every page |
| `T06` | 552 | First page and terminal page 23; terminal page contains the expected two rows |
| `P11` | 115 | First page and terminal page five; terminal page contains the expected 15 rows |

The complete `T11` query includes legacy notice `879704954` and notices published
between the oldest-first and newest-first windows (including 2018–2020). This
proves that the 1,000-result window does **not** make those listings inaccessible.
The failed wildcard probe was not evidence that plain prefix queries would fail.
These searches are keyword matches, not asserted disjoint or exhaustive identifier
partitions. No sum of their counts is claimed to represent the archive.

A practical backfill can collect the two chronological windows, derive smaller
positive queries from their public solicitation codes/titles, and merge their
results by notice ID. Queries still exceeding the cap need narrower positive
terms. Begin/end population checks, repeat-page detection, stable per-query totals,
and overlap accounting are necessary. Completion requires a reconciled unique
union; unexplained missing records must remain an explicit coverage gap.

For legacy **details**, an independent public source works for the actual gated
example. TTC's [March 27, 2013 staff report](https://cdn.ttc.ca/-/media/Project/TTC/DevProto/Documents/Home/Public-Meetings/Board/2013/March-27/Reports/Procurement_Authoriz.pdf?hash=336EDF47FFB9E73695D735CA9A1DDAF9&rev=0d82a1f0bfff4780b9d04fbbd6b802d8)
for C1-38 / Wilson Carhouse North Expansion includes seven named bidders and
prices in Appendix B, and recommends Buttcon at $37,700,190 inclusive of taxes.
The [TTC meeting record](https://www.ttc.ca/All-public-meetings/board/2013/board-meetings-mar-27)
independently records approval. The join is contract C1-38 plus title; the report
does not establish the MERX solicitation number `T11PT12870`. Preserve the evidence
for that bridge. The report's advertised date is November 7, 2012, whereas MERX's
listing publication is November 8; preserve both source-specific dates.

This is TTC's independently published content, retrieved without MERX account
access. It demonstrates recovery of actual historical bid/award evidence, not just
listing metadata. It does not establish that TTC reports cover every MERX notice;
contracts without matching public reports may still need a supported export or
legitimate account entitlement for complete legacy details.

A source-derived older advanced-search path,
`https://www.merx.com/public/supplier/solicitations/search`, returned a SAML login
handoff rather than an anonymous advanced form. No authentication was attempted;
this route supplies no verified anonymous date-range workaround.

Research scripts and raw responses are in `.validation-merx/workaround/` and
`.validation-merx/verify-prefix-workaround.py`. `prefix-verification.json` retains
per-page IDs, counts, selected pages, response hashes and URLs. Raw HTML is private
(mode 0600); form token values are omitted from structured metadata. No production
writes or paid model calls were made.

## Confirmed boundary

Fresh requests use an explicit identifying Toronto Bids archive User-Agent and
at least five seconds between page/search requests. With the same public query:

- Page 1 returned 25 notices, selected page 1, and 40 page options.
- Page 40 returned 25 notices, selected page 40.
- Requesting page 41 returned selected page **40** and exactly the same ordered
  notice IDs as page 40. It did not advance and did not return an empty page.

Thus an iterator that stops only on empty pages could repeat indefinitely, and an
iterator following the displayed pager could stop short of the advertised total.
The [Closed tab](https://www.merx.com/ttc/solicitations/closed-bids?selectedContent=BUYER)
advertised 2,938 in initial reconnaissance, 2,927 in the first follow-up sequence,
2,920 in a later baseline, and 2,918 in a subsequent sort test. The cause of the
changing totals is unestablished. Each count belongs to its observed response;
it must not be treated as a stable snapshot or a completeness guarantee.

## Older listing metadata versus legacy detail access

The older records are not simply absent. Oldest-first search exposes a 2012
solicitation, and an exact public search for `T11PT12870` returns that listing.
However, its published link follows a different path from modern notices:

```text
/public/supplier/interception/view-notice/879704954
  -> /public/supplier/solicitations/notice/879704954/abstract
```

The first response is 302; the final [abstract page](https://www.merx.com/public/supplier/solicitations/notice/879704954/abstract)
returns HTTP 200 but explicitly requires login and subscription-package access.
The actual notice title/details are absent. No login or subscription action was
attempted. Whether the appropriate entitlement is free or paid is unestablished.
An HTTP-success check alone would incorrectly treat that gate as captured data.

Thus search enumeration and access to each record's detail are separate problems.
Modern sampled notice details/results are public; at least this older detail is
account-gated while its listing metadata remains public. It is not evidence that
the record is irretrievably lost, nor that every older notice has identical access.

The listing also uses different identifier forms: the older link carries the
nine-digit `879704954`, whereas modern examples expose ten-digit zero-prefixed
references. Preserve the published route/identifier and redirect provenance;
do not assume one numeric prefix describes the entire archive. An ordinary
`T11*` search returned zero despite the exact solicitation-number search succeeding,
so wildcard semantics remain unsuitable for an asserted exhaustive partition.

For complete legacy details, the most reliable next route is a historical export
from MERX/TTC or a legitimate account with the relevant entitlement. Reproduction
authorization is already recorded; the missing piece is access to the content.
Overlapping smaller public searches can still be assessed for listing metadata,
but no complete union has been demonstrated. It must reconcile stable unique IDs
and the publisher's population before being called a complete backfill.

## What Playwright established

A real Chromium context loaded the public TTC page without account login or
challenge handling, using the identifying archive User-Agent. Browser interaction
with Published Date (Oldest first) generated:

```text
/ttc/solicitations/closed-bids?sortBy=publicationDate&sortDirection=ASC&pageNumberSelect=1
```

That is the working UI request format. Earlier manual probes using lowercase
`asc` and an explicit empty keyword parameter returned 400; they did not establish
that oldest-first sorting was unavailable. The browser trace corrected the request
format. Browser sorting still exposed only 40 page options.

Plain HTTP with the working sort parameters returned the oldest notices from
November 2012 on page 1. Page 40 of the oldest-first window reached November 2017.
The latest-first page-40 sample reached late 2021/early 2022. Reversing the sort
therefore exposes additional history but does not itself prove coverage of the
middle of the roughly 2,900-record population.

Playwright also submitted the actual search form:

| UI search | Result |
| --- | --- |
| `T06BI26617` | HTTP 200, one notice |
| `"PCM Operating" AND NOT SUPPLY` | HTTP 403 |

The same exclusion query was rejected through plain HTTP. No alternate encoding,
challenge solver or access-control workaround was attempted. The response proves
rejection of that tested request, not the reason for rejection or a general
subscription rule.

The browser's recorded document/search traffic did not reveal a hidden historical
JSON feed or additional date/organization controls. It was useful for observing
the UI; it did not remove the result window in this anonymous session.

## Supported-looking workarounds tested and rejected as completeness proofs

1. **Keyword organization phrases.** The buyer form supports `keywords`, and an
   exact TTC solicitation-number control found one record. Organization-name
   phrases returned large result sets, but their counts did not establish a
   disjoint, exhaustive partition. Keyword matching is not an exact organization
   filter. Pure-negative controls returned zero, while the tested positive-base
   exclusion expressions returned 403.
2. **Reference prefix search.** Searching the exact public MERX reference
   `0000333265` found that notice. However, `0000*` matched only 2,052 against an
   adjacent unfiltered count of 2,920. The proposed prefix partition was stopped
   at that failed coverage assertion; it was not used to infer complete history.
3. **Actual organization paths.** Source-derived child-profile paths returned
   public Operating and Capital listing pages, with observed counts of 2,671 and
   1,330 respectively. Both still had 40 pages. Their relationship to the parent
   profile must be reconciled by identifiers; simply adding child totals is not
   evidence of the parent's coverage.
4. **Public date controls.** The real [public search page](https://www.merx.com/public/solicitations/open)
   exposes `publishDate` presets All, Last 24 Hours and Last Week. The inspected
   anonymous forms do not supply arbitrary historical start/end dates or an exact
   organization selector. A guessed older search-processing route returned 400;
   it is not an established API.
5. **Advertised sitemap.** The exact sitemap URL named in the fresh robots file,
   `https://www.merx.com/sitemap.xml`, returned 404. This does not prove that every
   provider-supported catalogue/export is unavailable.

Official MERX [filter documentation](https://www.merx.com/resources/articles/using-the-search-filters-to-find-matching-tenders)
and the older [Opportunity Matching guide](https://marketing.merx.com/marketing/Documents/Generic%20MERX/OpportunityMatching.pdf)
describe broader filter/search concepts. The current TTC route and browser tests
must establish their actual availability and parameter semantics before use;
documented concepts alone did not yield a working complete-history partition.

## Next evidence needed before a full historical implementation

First run a whole-population coverage rehearsal using the verified smaller
positive queries and reconcile the unique notice union. Independently published
TTC reports can recover historical bid/award evidence where a supported match
exists. For remaining unexposed listings/details, obtain a supported historical
export/API, documented filter route or legitimate account entitlement. A precise question for the MERX technical contact is:

> Which supported export/API or account entitlement retrieves every historical
> TTC Closed, Bid Results and Awarded notice beyond the first 1,000 search results,
> including legacy notice abstracts such as 879704954? How should its totals and
> stable identifiers be reconciled?

No message was sent to MERX. The maintainer has been asked whether the supplied
authorization also came with an export/API route or account access. This is an
access-method question, not a request to repeat the reproduction authorization.

An eventual backfill must detect repeated/clamped pages, preserve stable notice
IDs, reconcile unique coverage against publisher counts, and explicitly report
partial coverage. Ordinary incremental HTTP capture remains technically feasible;
it should not be represented as a verified full backfill.

## Evidence and correction

Raw responses, safe request metadata, probe scripts and failed completeness
assertions are under `/home/alex/tb-data/operations/133/pagination-2026-10-24/`.
Browser HTML, screenshots and document/search traces are under
`/home/alex/tb-data/operations/133/playwright-pagination-2026-10-24/`.
No account credentials, authentication headers or cookie values are logged.

The initial reconnaissance mistakenly described its inherited browser-style
`config.USER_AGENT` as archive-identifying. That record has been corrected; these
follow-up requests and browser contexts explicitly identify the archive. The
follow-up made no production data changes, publication or paid model calls.
