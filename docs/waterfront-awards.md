# Waterfront Toronto public awarded contracts (#108)

`tb capture-waterfront-awards` archives Waterfront Toronto's public monthly awarded-contracts HTML page and runs the shared Claude extraction model on uncached months (`--limit N` caps new model calls; `--limit 0` captures HTML and replays existing cache offline). Unfinished or unsupported months exit non-zero. The same step runs isolated in `tb nightly` as `waterfront awards`.

Access is covered by [permissions/waterfront-toronto.md](permissions/waterfront-toronto.md). Bonfire listings, current opportunities, vendor-of-record lists, and Advance Contract Award Notices (ACANs, which are intended awards rather than awarded contracts) are outside this adapter.

## Capture and parsing

- Fetches only the public awarded-contracts page and saves raw HTML to `<DATA_DIR>/waterfront/<sha256>.html` before parsing, even if parsing fails.
- The HTML parser checks the expected year/month structure and verifies that visible year-body text is preserved during normalization, without writing brittle supplier/value regexes.
- **`award_publication`** stores each visible text block with its publication month, position, URL, snapshot hash, and raw method/attribution notes. Each block's stable key hashes `(month, normalized text, occurrence index)` so unrelated edits elsewhere on the page do not rekey existing rows. Changed text adds a new observation; removed text stays archived.

## Extraction and verification

- Months are extracted as units (so nested supplier lines keep the project title above them) using `extraction_cache` and the shared prompt.
- **`published_award`** holds verified supplier/value claims matched to a single block in that month (using the extracted title to disambiguate identical pairs on different lines):
  - Construction-manager attribution ("awarded by X on behalf of Waterfront") is excluded from recipient matching while kept on the block.
  - Raw values keep surrounding parenthetical qualifiers (such as weekly rates or trade-package notes). Strict numeric parsing leaves malformed values (like `$5,0000,000`) and qualified rates as `NULL`.
  - No award day, bid, confidential flag, native reference, or funding fraction is invented.
- **`publication_extraction`** tracks pending months, unmatched dollar figures, and unsupported assignments. Every dollar amount in a month must be accounted for, including multiple awards in one block. Supported claims survive even if another claim in the same month is rejected, and an incomplete refresh never deletes prior claims. A fully verified refresh replaces derived claims for its observed blocks.

## Independently corroborated recipients

When the awarded-contracts page omits a winner, `linking/publication_evidence.py` can supply a reviewed recipient from another official Waterfront publication (binding the full monthly input/result and award position without altering the raw cache or primary amount; evidence lives in `data/publication_award_evidence.json`). Exported claims include `evidence_json` with the source URL, PDF hash, and page. Source or context drift requires re-review.

## Identity and export

- Each verified claim gets a namespaced procurement identity (no fuzzy merging with Bonfire or board records), and accepted supplier names flow into the canonical `supplier` table.
- Exports include `award_publications`, `published_awards`, and `publication_extractions` under `buyers[]` in JSON and as tables in CSV, Parquet, and the schema dictionary.
- Totals stay separate from City-spine awards. Waterfront Toronto is a tri-government corporation; no Toronto funding share is inferred.
