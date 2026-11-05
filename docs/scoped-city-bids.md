# Scoped City bid responses

City reports can list the same firm in several lots, categories, regions or alternatives. A response cell is not another participating firm, and its price is not a procurement-wide total. The vocabulary is in [GLOSSARY.md](../GLOSSARY.md); [ADR 0001](adr/0001-scoped-bid-responses.md) records the storage decision.

## Representation and counts

Existing `bid` keys, prices and IDs retain their meaning. For the reviewed scoped cohort, an unpriced `bid` records each proven firm's participation once per event and source. Three child tables retain the detail:

- `bid_scope` identifies a source's named part, lot, category or region, or explicitly unspecified scope.
- `bid_response` retains the literal party name, source-labelled alternative, submitted/No Bid/unscoped-outcome classification and scope-specific status. Its optional `bid_id` links a proven participant.
- `bid_response_price` retains individual quoted values, headers, units, tax basis and qualifications. Percentage discounts and unit rates have no currency-total numeric value.

A literal No Bid cell records nonparticipation in that scope. An empty cell establishes neither submission nor No Bid. An outcome with unstated scope stays unscoped. A cancelled or unawarded scope can still contain real submissions. Unknown party fragments remain visible, including supported prices, without a fabricated firm identity. Firm counts and source response-cell counts are separate in the export.

## Source review and extension

`linking/city_scoped_bids.py` is the admission boundary. `data/city_scoped_bid_reviews.json` holds immutable reviews; `pdf_witness.py` verifies literal rectangular quotations against original PDF words using Poppler. The shared extraction prompt and cached model results stay unchanged.

For another review, first establish procurement identity independently through an existing accepted crosswalk or the source's primary document number. Review the entire source table and every raw cached observation. Commit the report/text/cache/contract-collection hashes, original source rectangles, supplier identities and unresolved fragments, leaf scopes, alternatives, prices, qualifiers and explicitly bound outcomes. Supplier columns and scope rows must geometrically contain their own response cells. Keep all literal aliases even when existing legal-form normalization proves one firm. Record a reason for every cached observation that is replaced or withheld.

Preflight requires the original PDF for a new review. Missing or changed evidence aborts before writes. Once accepted, `city_scoped_bid_evidence` stores the complete proof and digest: release consumers can replay without local PDFs, but still check current archived context and identities. Changed or removed applied proofs require an explicit reviewed repair; deleting the ledger to bypass a mismatch is unsafe.

Resolution and explicit BA/BD backfills use the same preflight and persistence path. Participation and child rows rebuild atomically under their stable source keys, preserving IDs and `first_seen`. Late canonical conflicts roll back the entire resolution. The older scalar bid-review ledger remains separate; overlapping applied scalar reviews require an explicit migration.

## Export and deployment

JSON nests `bid_scopes` and `bid_scope_summary` under the proven City solicitation, with response prices and participant names. The accepted proof ledger is exported too. CSV, Parquet and the schema dictionary expose all four new tables. Canonical procurement identity attaches to the scope; responses and prices follow its foreign keys.

Deploy the companion frontend that renders these optional fields before publishing recovered data. It distinguishes firms from cells, labels percentage discounts and unknown tax bases, preserves alternatives and qualifications, and links supplier participation back to the scoped view without a global win/loss claim. Older exports remain supported by that frontend.

After merge, use the normal locked production deployment/recovery workflow to resolve, rebuild suppliers and export. Verify the cohort counts against the [validation record](research/city-scoped-bid-validation-2026-11-05.md), SQLite foreign keys, stable previous facts and the published site. This implementation's validation used a disposable production copy; it did not publish production data.
