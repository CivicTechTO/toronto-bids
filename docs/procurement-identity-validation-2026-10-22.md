# Procurement identity migration validation (#124)

The full isolated migration and replay preserved every pre-existing source value and
stored 371 portal postings as 371 distinct procurement identities. SQLite integrity and
foreign-key checks passed. Production data was not modified by this rehearsal.

The [design](designs/2026-10-22-procurement-identity.md) defines the identity/evidence
contract; the [machine-readable validation](procurement-identity-validation-2026-10-22.json)
records each source table's row count/digest, identity coverage and replay checks.

## Dataset and checks

The database was copied using SQLite backup from `/home/alex/tb-data/bids.sqlite` into
`/home/alex/tb-data/operations/124-rehearsal/final/bids.sqlite`. The final run used all
249 saved TRCA and 122 saved Zoo public listing observations. It made no new portal
requests and no paid extraction calls. All 23 published tables were exported to JSON,
schema metadata, CSV and Parquet in that rehearsal directory.

Every old table was compared row by row using exactly its original columns, including
primary keys, raw payloads, stored hashes and first/last-seen timestamps. Only new
procurement columns/tables were added. The 610 agency solicitation rows, 1,011 agency
awards and 1,457 agency bids were all preserved. Existing refused extraction evidence
and withheld amounts were unchanged; creating IDs does not validate those claims.

Both portal datasets contain reused references. All 371 buyer/page identities survived,
with 371 distinct procurement IDs. Replaying them in reverse order retained all IDs,
keys and identity creation timestamps. Unique document/workspace/call anchors were
checked for accidental coalescing; none shared a procurement ID within a namespace.

The regression suite checks actual captured records, not just synthetic reference
strings. It also covers an identifier-less posting, collisions first observed on a later
run, overlapping values in separate City keyspaces, unlinked awards/bids/postings,
late source-proven joins and permanent redirects, rollback of contradictory evidence,
legacy URL enrichment that cannot prove a board/portal match, and PDFs/items describing
several procurements. A legacy database receives actual foreign-key constraints while
retaining its original row and timestamps. Ambiguous report nesting is refused rather
than choosing an arbitrary solicitation.

Reproduce against a pre-migration database copy, from the repository root:

```sh
scrapers/.venv/bin/python docs/procurement-identity-validation-2026-10-22/validate.py \
  --baseline /path/to/bids.sqlite --out-dir /path/to/new-rehearsal
```

The baseline opens read-only. The output directory must not already contain a database.
The script copies the database before migrating and records all validation results.

## Public export and deployment

Existing JSON sections remain. Single-procurement source facts carry `procurement_id`;
`procurements` contains active IDs and durable redirects (`merged_into`).
`procurement_keys` maps namespaced source identities. `procurement_documents` and
`procurement_council_items` express many-to-many evidence. Their equivalent SQLite
association tables use singular names.

New portal observations appear in `buyers[].portal_listings`, not in the legacy board
`solicitations` list. Clients displaying portal opportunities should consume that array,
using `procurement_id` or `portal_url` as the key; `native_ref` is nullable and nonunique.
This preserves old sections without pretending a shared label proves an equivalence.

Initialization adds the schema automatically. The normal sync resolves identities after
linking, and JSON export resolves again after any enrichment. The next permitted off-peak
nightly portal pass therefore stores distinct pages without the former repeated-reference
batch refusal. The normal capture grants, rate limits, raw recording and incomplete-page
failures remain in force. Status refresh stays with #140. No new fuzzy matching runs.
The existing TRCA download gaps and agency identity/amount refusals remain visible.

Example joins on the public SQLite database:

```sql
-- Every portal observation, including repeated or absent printed references.
SELECT b.slug, p.procurement_id, p.native_ref, p.title, p.portal_url
FROM portal_listing AS p JOIN buyer AS b ON b.id = p.buyer_id;

-- All stored evidence documents for a procurement, including multi-contract reports.
SELECT d.procurement_id, pdf.url, pdf.sha256
FROM procurement_document AS d JOIN background_pdf AS pdf ON pdf.url = d.url;

-- Stable old IDs continue to resolve after a source-proven equivalence.
SELECT id, COALESCE(merged_into, id) AS canonical_id FROM procurement;
```
