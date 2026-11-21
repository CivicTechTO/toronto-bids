# Glossary

Core terms used across the procurement archive and its documentation.

## Bidding

- **Participation**: Published evidence that a supplier submitted an offer to a procurement, including an offer that was later rejected or disqualified. One supplier can participate through multiple scoped or alternative submissions.
- **Bid scope**: A source-labelled lot, part, category, subcategory, or region to which an offer or outcome applies. If the source does not state a scope, it stays unstated.
- **Submission**: A concrete offer for a bid scope, possibly one of several alternatives from the same supplier. Distinct from the firm itself and from price variants of the same offer.
- **Response observation**: A source record of a submission, an explicit "No Bid" entry, or an outcome whose scope is unstated. A "No Bid" cell does not count as a submission.
- **Price variant**: A quoted value together with its source basis, unit, and qualifiers (such as a one-year total, an all-option-years total, or a percentage discount off list price). Price variants are not separate submissions.

## Core archive terms

- **Solicitation**: A competitive procurement opportunity (RFT, RFP, RFQ, etc.).
- **Spine**: The City's OData solicitation feed (`feis_solicitation_published`), which is the authoritative record for competitive City procurements.
- **Award line**: One row in the `award` table. A single solicitation can have multiple award lines for the same supplier (for example, standing-offer call-ups).
- **Keyspace**: A distinct identifier system across sources: 10-digit City `document_number`, non-competitive `workspace_number`, pre-2013 `call_number`, and agency `(buyer_id, native_ref)` or portal page URLs.
- **Linking pass**: A post-sync step that connects records across tables (such as bridging Ariba postings to solicitations or building the canonical `supplier` table).
- **Placeholder title**: A solicitation title where the City published the document number itself (such as `Doc-3524228095`) instead of a real description. Stored as `NULL` so real titles can be backfilled.
- **Ground truth**: Human-labelled evaluation data stored in `docs/ground-truth/`. Never mixed with machine-generated labels (`docs/machine-labels/`).
