# Procurement identity (#124)

Each procurement gets an archive-owned integer ID in `procurement`. Publisher identifiers stay on their source tables as ingest keys or labels, and nullable `procurement_id` foreign keys join across sources without discarding unmatched records. Supplier linkage and dollar amounts remain independent.

## Identity and evidence rules

- **Stable IDs:** `procurement` IDs are allocated once and never rebuilt or reused.
- **Namespaced keys (`procurement_key`):** Binds source identities across namespaces (City document, workspace, and call keys; accepted legacy agency keys; standalone Ariba event IDs; buyer-scoped portal URLs). Identical strings in different namespaces never imply equivalence.
- **Portal pages (`portal_listing`):** Keyed by `(buyer, page URL)` with nullable, non-unique `native_ref` labels. Portal listings never join board contracts on reference text alone, even if a reference happens to be unique in one fetch.
- **Explicit evidence only:** Resolution uses only existing explicit links: a posting's published document number, a dual-key bid, or a reviewed `solicitation_link` / crosswalk / alias. No fuzzy matcher runs. A council reference with conflicting document links never merges those documents.
- **Many-to-many evidence:** Background PDFs and council items can cover multiple procurements and link through association tables.
- **Merges and redirects:** When new proof links two existing IDs, the older ID survives and the newer one becomes a durable redirect with its evidence preserved. Distinct City document, workspace, call, or accepted agency anchors cannot merge (except via a reviewed document alias). Contradictory evidence aborts the resolution transaction.
- **Refusals stay refusals:** Allocating an ID to an observation does not override extraction identity refusals or validate unsupported monetary claims.

## Migration and export

- Migration adds the identity tables and nullable `procurement_id` columns while preserving all existing rows, keys, `first_seen` timestamps, and raw identifiers.
- Resolution runs after sync linking passes and again before export (so nightly enrichment is included). All identity, association, and redirect updates are transactional and idempotent.
- JSON exports keep all existing top-level sections and add `procurement_id` on single-procurement rows, `buyers[].portal_listings`, PDF/item associations, and redirects. SQLite, CSV, and Parquet export the same tables and foreign keys.
