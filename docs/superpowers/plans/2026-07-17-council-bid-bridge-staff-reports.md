# Council bid-bridge staff reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold staff-report PDFs into #123's per-solicitation `documents` export array, joined to solicitations through the exact council-`reference` ↔ `document_number` link the `bid` table already carries.

**Architecture:** A read-only extension of `export/document.py`'s `documents_by_doc` assembly. Derive a `reference → document_number` map from the `bid` table at query time (no new table), then attach each `kind='bgrd'` staff report to its bridged solicitation. Pure and deterministic.

**Tech Stack:** Python 3.12+, `uv`, SQLite, pytest.

## Global Constraints

- Python **3.12+**, `uv`-managed. Run tests with `uv run pytest` from `scrapers/`. No lint/format/typecheck.
- `build_export_document` is **pure and deterministic**: every query `ORDER BY`, no file I/O.
- Document entry shape is EXACTLY `{source, name, path, type, size_bytes, url}` — no other keys, no internal fields.
- The bridge is **derived at query time** from `bid` — do NOT add a table.
- The link is exact (both keys from one `bid` row); **Ariba-era only** by nature — pre-2019 council items have no dual-key bid row and are out of scope.
- Branch `feat-bidbridge-staff-reports` sits on top of #123 (branched from `feat-123-document-index`); it rebases onto `main` after PR #125 merges.

---

### Task 1: Surface bridged staff reports in the `documents` array

**Files:**
- Modify: `scrapers/toronto_bids/export/document.py` (add a third loop to the `documents_by_doc` assembly, after the award-summary loop at lines 79-88, before `solicitations = []` at line 90)
- Modify: `scrapers/tests/test_export_document.py` (add `Bid` to the model import at lines 6-13; add two tests)

**Interfaces:**
- Consumes: `sol_docs` (the set of solicitation document numbers, already built at `document.py:45`), `_ext` (helper), the `bid` and `background_pdf` tables.
- Produces: each solicitation's `documents` list additionally contains `{"source": "staff_report", ...}` entries for staff reports whose council reference bridges (via `bid`) to that solicitation.

- [ ] **Step 1: Write the failing tests**

