# Waterfront Toronto fixtures (#108)

- `awards.html`: Complete anonymous HTTP response from `https://www.waterfrontoronto.ca/opportunities/procurement/awarded-contracts`, captured 2026-10-22. No credentials or request headers are included.
- JSON files: Monthly extraction inputs, actual `ExtractionClient` responses from the same date, and the prompt's `extractor_version`. Covered cases include nested supplier-first lists (September 2017), one title with four suppliers (October 2021), a published malformed amount `$5,0000,000` (July 2025), identical supplier/value pairs on separate lines, parenthesized years in titles and firm names, and an attribution-only recipient error caught by source verification.
