# Per-solicitation document index (#123)

## Problem

`ariba_attachment` (#121) records one row per file in each captured Ariba bundle, but only the
bundle's **top-level** entries — 48 of the 520 archived files are themselves zips
("Appendix C2 - Planning Documents.zip", "Base Information.zip") whose real contents (drawings,
planning documents) are invisible to the index. And the index is not surfaced anywhere: the
public export (`bids.json`) says nothing about what documents a solicitation contains.

Separately, the archive already holds **229 Award Summary Forms** (`background_pdf` where
`kind='award_summary'`, keyed on `document_number`) that are **orphaned from the export today**:
`export/document.py` buckets `background_pdf` by council `reference`, and award-summary rows have
`reference IS NULL`, so they attach to nothing and never appear in the artifact.

## Goal (this phase)

Expose, as public data, a **per-solicitation index of what documents we hold** — a listing, not
the content. Recursively expanded so the index is truthful about nested zips, and unified across
the corpora that cleanly join a solicitation. Full-text extraction, OCR, and search are
explicitly deferred to a later phase.

## Scope decisions (from brainstorming)

- **Recursive expansion, fully.** Nested zips are descended to any depth; every real leaf file
  appears in the index with a path that encodes its nesting.
- **Unified across the `document_number` keyspace only** — Ariba attachments (recursive) + Award
  Summary Forms. Both key on `document_number` and join a solicitation directly. Surfacing the
  award-summary forms fixes the 229-orphan gap as a side effect.
- **Staff reports stay where they are.** The 4,919 staff-report PDFs (`kind='bgrd'`) key on
  council `reference` with no `document_number`; a bridge via the `bid` table covers only ~47%
  and is one-to-many. Attaching them to solicitations would risk mis-attribution — worse than
  none. They remain nested under `council_items`, where the export already surfaces them.
- **A surrogate per-solicitation identity is the right eventual home for full unification but is
  out of scope here** (issue #124). This phase ships on `document_number` and is
  forward-compatible: when a surrogate `solicitation_id` lands, the `documents` index re-homes
  onto it with a mechanical migration and no rework of the extraction.
- **Expose the Award Summary Form's City URL** (`secure.toronto.ca/...`) — the only genuine
  public link in the set. Ariba attachment files carry no URL: their bytes live only in our
  archive and are not published.
- **The bytes are not published.** This is an index of what exists, not a download surface.

## Design

### 1. Storage — recursive index (`sources/ariba_attachments.py`, `store/`)

`ariba_attachment` gains a `path` column: the full nested path, e.g.
`Appendix C2 - Planning Documents.zip/drawings/site-plan.pdf`. `filename` stays the **leaf** name
(convenient for display); `path` is the row's real identity, because leaf names collide across
nested zips. The uniqueness key moves from `(document_number, filename)` to
`(document_number, path)`.

`index_zip` becomes **recursive**: when a central-directory entry is itself a zip, open it from a
`BytesIO` of its bytes and descend, prefixing the parent path. `file_size` and `crc32` come from
each level's central directory (no inflation of a 160 MB bundle for sizes). Only leaves are
indexed — an expandable container zip contributes its contents, not itself; its name lives in the
descendants' path prefix.

**Recursion safety.** A max depth and a max-total-entries-per-bundle cap bound zip bombs. A
nested zip that is corrupt, encrypted, or would trip a cap is recorded as a **single opaque
leaf** (its own path, flagged) rather than descended — so a bad appendix never costs a
solicitation the rest of its index.

**Rebuild from the bytes.** `ariba_attachment` is a *derived index of the on-disk zips*, not
primary archival data — so, exactly like the supplier dimension, it is rebuilt from scratch
rather than diff-upserted. Re-indexing a bundle clears that `document_number`'s rows and inserts
the recursive set. This is the deliberate exception to the archive's "rows are never deleted"
rule, and it is safe because the zip on disk is the source of truth. The same pass both migrates
the 33 existing bundles (which hold only top-level rows today) and defines the steady state:
`store_bundle` writes recursively on every future capture.

**Schema migration.** Additive `path` column via `_add_missing_columns`, plus swapping the unique
index `(document_number, filename)` → `(document_number, path)` in a `_rebuild_*` helper
following the existing `_rebuild_award_for_line_key` / `_rebuild_bid_for_nullable_reference`
patterns in `store/db.py`.

**Command.** A `tb enrich-ariba-attachments --reindex` mode rebuilds the index from the zips
already under `<DATA_DIR>/ariba/attachments/` — offline, no browser. It is the migration for
existing bundles and is safe to re-run any time.

### 2. Export — per-solicitation `documents` array (`export/document.py`)

In `build_export_document`, each solicitation gains a `documents` list (alongside the existing
`awards` and `ariba_postings`), unioning the two `document_number`-keyed corpora:

- **Ariba attachment files** — every leaf from the recursive `ariba_attachment` index.
- **Award Summary Forms** — `background_pdf` where `kind='award_summary'`, joined on
  `document_number`. First time these reach the artifact.

Each entry has a shared, minimal shape:

```json
{
  "source": "ariba_attachment",
  "name": "Appendix A - 34 Hanna Park Design Brief.pdf",
  "path": "Appendix C2 - Planning Documents.zip/drawings/site-plan.pdf",
  "type": "pdf",
  "size_bytes": 12656277,
  "url": null
}
```

- `source`: `"ariba_attachment"` | `"award_summary"`.
- `name`: leaf filename. `path`: full nested path (equals `name` for a top-level file; carries
  the zip prefixes for nested ones).
- `type`: lowercased extension (`pdf`, `xlsx`, `dwg`, …).
- `size_bytes`: nullable — award-summary rows carry no size.
- `url`: the City `secure.toronto.ca` link for `award_summary`; `null` for Ariba files.

Internal integrity fields (`crc32`, `sha256`, `zip_name`, `zip_sha256`) stay out of the public
export — archival, not useful downstream. The builder stays pure and deterministic: `ORDER BY`
on every query, no file I/O, consistent with the export-seam contract. A solicitation with no
held documents gets an empty array.

The nesting is **total by construction** — every `document_number` in `ariba_attachment` and in
the `award_summary` PDFs derives from the OData spine, so all of them match a `solicitation` row
(verified: 0 orphans on either side). Unlike `awards`/`ariba_postings`, there is no
`unlinked_documents` bucket to build, because nothing is unlinked. If that ever changes, a
non-matching document should follow the existing `unlinked_*` pattern rather than be dropped.

### 3. Testing

All offline and fixture-based — no browser, no network — as the rest of the suite.

- **Pure recursion** (`test_ariba_attachments.py`): a fixture zip-containing-a-zip asserts leaves
  surface with correct nested paths and sizes; a guard test asserts an over-cap / unreadable
  nested zip becomes a single opaque flagged leaf rather than raising.
- **Rebuild semantics**: re-indexing a bundle whose rows already exist replaces them (no
  duplicates, container-zip rows from the old top-level-only index are gone).
- **Export** (`test_export` or a new focused test): a solicitation surfaces its `documents`
  including a recursively-nested Ariba file (with `path`) and an award-summary form (with `url`
  and `source='award_summary'`); internal hash fields are absent; a document-less solicitation
  yields `[]`.

## Out of scope (explicit)

- **Full-text extraction, OCR, FTS** — a later phase (the original #123 exploration). This ships
  the index only.
- **Unifying staff-report / council-reference documents under solicitations** — needs the
  surrogate identity in #124.
- **Publishing the document bytes** — the index lists what exists; distribution is a separate
  decision.

## References

- #121 — the Ariba attachment archive this indexes (`sources/ariba_attachments.py`,
  `ariba_attachment` table).
- #124 — the surrogate per-solicitation identity this re-homes onto later.
- `store/db.py` — `_add_missing_columns`, `_rebuild_award_for_line_key`,
  `_rebuild_bid_for_nullable_reference` (schema-migration precedents);
  `linking/supplier.py:build_supplier_dimension` (rebuild-derived-layer precedent).
- `export/document.py` — the solicitation-centric builder and the `background_pdf`-by-reference
  bucketing that currently orphans the award-summary forms.
