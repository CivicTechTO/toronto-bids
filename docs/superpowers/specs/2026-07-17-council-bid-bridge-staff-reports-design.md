# Council↔solicitation bid-bridge: surface staff reports in the documents index (#126)

## Problem

#123 built a per-solicitation `documents` array in the export but could only include the corpora
that key on `document_number` (Ariba attachments, Award Summary Forms). Staff-report PDFs
(`background_pdf` where `kind='bgrd'`, 4,919 rows) key on council `reference` and had no clean
join to a solicitation, so #123 left them under `council_items` only. This is a real gap:
staff reports are, per the design spec, "the richest award context."

## The bridge

Ariba-era Bid Award Panel agendas (2019+) name **both** the council `reference` and the Ariba
`document_number` for an item, so a single `bid` row carries both. That is an **exact** link —
parsed from one agenda item — with no fuzzy matching and no false-positive surface (the opposite
of the closed #77's supplier+amount route, and lower-risk than the deferred #124 spine).

```sql
SELECT DISTINCT reference, document_number FROM bid
WHERE reference IS NOT NULL AND document_number IS NOT NULL;
-- 1,759 links. Verified: 0 references map to >1 document_number (clean on the reference side);
-- 47 document_numbers have several references (an award plus later amendments — expected).
```

## Goal

Fold the staff-report PDFs reachable through this bridge into #123's per-solicitation `documents`
array. Measured payoff: **1,310 staff reports across 1,237 solicitations**.

## Scope decisions (from brainstorming)

- **Surface the reports, not just the link.** The deliverable is the visible export change, not a
  bare bridge table.
- **Derive the bridge at query time — no new table.** The `bid` table is the source of truth;
  materializing a `council_solicitation` table would duplicate it and need rebuilding when `bid`
  changes. `export/document.py` reads the map directly, fitting the archive's derived-layer
  pattern.
- **Ariba-era only, by nature.** Pre-2019 council items carry no dual-key `bid` row and still
  won't join — that remains the eventual #124 spine's job, not this change's.
- **New issue (#126), not reopening #77** — #77 was the supplier+amount route for pre-Ariba
  *titles* and is closed; this is a different mechanism for a different purpose (unification).

## Design

Entirely within `export/document.py`'s `build_export_document` (pure, deterministic — every query
`ORDER BY`, no file I/O), extending the `documents_by_doc` assembly #123 added.

1. **Read the bridge** into a `reference → document_number` dict:

   ```sql
   SELECT DISTINCT reference, document_number FROM bid
   WHERE reference IS NOT NULL AND document_number IS NOT NULL
   ORDER BY reference, document_number
   ```

   The reference side is 1:1 (verified), so a plain dict keyed on `reference` is exact; if a
   future row ever broke that, the last write wins deterministically because of the `ORDER BY`.

2. **Append bridged staff reports** to `documents_by_doc`. For each
   `background_pdf WHERE kind='bgrd' ORDER BY reference, url`, resolve its `reference` through the
   bridge to a `document_number`; if that document is a solicitation, append an entry:

   ```json
   {
     "source": "staff_report",
     "name": "backgroundfile-99644.pdf",
     "path": "backgroundfile-99644.pdf",
     "type": "pdf",
     "size_bytes": null,
     "url": "https://www.toronto.ca/legdocs/mmis/2017/ba/bgrd/backgroundfile-99644.pdf"
   }
   ```

   `name`/`path` are the URL basename (all 4,919 bgrd URLs end `.pdf`, verified); `url` is the
   public legdocs link (exposed, like the Award Summary Form's City URL); `size_bytes` is null.
   The existing `_ext` helper yields `type` `"pdf"`.

3. The solicitation loop's `sol["documents"] = documents_by_doc.get(doc, [])` line is unchanged —
   staff reports now arrive in the same bucket alongside Ariba files and award-summary forms.

Nothing else changes: `council_items` keeps its own `background_pdfs` nesting (a staff report can
appear both under its council item and under its bridged solicitation — different views of the
same PDF, neither wrong).

## Why it is safe

The link is **exact** — both keys come from the same `bid` row, parsed from one agenda item — so
there is no false-positive risk to measure. This is the property that distinguishes it from #77
(supplier+amount, which needed a calibrated false-positive rate) and lets it ship without the
matching-rules scrutiny #124 will need.

## Testing

Offline, fixture-based, in `tests/test_export_document.py` (extending #123's document tests):

- **A bridged staff report surfaces under the right solicitation.** Seed a solicitation, a `bid`
  row carrying both its `document_number` and a council `reference`, and a `background_pdf`
  (`kind='bgrd'`) on that reference. Assert the solicitation's `documents` contains an entry with
  `source="staff_report"`, the URL basename as `name`, `type="pdf"`, and the legdocs `url`.
- **An unbridged staff report stays out.** A `background_pdf` whose `reference` has no dual-key
  `bid` row does not appear under any solicitation's `documents`.
- **Determinism / no regression.** Existing #123 export tests still pass (documents just gains
  staff-report entries where a bridge exists); a solicitation with no bridged reports is
  unaffected.

## Out of scope

- Pre-2019 council items (no dual-key bid row) — future #124 spine.
- Any change to the `council_items` section or the `bid`/`background_pdf` tables.
- Full-text of the staff reports (`background_pdf.text` already exists; not surfaced here).

## References

- #123 (PR #125) — the `documents` array this extends; this branch rebases onto main after it merges.
- #124 — the deferred surrogate spine that will unify the pre-2019 remainder.
- #77 — closed; the supplier+amount route for pre-Ariba titles (different mechanism).
- `export/document.py` — `build_export_document`, the `documents_by_doc` assembly, `_ext`.
