# MERX reconnaissance (#133 / #104) — 2026-10-24

**Recommendation: use a paced `httpx` client and HTML parsing. Playwright is not
required for any sampled listing, detail, bid-result or award path.** The public
results/award tabs use small AJAX responses containing quoted HTML; they do not
require a browser JavaScript engine. Ordinary full-page tab links alone did not
select those tabs in the tested HTTP requests.

This is reconnaissance, not an implemented or enabled adapter. Production was
unchanged. Twenty bounded technical HTTP GETs all returned 200, without account
login, browser use, POSTs or model calls. Only TTC and one Police profile were
technically sampled; the other #133 buyer profiles still need individual checks.

## What actually works over HTTP

| Capability | Direct observation | Primary source |
| --- | --- | --- |
| TTC current listings | 10 notice links, titles and dates in server-rendered HTML | [TTC profile](https://www.merx.com/ttc) |
| Closed history | Advertises 2,938 records; pages 1 and 2 each have 25 distinct notices, with no overlap | [Closed](https://www.merx.com/ttc/solicitations/closed-bids?selectedContent=BUYER) |
| Bid-result discovery | Advertises 1,967 records; first page has 25 notice links | [Bid Results](https://www.merx.com/ttc/solicitations/bidresults-bids?selectedContent=BUYER) |
| Award discovery | Advertises 1,921 records; first page has 25 notice links | [Awarded](https://www.merx.com/ttc/solicitations/awarded-bids?selectedContent=BUYER) |
| Notice details | Named fields include public reference, buyer unit, solicitation number/type, publication and closing dates | [Sample notice](https://www.merx.com/ttc/ttcpurchasingandsales/solicitations/REQUEST-FOR-BIDS-FOR-THE-SUPPLY-OF-AXLE-BID-NO-T06BI26617/0000333265) |
| Bidder results | Eight named source containers: three prices and five No Bid observations | [Public result fragment](https://www.merx.com/public/solicitations/4109202665/abstract/bid-results) |
| Award details | Explicit supplier, value, award date and separate publication date | [Public award fragment](https://www.merx.com/public/solicitations/4076848809/abstract/award) |
| Original print document | Anonymous GET returns an actual PDF, containing six named bidders and amounts | [Sample print PDF](https://www.merx.com/ttc/ttcpurchasingandsales/solicitations/REQUEST-FOR-BIDS-FOR-THE-PUBLIC-WASHROOM-CLEANING-AT-VARIOUS-TTC-SUBWAY-STATIONS/0000292222/print) |
| Cross-buyer control | Police profile returns the same general server-rendered listing structure, with four current notices | [Police profile](https://www.merx.com/torontopolice) |

## The simplest technical path

1. Fetch the buyer-scoped listing tabs and follow their advertised page links.
2. Deduplicate public MERX notice references across tabs before fetching details.
3. Read each detail page's advertised `data-ajax-url` for results/award content.
   Do not calculate or guess the internal numeric identifier.
4. GET that route with the normal `X-Requested-With: XMLHttpRequest` header.
   A fresh anonymous client returned the sample results payload successfully.
5. Validate the narrow response envelope, decode its quoted HTML string without
   executing JavaScript, then parse the resulting named fields and bidder blocks.
   Refuse unfamiliar envelopes or login/error responses instead of returning zero.
6. Keep original responses and parent-to-fragment provenance. Print PDFs provide
   an additional original-document path where needed; routine structured-field
   extraction can avoid both PDF rendering and paid models.

The tested AJAX response is `text/javascript`, with a jQuery command assigning a
quoted HTML string to `#innerTabContent`; it is **not JSON**. Without the XHR header,
the same route included a larger page shell. A bounded literal-only decoder
successfully recovered the payload. No remote script was evaluated. Conversely,
GETs to the ordinary `?innerTabId=...` tab links returned the default notice body
in the two tests, so relying on those links alone would miss results.

Each bidder's heading and Bid Amount field share a `bidResultItems` container.
Parse those together; do not zip independent name/price lists. Preserve No Bid as
an observation, with no invented zero price or compliance decision. The award
sample explicitly names the supplier and value; a minimum bid is not award proof.
Award publication date and award date are separate fields. Keep currency, tax and
pricing qualifiers only where the publisher supplies them.

## Historical completeness is unresolved

A [subsequent HTTP and Playwright investigation](merx-pagination-followup-2026-10-24.md)
confirmed page clamping and tested candidate partitions. It has not established a
complete historical retrieval method; that follow-up supersedes the initial
pagination observations below.

All three TTC historical tabs advertise more than 1,000 notices, but their sampled
pagers expose only 40 options at 25 per page. The last advertised Closed page
(page 40) returned 25 notices, closing in late 2021/early 2022, and **no Next link**.
Only Closed pages 1, 2 and 40 were fetched; this was not a 1,000-record crawl.

This is an apparent 1,000-result window, not proof that the advertised history has
been fully captured. Its cause, subscription implications and an authorized path
to the rest of the history remain unestablished. No out-of-range page numbers or
unadvertised search partitions were probed. A completeness guard must compare
observed coverage against publisher totals. Provider-supported historical export,
API access or documented filtering should be investigated before claiming a full
backfill. A browser does not by itself establish access to missing history.

Also, hidden empty-state rows/messages coexist with real results in held HTML.
Parse actual notice containers and the selected tab, rather than treating an
empty-state phrase anywhere in the page as authoritative.

## TTC meanings and identities

TTC's own [Solicitations and Results](https://www.ttc.ca/doing-business-with-the-ttc/Procurement-and-Category-Management/solicitations-and-results)
page distinguishes opening results from later evaluation and awards. RFP result
publication can contain names/closing dates without prices. The sampled MERX
result fragments and print document label their publication as unofficial.
The [TTC procurement policy](https://www.ttc.ca/transparency-and-accountability/policies/Materials-and-Procurement-Policies/procurement-policy)
also distinguishes opening, evaluation and award stages. The adapter must retain
that distinction before adding these rows to any combined bid view; the current
agency bid shape does not explicitly encode evaluation stage.

The sample notice has three distinct identifiers:

- Public MERX reference: `0000333265`.
- TTC solicitation number: `T06BI26617`.
- Internal fragment-route identifier: `4109202665`.

Preserve leading zeroes and namespace these identifiers. Their lengths do not make
them City document numbers. Canonical notice URLs contain title slugs, so printed
references or changing titles are not sufficient archive identity. TTC's parent
profile includes both PCM Operating and PCM Capital notices; capturing only one
child would narrow coverage accidentally.

## Bonfire can complement MERX

A freshly fetched [TTC Capital notice](https://www.merx.com/ttc/solicitations/open-bids/R52PW26731-UNIFORM-MODERNIZATION-PROJECT/0000334795)
explicitly directs submissions to `https://ttc.bonfirehub.ca/opportunities/112287`.
Other sampled Operating notices describe MERX electronic submission instead.
TTC's [2024 EAM procurement report](https://pw.ttc.ca/-/media/Project/TTC/DevProto/Documents/Home/Public-Meetings/Board/2024/September-24/9_Procurement_Authorization_Enterprise_-Asset_Management_System_Implementation_Services.pdf)
also documents advertising on both platforms.

The existing Bonfire adapter may be a cheap additional source for TTC listing
metadata after its profile is checked. It does not establish parity with MERX's
results, awards or historical archive. Only explicit published cross-links can
bridge the different platforms' identifiers.

## Access conditions and supplied authorization

The fresh [robots file](https://www.merx.com/robots.txt) requests a five-second crawl
delay, excludes account/action routes and several query patterns, and separately
blocks named AI agents. After reading it, technical probes used at least five
seconds between requests. Correction during follow-up: the initial probes inherited the repository's
browser-style `config.USER_AGENT`; they did not use the archive-identifying
User-Agent originally claimed here. No browser or challenge bypass was used.
Follow-up pagination probes explicitly identify the Toronto Bids archive. The advertised pager uses
`pageNumber`, while robots names `page`/`pageSize`/`sort` patterns; that mismatch is
not treated as permission for unrestricted pagination or facet crawling.

The freshly fetched official [supplier agreement](https://www.merx.com/cms-view.jsa?page=%2Fcms%2Fblocks%2Fterms-conditions-en.jsp)
contains sections 3.1/3.2 restricting information reuse, redistribution and
third-party dissemination, with written consent provisions. The footer-linked
[terms route](https://www.merx.com/public/terms-conditions) returned a contents menu
without the substantive clauses in the inspected HTTP body. Older search caches
show different section numbers; they were not used as the current wording.

After this reconnaissance, the maintainer confirmed receiving authorization to
reproduce the MERX data in the context of open data. The supplied authorization is
recorded in [the MERX permission record](permissions/2026-10-24-merx.md), resolving
the project-specific reproduction question. It is separate from the Bonfire
permission and is not inferred from robots directives or anonymous accessibility.
The planned capture remains scoped to public data with respectful pacing/backoff.

No documented public bulk API/RSS/export was located in the limited official-document
search; this does not prove that a provider-supported feed or export is unavailable.

## Recommended first implementation slice

Build a TTC-only HTTP proof of concept using held fixtures: current listing
metadata, one results fragment and one award fragment, with stable IDs, explicit
result stage, fail-closed coverage checks and five-second pacing/backoff. Preserve
the existing City spine. Use the recorded MERX authorization and retain explicit
coverage limits while full-history access is investigated. Add other buyers only after each
profile has been verified; Police provides one positive structural control.

Evidence and reproducible probe/verification scripts are retained under
`/home/alex/tb-data/operations/133/recon-2026-10-24/`. `verification-summary.json`
records the 20 successful HTTP responses, source hashes, page uniqueness and
container-bound bidder checks. No scraper implementation, production data change,
publication, issue update or model charge was made by this reconnaissance. The
subsequent maintainer authorization is recorded above.
