# Waterfront Toronto own-site award publication (#108)

- **Confirmed:** 2026-10-22 by the project maintainer:
  > "We have explicit permission to scrape this data as long as we are respectfully accessing their servers."

## Scope and conditions

- **Included:** The public [Awarded Contracts page](https://www.waterfrontoronto.ca/opportunities/procurement/awarded-contracts) only (one anonymous HTTP GET per nightly run with an identifying `User-Agent` and bounded retries).
- **Attribution & fidelity:** Keep the source URL, published month, original wording, non-competitive/sole-source labels, and construction-manager attribution in exports. The page covers contracts above $50,000 and is not a record of all Waterfront spending.
- **Excluded:** Other Waterfront site pages, ACANs, and Bonfire documents/opportunities (Waterfront's Bonfire listing capture is covered separately in [`2026-10-22-bonfire.md`](2026-10-22-bonfire.md)).
