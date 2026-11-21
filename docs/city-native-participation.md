# Native-call participation in composite City reports

Pre-2013 Bid Committee reports often bundle several contracts under one council item. `linking/city_native_participation.py` and `linking/city_received_participation.py` apply reviewed participation records from `data/city_native_bid_reviews.json` for individual Call Numbers without merging sibling contracts into one procurement.

## Single-submission composite blocks (`linking/city_native_participation.py`)

Uses `extraction_cache` for candidate observations. Each new review requires the local PDF and pins the report, text, cache entry, contract indices, and the full text block from the Call Number heading to the next contract:

- Every cached contract naming that Call Number must be accounted for.
- The literal count field must confirm exactly one submission, and the same block must name one recommended bidder matching the cached supplier. Mixed/informal submissions, `No Bid` outcomes, and stage conflicts stay held.
- If the same supplier appears across multiple price schedules, all schedule qualifiers stay in the proof and produce one unpriced participant row in `bid` (no combined price, no extra firms). Model prices and compliance claims stay in the cache and are omitted from the participation row.

## Reviewed received-submission tables (`linking/city_received_participation.py`)

Validates reviewed tables and narratives listing received submissions for a native call:

- Accounts for every cached observation under that call, pins literal table sections and supplier name fragments (including split column headings), and keeps cached contracts, declared counts, and stage claims as withheld evidence.
- A bare supplier heading does not prove receipt. The council title must uniquely name the proven Call Number, and a conflicting call inside the receipt table rejects admission.
- Produces one unpriced participant per full legal supplier. Multi-lot offers stay listed separately in the proof with their scope labels. A literal `No Bid` row records non-participation for that lot and does not create a participant (unless the same firm has a separate submitted row). No price, compliance status, award, or other procurement stage is inferred. Conflicting legal names across source revisions stay held.

## Identity proof and persistence

- **Identity**: Established independently from the City's archived award publication (exact full supplier name, printed award date, and amount uniquely matching one record across the frozen and current populations and archived portal views). The PDF block must print the same Call Number. Numeric Call Number conversions and fuzzy supplier matches are not accepted.
- **Ledger**: `city_native_bid_evidence` stores the immutable proof JSON and hash. Removed or edited applied reviews, changed publisher facts, altered PDF/cache bytes, unaccounted observations, or changed participation rows abort replay before canonical changes commit. Already-applied evidence can replay without local PDFs; local PDFs are hash-verified when present.
- **Storage and export**: New `bid` rows carry the proven City document number and no council item reference (the council item and PDF keep their many-to-many links). Canonical resolution links only the reviewed Call Number to its City document, leaving sibling calls separate. JSON nests the unpriced bid and PDF under the City solicitation and exports the proof ledger; CSV and Parquet include `city_native_bid_evidence`.