Add `Bid` to the existing model import block in `scrapers/tests/test_export_document.py` (lines 6-13, which already imports `AribaAttachment, AribaPosting, Award, BackgroundPdf, NonCompetitive, Solicitation, ...`) — insert `Bid,` alphabetically. Then add these two tests (the `seeded` fixture's solicitation is `document_number="5672751291"`):

```python
def test_staff_report_surfaces_under_solicitation_via_bid_bridge(seeded):
    # An Ariba-era bid row carries BOTH the council reference and the document_number.
    db.upsert_row(seeded, Bid(bidder_name_raw="Acme Co", reference="2020.BA5.3",
                              document_number="5672751291", bid_price="1000",
                              source="bid_award_panel"), overwrite=True)
    db.upsert_row(seeded, BackgroundPdf(
        url="https://www.toronto.ca/legdocs/mmis/2020/ba/bgrd/backgroundfile-99644.pdf",
        reference="2020.BA5.3", kind="bgrd"), overwrite=True)
    seeded.commit()

    sol = next(s for s in build_export_document(seeded, generated_at="t")["solicitations"]
               if s["document_number"] == "5672751291")
    report = next(d for d in sol["documents"] if d["source"] == "staff_report")
    assert report["name"] == "backgroundfile-99644.pdf"
    assert report["path"] == "backgroundfile-99644.pdf"
    assert report["type"] == "pdf"
    assert report["size_bytes"] is None
    assert report["url"] == "https://www.toronto.ca/legdocs/mmis/2020/ba/bgrd/backgroundfile-99644.pdf"
    assert set(report) == {"source", "name", "path", "type", "size_bytes", "url"}


def test_unbridged_staff_report_stays_out_of_documents(seeded):
    # A staff report whose reference has no dual-key bid row must not attach to any solicitation.
    db.upsert_row(seeded, BackgroundPdf(
        url="https://www.toronto.ca/legdocs/x/backgroundfile-1.pdf",
        reference="2020.XX9.9", kind="bgrd"), overwrite=True)
    seeded.commit()

    for s in build_export_document(seeded, generated_at="t")["solicitations"]:
        assert not any(d["source"] == "staff_report" for d in s["documents"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_export_document.py -k "bid_bridge or unbridged_staff" -v`
Expected: FAIL — `test_staff_report_surfaces...` raises `StopIteration` (no `staff_report` entry yet); `test_unbridged...` passes trivially (also fine — it must keep passing after the change).

- [ ] **Step 3: Implement the bridge + staff-report loop**

In `scrapers/toronto_bids/export/document.py`, immediately after the award-summary loop (which ends at line 88, the closing `})` of the `award_summary` append) and before the blank line preceding `solicitations = []` (line 90), insert:

```python
    # Staff reports join a solicitation through the bid-bridge (#126): an Ariba-era bid row
    # carries BOTH the council reference and the document_number, so the link is exact — no
    # fuzzy matching. Derived from `bid` at query time; the reference side is 1:1 (verified), so
    # a plain dict is exact and the ORDER BY makes any future many-to-one deterministic.
    bridge: dict[str, str] = {}
    for row in _rows(conn, "SELECT DISTINCT reference, document_number FROM bid "
                           "WHERE reference IS NOT NULL AND document_number IS NOT NULL "
                           "ORDER BY reference, document_number"):
        bridge[row["reference"]] = row["document_number"]
    for report in _rows(conn, "SELECT reference, url FROM background_pdf "
                              "WHERE kind='bgrd' ORDER BY reference, url"):
        doc = bridge.get(report["reference"])
        if doc in sol_docs:                           # attach only to a real solicitation
            name = report["url"].rsplit("/", 1)[-1]
            documents_by_doc.setdefault(doc, []).append({
                "source": "staff_report",
                "name": name,
                "path": name,
                "type": _ext(name),
                "size_bytes": None,
                "url": report["url"],
            })
```

(`doc` is `None` when the reference is unbridged; `None in sol_docs` is `False`, so unbridged and non-solicitation reports are skipped.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_export_document.py -v`
Expected: PASS (the two new tests plus all existing #123 document tests — a solicitation with no bridged report is unaffected; `documents` just gains staff-report entries where a bridge exists).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass, output pristine.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/export/document.py scrapers/tests/test_export_document.py
git commit -m "feat(export): surface staff reports under solicitations via the bid-bridge (#126)"
```

---

### Task 2: Verify on real data + document

Confirm the real payoff and record it in CLAUDE.md.

**Files:**
- Modify: `CLAUDE.md` (the Ariba attachments / documents-index area, or the linking section — wherever the `documents` export is described)

- [ ] **Step 1: Verify the real payoff (read-only — the export does not mutate the DB)**

Run:
```bash
cd scrapers
uv run python -c "
from toronto_bids.store import db
from toronto_bids import config
from toronto_bids.export.document import build_export_document
conn = db.connect(config.DB_PATH); db.init_db(conn)
doc = build_export_document(conn, generated_at='t')
n_reports = sum(1 for s in doc['solicitations'] for d in s['documents'] if d['source']=='staff_report')
n_sols = sum(1 for s in doc['solicitations'] if any(d['source']=='staff_report' for d in s['documents']))
print('staff reports surfaced:', n_reports, 'across solicitations:', n_sols)
"
```
Expected: roughly `staff reports surfaced: 1310 across solicitations: 1237` (small drift is fine if the DB changed).

- [ ] **Step 2: Update CLAUDE.md**

In the section describing the per-solicitation `documents` export (the Ariba attachments section added in #123), add that staff-report PDFs (`background_pdf` kind='bgrd') now also join via the **bid-bridge** — an exact council-`reference` ↔ `document_number` link derived from the `bid` table at query time (both keys parsed from one Ariba-era agenda item, so no fuzzy matching). Note it is Ariba-era only (pre-2019 council items have no dual-key bid row and remain for the #124 spine), that a staff report can appear both under its `council_item` and under its bridged solicitation, and that the entry uses `source="staff_report"` with the legdocs URL exposed. Keep it to a few sentences in the house style.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: staff reports join solicitations via the bid-bridge (#126)"
```

## Notes for the implementer

- The `conn` / `seeded` fixtures live in `tests/test_export_document.py` and `tests/conftest.py`; the `seeded` fixture already creates solicitation `5672751291`. Reuse them — no new fixtures.
- No new dependency; everything is stdlib + existing helpers (`_rows`, `_ext`, `sol_docs`).
- Determinism: the two new queries both carry `ORDER BY`, and staff reports are appended after the ariba/award-summary entries in `(reference, url)` order — stable across runs.
