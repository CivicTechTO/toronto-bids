# Pre-Ariba Bid Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link the already-captured pre-Ariba council-agenda bids to their spine solicitations by recording the `reference ↔ document_number` equivalence in a new mapping table (populated by #77's proven matcher) that the export consults — recovering bids for ~1,288 zero-bid pre-2019 awards with no new scraping.

**Architecture:** A `solicitation_link(reference, document_number, method)` table, rebuilt idempotently each run by a new offline `match_pre_ariba_solicitations` pass (reusing the (winner, value) unique-match #77 uses for titles). The export unions this table into the `reference→document_number` bridge it already builds (#126), so pre-Ariba bids move from their council-item bucket to their solicitation, and pre-Ariba staff reports attach too.

**Tech Stack:** Python 3.12, `uv`, pytest (offline, fixture-based). Live-calibration gate against `~/tb-data/bids.sqlite`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-pre-ariba-bid-bridge-design.md`.
- **No lint/typecheck** — only `uv run pytest` from `scrapers/`.
- **A wrong merge is worse than none.** Only a UNIQUE (winner, value) match records a link; a non-unique or no-supplier-token match is dropped. Measured: 99.6% of (supplier, rounded value) keys identify exactly one solicitation.
- **#145's reconciliation invariant must still hold:** `council_items[].bids + solicitations[].bids + unlinked_bids == meta.counts.bid`. A bridged pre-Ariba bid **moves** from the council-item bucket to the solicitation bucket (each bid in exactly one place), never appears in both.
- **Idempotent:** the mapping table is cleared and rebuilt each run (like the supplier dimension); `store_bids` and bid rows are never mutated.
- Do NOT touch the sync path or add any browser step; this runs in the offline `enrich-titles` agenda flow.
- Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE
  ```
- Branch `feat-124-pre-ariba-bid-bridge` (create it). Do not commit to `main`.

## File Structure

- `scrapers/toronto_bids/store/schema.sql` — new `solicitation_link` table (Task 1).
- `scrapers/toronto_bids/store/db.py` — `counts()` gains `solicitation_link` (Task 1).
- `scrapers/toronto_bids/sources/bid_award_panel.py` — `parse_pre_ariba_awards` carries `reference`; new `match_pre_ariba_solicitations` (Task 2).
- `scrapers/toronto_bids/cli.py` — wire the new pass into `_cmd_enrich_titles` (Task 2).
- `scrapers/toronto_bids/export/document.py` — union the bridge; nest bridged bids under solicitations (Task 3).
- Tests + a live-calibration script (Tasks 2-4).

---

## Task 1: `solicitation_link` table

**Files:** Modify `scrapers/toronto_bids/store/schema.sql`, `scrapers/toronto_bids/store/db.py`; Test `scrapers/tests/test_db.py`.

**Interfaces:** Produces the table `solicitation_link(reference TEXT PRIMARY KEY, document_number TEXT NOT NULL, method TEXT NOT NULL)`; `db.counts(conn)` includes key `"solicitation_link"`.

- [ ] **Step 1: Failing test** — append to `tests/test_db.py`:

```python
def test_solicitation_link_table_exists_and_is_counted(conn):
    from toronto_bids.store import db
    conn.execute("INSERT INTO solicitation_link (reference, document_number, method) "
                 "VALUES ('2016.BD106.3', '5672751291', 'council_pre_ariba')")
    conn.commit()
    assert db.counts(conn)["solicitation_link"] == 1
```

- [ ] **Step 2: Run — fails** (`cd scrapers && uv run pytest tests/test_db.py -k solicitation_link -v`) — no such table.

- [ ] **Step 3: Add the table** in `schema.sql` (near the `bid` table; `IF NOT EXISTS` so `init_db` creates it on existing DBs — no data migration, the pass rebuilds it):

```sql
-- Pre-Ariba reference <-> document_number equivalence (#124, the first slice). A 2013-2018
-- council item and a spine solicitation are the SAME procurement; the City gives no shared
-- identifier (Call Number vs the 10-digit Ariba number backfilled later, #77), so the match
-- is (winner, award value) and lives here. Rebuilt each run; a wrong merge is worse than none,
-- so only a unique match is recorded.
CREATE TABLE IF NOT EXISTS solicitation_link (
    reference        TEXT PRIMARY KEY,
    document_number  TEXT NOT NULL,
    method           TEXT NOT NULL
);
```

- [ ] **Step 4: Add to `db.counts`** — add `"solicitation_link"` to the `tables` list in `counts()`.

- [ ] **Step 5: Run test + full suite** (`uv run pytest tests/test_db.py -k solicitation_link -v` then `uv run pytest -q`) — PASS.

- [ ] **Step 6: Commit**
```bash
git add scrapers/toronto_bids/store/schema.sql scrapers/toronto_bids/store/db.py scrapers/tests/test_db.py
git commit -m "feat(store): solicitation_link table for pre-Ariba reference<->document_number (#124)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 2: `match_pre_ariba_solicitations` pass

**Files:** Modify `scrapers/toronto_bids/sources/bid_award_panel.py`, `scrapers/toronto_bids/cli.py`; Test `scrapers/tests/test_bid_award_panel.py`.

**Interfaces:**
- Consumes `solicitation_link` (Task 1).
- Produces: `parse_pre_ariba_awards(html, meeting=None)` now includes `"reference"` on each item (a full council reference like `2016.BD106.3` when `meeting` is given, else `None`); `match_pre_ariba_solicitations(conn, agendas) -> int` rebuilds `solicitation_link` and returns the count of links recorded.

- [ ] **Step 1: Failing tests** — add to `tests/test_bid_award_panel.py`:

```python
def test_parse_pre_ariba_awards_carries_the_reference():
    from toronto_bids.sources.bid_award_panel import parse_pre_ariba_awards
    html = ("<html><body><h3>BD106.3 - Award of Request for Quotation No. 3917-12-7226 to "
            "Accrue Contracting Ltd. for concrete cutting services in the amount of "
            "$420,000.00 net of all applicable taxes</h3></body></html>")
    items = parse_pre_ariba_awards(html, meeting="2016.BD106")
    assert items and items[0]["reference"] == "2016.BD106.3"
    assert items[0]["winner_raw"] == "Accrue Contracting Ltd."
    assert round(items[0]["award_value"]) == 420000


def test_match_pre_ariba_solicitations_records_a_unique_match(conn):
    from toronto_bids.store import db
    from toronto_bids.models import Solicitation, Award
    from toronto_bids.sources.bid_award_panel import match_pre_ariba_solicitations
    db.upsert_row(conn, Solicitation(document_number="5672751291", source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5672751291", supplier_name_raw="Accrue Contracting Ltd.",
                              award_amount="420000", award_amount_numeric=420000.0,
                              award_date="2016-05-01", source="odata"), overwrite=True)
    conn.commit()
    html = ("<html><body><h3>BD106.3 - Award of RFQ 3917-12-7226 to Accrue Contracting Ltd. "
            "for concrete cutting in the amount of $420,000.00 net of all applicable taxes"
            "</h3></body></html>")
    n = match_pre_ariba_solicitations(conn, {"2016.BD106": html})
    assert n == 1
    row = conn.execute("SELECT * FROM solicitation_link WHERE reference='2016.BD106.3'").fetchone()
    assert row["document_number"] == "5672751291" and row["method"] == "council_pre_ariba"


def test_match_pre_ariba_solicitations_drops_a_non_unique_match(conn):
    # two solicitations share (supplier, value) -> ambiguous -> no link recorded (a wrong merge is worse than none)
    from toronto_bids.store import db
    from toronto_bids.models import Solicitation, Award
    from toronto_bids.sources.bid_award_panel import match_pre_ariba_solicitations
    for doc in ("1111111111", "2222222222"):
        db.upsert_row(conn, Solicitation(document_number=doc, source="odata"), overwrite=True)
        db.upsert_row(conn, Award(document_number=doc, supplier_name_raw="Accrue Contracting Ltd.",
                                  award_amount="420000", award_amount_numeric=420000.0,
                                  award_date="2016-05-01", source="odata"), overwrite=True)
    conn.commit()
    html = ("<html><body><h3>BD106.3 - Award to Accrue Contracting Ltd. for x in the amount of "
            "$420,000.00 net of all applicable taxes</h3></body></html>")
    assert match_pre_ariba_solicitations(conn, {"2016.BD106": html}) == 0
    assert conn.execute("SELECT COUNT(*) FROM solicitation_link").fetchone()[0] == 0
```

- [ ] **Step 2: Run — fails** (`cd scrapers && uv run pytest tests/test_bid_award_panel.py -k "pre_ariba_solicitations or carries_the_reference" -v`).

- [ ] **Step 3: Make `parse_pre_ariba_awards` carry the reference.** It currently does `for chunk in _ITEM_SPLIT.split(text)[1:]`, discarding the `B[AD]\d+\.\d+ - ` delimiter. Change the signature to `parse_pre_ariba_awards(html, meeting=None)` and split with a **capturing** group so the item ref token is retained, pairing each ref with its chunk:

```python
def parse_pre_ariba_awards(html, meeting=None):
    """... (keep the existing docstring) ...

    When `meeting` is given, each item carries a full council `reference` (e.g. '2016.BD106.3')
    built from the meeting's year prefix and the item's 'BD106.3' token — so a match can be
    recorded against the reference (#124), not only used to fill a title.
    """
    text = _WS_LINES.sub(" ", _html.fromstring(html).text_content())
    parts = _ITEM_SPLIT_CAP.split(text)          # [pre, reftoken1, chunk1, reftoken2, chunk2, ...]
    year = (meeting or "").split(".")[0] if meeting else None
    out = []
    for i in range(1, len(parts) - 1, 2):
        reftoken, chunk = parts[i], parts[i + 1]
        head = chunk[:400]
        if _TEN_DIGIT.search(head):
            continue
        winner, value = _WINNER.search(head), _NET_OF_TAXES.search(chunk)
        if not (winner and value):
            continue
        amount = parse_amount(value.group(1))
        if amount is None:
            continue
        out.append({"reference": f"{year}.{reftoken}" if year else None,
                    "title": _clean(head.split("\n")[0]),
                    "winner_raw": _clean(winner.group(1)),
                    "award_value": amount})
    return out
```

Add the capturing split pattern beside `_ITEM_SPLIT`:

```python
_ITEM_SPLIT_CAP = re.compile(r"(B[AD]\d+\.\d+) - ")   # capturing: keeps the 'BD106.3' item token
```

(Leave `_ITEM_SPLIT` as-is if other code uses it; the new pattern is only for this function.)

Update the existing caller `match_pre_ariba_titles` to pass the meeting (so titles still work and the reference is available if ever needed) — find `items.extend(parse_pre_ariba_awards(html))` and change to `parse_pre_ariba_awards(html, meeting)`.

- [ ] **Step 4: Add `match_pre_ariba_solicitations`.** Beside `match_pre_ariba_titles`:

```python
def _awards_by_value(conn):
    """ALL odata awards indexed by rounded value -> [(supplier_tokens, document_number)].
    Unlike _title_less_awards_by_value, this includes titled solicitations: a solicitation with
    a title still needs its bids linked."""
    by_value = {}
    for row in conn.execute(
            "SELECT document_number d, supplier_name_raw s, award_amount_numeric v FROM award "
            "WHERE source='odata' AND award_amount_numeric IS NOT NULL AND supplier_name_raw IS NOT NULL"):
        by_value.setdefault(round(row["v"]), []).append((supplier_tokens(row["s"]), row["d"]))
    return by_value


def match_pre_ariba_solicitations(conn, agendas: dict) -> int:
    """Record pre-Ariba reference<->document_number equivalences in solicitation_link (#124).

    Same join as #77's title match — a council item's (winner, award value net-of-taxes) to a
    solicitation's award — but keyed on the item's REFERENCE, and matched against ALL awards
    (a titled solicitation still needs its bids linked). Unique match only; a wrong merge is
    worse than none. Idempotent: the table is rebuilt from the current match each run.
    """
    by_value = _awards_by_value(conn)
    links = {}
    for meeting, html in agendas.items():
        if meeting.split(".")[0] >= "2019":
            continue                          # 2019+ names a document number directly
        for item in parse_pre_ariba_awards(html, meeting):
            if not item["reference"]:
                continue
            want = supplier_tokens(item["winner_raw"])
            docs = {doc for toks, doc in by_value.get(round(item["award_value"]), []) if want & toks}
            if len(docs) == 1:
                links[item["reference"]] = docs.pop()
    conn.execute("DELETE FROM solicitation_link")
    conn.executemany(
        "INSERT INTO solicitation_link (reference, document_number, method) VALUES (?, ?, 'council_pre_ariba')",
        list(links.items()))
    conn.commit()
    return len(links)
```

- [ ] **Step 5: Wire into `_cmd_enrich_titles`** — in `cli.py`, after the `match_pre_ariba_titles` line (~263), add:

```python
            print(f"  bids linked pre-Ariba: {match_pre_ariba_solicitations(conn, agendas)}")
```
and add `match_pre_ariba_solicitations` to the `from toronto_bids.sources.bid_award_panel import (...)` list at the top of `_cmd_enrich_titles`.

- [ ] **Step 6: Run tests** (`uv run pytest tests/test_bid_award_panel.py -v`) then full suite (`uv run pytest -q`) — PASS. If an existing `parse_pre_ariba_awards` test calls it with one positional arg, it still works (`meeting` defaults to None → `reference` None); update only if it asserts the item dict's exact keys.

- [ ] **Step 7: Commit**
```bash
git add scrapers/toronto_bids/sources/bid_award_panel.py scrapers/toronto_bids/cli.py scrapers/tests/test_bid_award_panel.py
git commit -m "feat(link): match_pre_ariba_solicitations records reference<->document_number (#124)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 3: Export — attach pre-Ariba bids (and staff reports) to their solicitation

**Files:** Modify `scrapers/toronto_bids/export/document.py`; Test `scrapers/tests/test_export_document.py`.

**Interfaces:** Consumes `solicitation_link`. No signature change to `build_export_document`.

- [ ] **Step 1: Failing tests** — add to `tests/test_export_document.py`:

```python
def test_pre_ariba_bid_bridges_to_its_solicitation(seeded):
    # A pre-Ariba bid: has a reference, no document_number. A solicitation_link maps its
    # reference to a solicitation -> the bid nests under the solicitation, not the council item.
    from toronto_bids.models import CouncilItem
    db.upsert_row(seeded, CouncilItem(reference="2016.BD106.3", title="Award"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Loser Co", reference="2016.BD106.3",
                              document_number=None, bid_price="9", source="bid_award_panel"), overwrite=True)
    seeded.execute("INSERT INTO solicitation_link (reference, document_number, method) "
                   "VALUES ('2016.BD106.3', '5672751291', 'council_pre_ariba')")
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    sol = next(s for s in doc["solicitations"] if s["document_number"] == "5672751291")
    assert any(b["bidder_name_raw"] == "Loser Co" for b in sol["bids"])          # under solicitation
    ci = next(c for c in doc["council_items"] if c["reference"] == "2016.BD106.3")
    assert all(b["bidder_name_raw"] != "Loser Co" for b in ci["bids"])           # NOT under council item


def test_unbridged_pre_ariba_bid_stays_under_its_council_item(seeded):
    from toronto_bids.models import CouncilItem
    db.upsert_row(seeded, CouncilItem(reference="2016.BD200.1", title="Award"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Orphan Co", reference="2016.BD200.1",
                              document_number=None, bid_price="9", source="bid_award_panel"), overwrite=True)
    seeded.commit()  # no solicitation_link row
    doc = build_export_document(seeded, generated_at="t")
    ci = next(c for c in doc["council_items"] if c["reference"] == "2016.BD200.1")
    assert any(b["bidder_name_raw"] == "Orphan Co" for b in ci["bids"])


def test_reconciliation_holds_with_a_bridged_pre_ariba_bid(seeded):
    from toronto_bids.models import CouncilItem
    db.upsert_row(seeded, CouncilItem(reference="2016.BD106.3", title="Award"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Loser Co", reference="2016.BD106.3",
                              document_number=None, bid_price="9", source="bid_award_panel"), overwrite=True)
    seeded.execute("INSERT INTO solicitation_link (reference, document_number, method) "
                   "VALUES ('2016.BD106.3', '5672751291', 'council_pre_ariba')")
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    counts = doc["meta"]["counts"]
    council = sum(len(c["bids"]) for c in doc["council_items"])
    nested = sum(len(s["bids"]) for s in doc["solicitations"])
    assert council + nested + len(doc["unlinked_bids"]) == counts["bid"]
```

- [ ] **Step 2: Run — fails** (`cd scrapers && uv run pytest tests/test_export_document.py -k "pre_ariba or reconciliation_holds_with" -v`).

- [ ] **Step 3: Union `solicitation_link` into the bridge.** In `build_export_document`, the bridge is built (~line 93) from dual-key bids:

```python
    bridge: dict[str, str] = {}
    for row in _rows(conn, "SELECT DISTINCT reference, document_number FROM bid "
                           "WHERE reference IS NOT NULL AND document_number IS NOT NULL "
                           "ORDER BY reference, document_number"):
        bridge[row["reference"]] = row["document_number"]
```

Immediately after that loop, union the recorded pre-Ariba links (a bid-derived entry, if any, wins; otherwise the recorded link fills it):

```python
    # Pre-Ariba items have no dual-key bid to derive from; solicitation_link records the
    # (winner,value) match instead (#124). setdefault so a bid-derived bridge is never overridden.
    for row in _rows(conn, "SELECT reference, document_number FROM solicitation_link "
                           "ORDER BY reference"):
        bridge.setdefault(row["reference"], row["document_number"])
```

- [ ] **Step 4: Nest a bridged pre-Ariba bid under its solicitation.** Find the bid-nesting loop (post-#145) that fills `bids_by_ref` / `bids_by_doc` / `unlinked_bids`. A reference bid currently always goes to `bids_by_ref` (council item). Change it so a reference bid whose reference bridges to a real solicitation nests under that solicitation instead:

```python
    for bid in _rows(conn, "SELECT * FROM bid ORDER BY reference, document_number, bidder_name_raw, id"):
        cleaned = _drop(bid, "id")
        ref, doc = bid["reference"], bid["document_number"]
        bridged = bridge.get(ref) if ref is not None else None          # pre-Ariba: ref -> sol
        if ref is not None and bridged in sol_docs:
            bids_by_doc.setdefault(bridged, []).append(_drop(cleaned, "document_number"))   # under solicitation
        elif ref is not None:
            bids_by_ref.setdefault(ref, []).append(cleaned)             # under council item (unchanged)
        elif doc in sol_docs:
            bids_by_doc.setdefault(doc, []).append(_drop(cleaned, "document_number"))       # #145 reference-null
        else:
            unlinked_bids.append(cleaned)
```

(Match the exact variable names/structure the current code uses — read the loop first and adapt; the logic above is the intent. Do not change how reference-null bids are handled beyond routing.)

- [ ] **Step 5: Run tests** (`uv run pytest tests/test_export_document.py -v`) then full suite (`uv run pytest -q`) — PASS, including the pre-existing #145 reconciliation tests.

- [ ] **Step 6: Commit**
```bash
git add scrapers/toronto_bids/export/document.py scrapers/tests/test_export_document.py
git commit -m "feat(export): nest bridged pre-Ariba bids under their solicitation (#124)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 4: Live-calibration gate + recording

**Files:** none (measurement + a GitHub comment).

- [ ] **Step 1: Run the pass on the real DB** — `cd /home/alex/toronto-bids/scrapers && TB_DATA_DIR="$HOME/tb-data" uv run tb enrich-titles 2>&1 | grep -iE "bids linked|pre-Ariba|council"`. Record the "bids linked pre-Ariba" count (references linked).

- [ ] **Step 2: Calibrate false-merge against ground truth.** Run this and paste the output into the report:

```bash
cd /home/alex/toronto-bids/scrapers && TB_DATA_DIR="$HOME/tb-data" uv run python - <<'PY'
import sqlite3; from toronto_bids import config
c=sqlite3.connect(config.DB_PATH); c.row_factory=sqlite3.Row
# recovery: how many zero-bid awarded solicitations now gain a bid via a link
linked=c.execute("SELECT COUNT(*) FROM solicitation_link").fetchone()[0]
# false-merge check: a link whose document_number's award supplier does NOT share a token with
# any bidder under that reference would be suspect. (winner should be among the bidders.)
susp=0; checked=0
for r in c.execute("SELECT reference, document_number FROM solicitation_link"):
    checked+=1
    awd=c.execute("SELECT supplier_name_raw FROM award WHERE document_number=? AND source='odata'",(r['document_number'],)).fetchall()
    bidders=c.execute("SELECT bidder_name_raw FROM bid WHERE reference=?",(r['reference'],)).fetchall()
    import re
    def tok(s): return set(w for w in re.sub(r'[^a-z0-9 ]',' ',(s or '').lower()).split() if len(w)>2)
    awd_tok=set().union(*[tok(a['supplier_name_raw']) for a in awd]) if awd else set()
    bid_tok=set().union(*[tok(b['bidder_name_raw']) for b in bidders]) if bidders else set()
    if bidders and awd_tok and not (awd_tok & bid_tok):
        susp+=1
print(f"links recorded: {linked}")
print(f"links whose award winner is NOT among the reference's bidders (false-merge candidates): {susp}/{checked}")
PY
```

- [ ] **Step 3: Adjudicate.** If false-merge candidates are ~0, proceed. If material, the match is over-eager — tighten (this is a plan-level stop; report BLOCKED with the specifics rather than shipping a wrong merge).

- [ ] **Step 4: Comment on #124 and #163** with: links recorded, bids attached, the drop in the zero-bid awarded-solicitation count (re-run the #163 zero-bid query before/after), and the false-merge calibration. (Use `gh api ... /issues/124/comments` — `gh issue comment` also works.)

---

## Self-Review

**Spec coverage:** mapping table not bid-mutation → Task 1/3 ✓; reuse #77 matcher, unique-only → Task 2 ✓; parse carries reference → Task 2 ✓; export union + move-not-duplicate preserving #145 invariant → Task 3 ✓; live-calibration gate → Task 4 ✓; offline/no-browser → runs in enrich-titles ✓.

**Placeholder scan:** Task 3 Step 4 says "adapt to the exact current loop" — intentional (the implementer reads the post-#145 loop), with the full intended logic given. No TBD.

**Type consistency:** `solicitation_link(reference, document_number, method)` consistent across Tasks 1/2/3; `parse_pre_ariba_awards(html, meeting=None) -> [{reference,title,winner_raw,award_value}]` and `match_pre_ariba_solicitations(conn, agendas) -> int` used consistently; `bridge: dict[str,str]` extended in Task 3 matches its Task-derived producer.
