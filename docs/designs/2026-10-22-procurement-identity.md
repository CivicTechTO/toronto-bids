# Procurement identity (#124)

A procurement has an archive-owned integer ID. A publisher's identifiers are labels or
source keys, rather than the primary key of the archive's identity model. Existing source
tables keep their ingest keys and public sections; nullable procurement foreign keys add a
common join across them. Source observations are preserved even when no cross-source match
can be justified. Supplier linkage and monetary facts remain independent.

## Identity and evidence

`procurement` IDs are allocated once and never rebuilt or reused. `procurement_key` binds
namespaced source identities to them: City document/workspace/call keys, accepted legacy
agency keys, standalone Ariba event IDs, and buyer-scoped portal URLs. Equal strings in
different namespaces never imply equality. Portal references live in `native_ref` and can
be NULL or repeated; each distinct buyer/page URL is captured in `portal_listing`.

The resolver records only existing evidence: a posting's published document number, a bid
carrying both identifiers, or an existing reviewed `solicitation_link`. It adds no new
supplier/title/amount matcher. A council reference with conflicting document links is not
used to merge those documents. Background PDFs and council items are many-to-many evidence,
not necessarily procurements themselves. Dedicated association tables express that.

When a new proven relationship joins two existing IDs, the older ID survives and the other
becomes a durable redirect, with the relationship evidence retained. Distinct document,
workspace, call or accepted agency anchors cannot merge. Contradictory evidence fails the
resolution transaction rather than changing established identities. Identifier-less source
records can receive an ID from their stable source observation without inventing a bid number.

Portal postings do not join board contracts on reference text alone, even when a reference
happens to be unique in today's fetch. A portal URL on an old board row may itself have
come from reference-only backfill, so it is not independent evidence. Portal-origin legacy
rows can migrate to their source page identity; board facts remain separate, inspectable records. Extraction
identity refusals remain refusals: allocating an ID does not validate an extracted identity
or resolve unsupported monetary claims.

## Migration and export

Migration adds tables and nullable foreign keys, preserving every source row, primary key,
first-seen timestamp and raw identifier. Existing legacy agency listing rows stay intact;
new portal observations use their own table and never overwrite a board row. The resolver
runs after source linking and before export, so enrichment performed after sync is included.
Resolution, associations and key/redirect changes are transactional and repeatable.
Portal metadata refreshes from the latest non-null source values (#140); IDs and raw history persist.

JSON retains existing top-level sections and adds the procurement index and keys. Existing
single-procurement facts expose `procurement_id`; buyers expose separate `portal_listings`.
PDF/item associations and redirects are exported explicitly. SQLite/CSV/Parquet publish the
same tables and foreign keys. Existing clients keep their sections; clients that need all
portal postings must consume `buyers[].portal_listings`, using procurement IDs or portal URLs
rather than treating `native_ref` as a unique key.

## Acceptance evidence

Replay the complete saved 249 TRCA and 122 Zoo listings with all reused references. Every
buyer/page pair must remain distinct, including collisions introduced on later runs. Replays
must retain stable IDs and first-seen timestamps. Test identifier-less listings, same printed
values in different keyspaces, proven late joins/redirects, contradictory evidence rollback,
multi-procurement report/item associations, legacy database migration, and exported joins.
Validate on a full isolated production copy with all pre-existing source data unchanged.
