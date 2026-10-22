# Waterfront Toronto own-site award publication

On 2026-10-22 the project maintainer explicitly confirmed in the implementation conversation: “We have explicit permission to scrape this data as long as we are respectfully accessing their servers.” This records the supplied authorization; it is not a claim that robots.txt grants a licence.

The #108 implementation is bounded to the public [Awarded Contracts page](https://www.waterfrontoronto.ca/opportunities/procurement/awarded-contracts). Each capture makes one ordinary anonymous HTTP GET using the project's identifying User-Agent. Existing bounded HTTP retries apply to transport/server errors; client errors stop. Monthly extraction uses the retained response and makes no additional requests to Waterfront. Nightly execution fetches the page once; unchanged monthly inputs reuse the extraction cache.

The initial investigation checked robots.txt and the linked privacy policy. The award page was accessible by ordinary HTTP without login or browser automation; robots.txt did not disallow it. No separate reuse licence was established from the privacy policy. Authorization for this implementation comes from the maintainer's explicit permission above.

Keep the source URL, published month and wording with exported observations. Preserve the source's non-competitive/sole-source labels and construction-manager attribution; the source's introductory default-competition statement is not a per-award method label. The page states that it lists public prequalifications/contracts above $50,000; this capture is not evidence of all Waterfront spending.

Bonfire opportunities/documents, ACANs, and other own-site pages remain outside this bounded capture and retain their separate scope/access questions (#108, #134). Do not extend this recorded grant to another platform or contact anyone on the maintainer's behalf.
