# Reviewed City Call Number crosswalks

Historical council items and numeric City solicitation keys can refer to the same procurement under different identifiers. `linking/city_crosswalk.py` links them only through committed, reviewed source proofs in `data/city_call_crosswalk.json`. Original bid, award, and extraction payloads stay untouched; canonical resolution merges their procurement identities and records redirects and proof hashes.

## Proof classes

### Standard proof classes

- **`publisher_native_call`**: The City's award record names the same Call Number in its award-details field. The typed call must be unique, and the agenda, solicitation type, and issue/close dates must match.
- **`corroborated_compound`**: The agenda has one clear Call Number and named winner. Exact issue/close dates, solicitation type, full supplier name, and initial net-of-tax award amount must uniquely match one record in the complete publisher population. Punctuation and legal-suffix variants are allowed; spelling differences, locations, joint-venture splits, generic word overlap, date tolerances, and rounded prices are not.

### Review-annotated proof classes

These require explicit review annotations committed in the registry:

- **`reviewed_type_discrepancy`**: Records conflicting solicitation types between the agenda and the City feed. Exact issue/close dates, full supplier name, and initial net award must uniquely identify one record across all solicitation types in both the frozen and current populations. Raw types stay unchanged.
- **`reviewed_initial_award`**: Used when a combined title lists multiple winners, while a separately labelled first initial award names one full supplier and exact amount. Pinned quotations keep the match within one scope; later options, other winners, and combined totals do not qualify.
- **`reviewed_recommended_supplier`**: Uses a single `Recommended Bidder` / `Recommended Proponent` field directly before the first initial award (for abbreviated or long titles without inventing supplier aliases). Pins the full field and initial net award quotation, including trailing miscellaneous/allowance notes. Recommendation amounts establish identity only and never overwrite submitted bid prices.
- **`archived_publisher_award`**: Uses the City's old award portal as a separate primary source. The Call Number must be unambiguous in both the portal and the agenda, and the portal's HST-exclusive amount, full supplier name, and printed `Date Awarded` must uniquely match one record across the frozen population, archived views, and current award facts. `Date Awarded` is the printed award date, never a capture timestamp or council decision date. Original HTML is stored in `city_archived_award_sources.zip` alongside the compact award-date comparison population.

### Matching rules

- Accepted formatting variants: `Request of Quotation`, colonless date labels, ordinal day suffixes, `Closed on` labels, `Sept` abbreviations, and spaced Call Numbers. Exact dates and matching Call Numbers are still required.
- Equivalent net-tax labels: `net of applicable taxes` and `excluding all applicable taxes`. `Net of HST recoveries`, tax-inclusive amounts, and unlabeled prices are distinct and cannot be mixed. No rounding tolerance is allowed.
- Award amounts must come from the labelled initial-award section (no option-year values, tax conversions, or summed periods).
- Digit stripping and tender-number padding generate search candidates only and never approve a link. Derived titles, legacy matcher results, LLM caches, and `Parent_Id` cannot serve as independent proof.

## Generating and reviewing candidates

Save a snapshot of the City's paged OData solicitation records (with a `rows` array, request URLs, timestamps, and hashes).

Run discovery read-only from `scrapers/`:

```sh
uv run python -m toronto_bids.city_crosswalk \
  --database /path/to/bids.sqlite \
  --publisher-snapshot /path/to/original-odata.json \
  --agendas /path/to/council/agendas \
  --out /tmp/city-crosswalk-review
```

Pass `--candidates /path/to/frozen.json` to replay a frozen candidate list. The command writes candidate proposals, refusal reasons, and a review bundle outside the database without making network or model calls.

After reviewing proposals, update `data/city_call_crosswalk.json` and the compact gzipped publisher population (which stores identity fields for every publisher record so global uniqueness checks can run offline).

## Applying crosswalks and handling drift

The OData normalizer stores current publisher identity fields on `solicitation`:

- `publisher_identity_json`: current supplier/value pairs and publisher call details (empty arrays overwrite the projection while old `award` rows stay archived, so upstream removals are not hidden).
- `publisher_award_identity_json`: current nested supplier award dates and supplier/value pairs (for `archived_publisher_award` proofs).

Before resolving new crosswalks on a database that lacks these columns, run a full OData sync (or seed only the new column from a pinned official capture after checking record ID, document key, dates, and type).

During canonical resolution:

- The resolver rechecks current source fields, global uniqueness, agenda context, local agenda bytes (when present), and competing identities before writing new links.
- Already-applied proofs can replay on release databases without local agenda HTML as long as their canonical JSON and hash match. New approvals require the local original agenda.
- Changed contexts or missing registry entries fail resolution and require explicit review or repair. Do not delete registry entries or weaken checks to clear a failure.
- Accepted links write `solicitation_link.method = 'city_call_crosswalk'` and immutable rows in `city_crosswalk_evidence` (exported to SQLite, CSV, and Parquet), with proof class and hash in `procurement.merge_evidence`.
- Because canonical IDs and redirects are permanent history, undoing a bad merge requires an explicit repair rather than just deleting the link. Two earlier temporary holds have explicit supersession pointers to valid proofs so the resolver does not split and re-merge them.

## Reviewed cached City bids

`linking/city_cached_bids.py` applies `data/city_cached_bid_reviews.json` during canonical resolution and explicit BA/BD backfills. Each entry projects verified bids from an immutable `extraction_cache` entry without modifying the raw cache or extraction prompt.

Each review pins:

- Original PDF hash, extracted text hash, full cached extraction result, contract index, and council title.
- An independent procurement identity (accepted crosswalk for historical calls, or a primary `Doc` number for modern items; prequalification bids stay on the prequalification event).
- Literal table and row quotations from the PDF.

Rules:

- Wrapped supplier names and price footnotes come from bounded source rows. Inferred compliance and unsupported tax/price claims are withheld. Duplicate submissions, multi-lot tables, and contradictory document roles stay held until handled by [scoped-city-bids.md](scoped-city-bids.md).
- Hourly rates keep `/Hour` with a `NULL` numeric total. When a report declares more bids than it names, the discrepancy is recorded in the exported proof.
- New approvals require the original PDF locally (matching its pinned hash). Once applied, `city_bid_evidence` travels with the database for portable replay without local PDFs.
- Any changed source, cache, identity, applied proof, or conflicting link aborts before replacing bids; late canonical errors roll back both projections and evidence. Surviving bid IDs and `first_seen` timestamps are preserved.

Test-pinned review snapshots live in `docs/research/city-cached-bid-review-2026-11-05.json` and `docs/research/city-identity-holds-review-2026-11-05.json`.
