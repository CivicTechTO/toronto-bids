These are real captures, not authored example data. `awards.html` is the complete
anonymous HTTP response from
https://www.waterfrontoronto.ca/opportunities/procurement/awarded-contracts,
captured 2026-10-22 for #108 under the maintainer's explicit permission.

The JSON files retain exact monthly extraction inputs and actual responses
from the unchanged shared ExtractionClient on the same date, plus the prompt's
extractor_version. September 2017 covers nested supplier-first lists; October
2021 covers a title followed by four suppliers; July 2025 includes the published
malformed `$5,0000,000`. Additional months cover identical supplier/value pairs
on different published lines, parenthesized years in titles and company names,
and an attribution-only recipient hallucination. Tests check the source evidence rather than trusting the
model response alone. No credentials or request headers are retained.
