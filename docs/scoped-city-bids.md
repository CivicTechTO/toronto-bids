# Scoped City bid responses

Some City reports list the same firm across multiple lots, categories, regions, or alternative offers. A single table cell is not a separate participating firm, and a lot price is not a procurement-wide total. See [GLOSSARY.md](../GLOSSARY.md) for terminology and [ADR 0001](adr/0001-scoped-bid-responses.md) for the schema decision.

## Schema and counts

Existing `bid` rows keep their keys, prices, and IDs. For reviewed scoped reports, `bid` stores one unpriced participation row per proven supplier per event and source, while three child tables hold the detailed breakdown:

- **`bid_scope`**: A source-labelled lot, part, category, or region (or an explicitly unspecified scope).
- **`bid_response`**: The literal party name, any source-labelled alternative, the response classification (`submitted`, `no_bid`, or unscoped outcome), scope-specific status, and an optional `bid_id` linking to the proven participant.
- **`bid_response_price`**: Individual quoted values, column headers, units, tax basis, and qualifiers. Percentage discounts and unit rates have a `NULL` currency-total numeric value.

Interpretation rules:

- A literal `No Bid` cell records non-participation for that scope. An empty cell means neither a submission nor a `No Bid`.
- Outcomes without a stated scope stay unscoped.
- Cancelled or unawarded lots can still have valid submissions.
- Unidentified party fragments stay visible (along with any supported prices) without inventing a firm identity.
- Exports report distinct firm counts and response-cell counts separately.

## Adding and replaying reviews

- `linking/city_scoped_bids.py` validates and applies reviews from `data/city_scoped_bid_reviews.json`.
- `pdf_witness.py` checks quoted text against original PDF word coordinates using Poppler. For scanned PDFs with image-space coordinates and OCR corrections, see [image-pdf-text.md](image-pdf-text.md).

To add a new review:

1. Establish procurement identity first via an accepted crosswalk or the source's primary document number.
2. Review the full source table and every raw cached model observation.
3. Record the report/text/cache/contract hashes, PDF witness rectangles, supplier identities (or unresolved fragments), leaf scopes, alternatives, prices, qualifiers, and outcomes. Supplier columns and scope rows must geometrically contain their response cells. Keep literal aliases even when legal-form normalization maps them to one firm, and record a reason for every cached observation that is replaced or withheld.

Runtime behavior:

- New reviews require the original PDF locally; missing or changed evidence aborts before any writes.
- Accepted reviews are stored with their full proof and digest in `city_scoped_bid_evidence`, allowing release consumers to replay without local PDFs while still verifying archived context and identities.
- Modifying or deleting an already-applied proof requires an explicit repair; do not delete ledger rows to bypass a mismatch.
- Both canonical resolution and explicit BA/BD backfills run the same validation and atomic rebuild, preserving IDs and `first_seen` timestamps and rolling back on late conflicts. The older scalar bid-review ledger is separate; overlapping scalar reviews need an explicit migration.

## Export

- JSON nests `bid_scopes` and `bid_scope_summary` under the City solicitation, along with response prices, participant names, and the proof ledger.
- CSV, Parquet, and the generated schema dictionary include all four tables.
- Canonical procurement identity attaches to `bid_scope`, with `bid_response` and `bid_response_price` linked by foreign key.
- The test-pinned review snapshot for the initial cohort lives in `docs/research/city-scoped-bid-review-2026-11-05.json`.
