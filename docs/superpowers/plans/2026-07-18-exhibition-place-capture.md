# Exhibition Place Capture (#130) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover Exhibition Place's post-2019 procurement record — awards + bidder prices — from its Board of Governors reports on legdocs, into the existing `agency_*` keyspace, reusing the Zoo/TRCA board-report machinery.

**Architecture:** Extract shared amount/confidential helpers to `sources/agency_report.py`; add an `exhibition-place` buyer and a new `sources/ep_board.py` that reuses the `bid_award_panel` prober (TMMIS EP committee, `YYYY.EP<n>.<n>`, headed browser) for discovery, `trca_board._store_pending_pdfs` for the plain-HTTP legdocs PDF download, and new EP-specific pure parsers. Wired into `tb enrich-agencies --only ep --scrape`, not the browser-free nightly path.

**Tech Stack:** Python 3.12, `uv`, sqlite3, pytest (offline), `pdftotext`; Playwright (`council` extra) for discovery only.

## Global Constraints

- **Most EP board reports are NOT procurement awards** (WSIB safety reports, status updates, governance) and several carry `$` amounts that are traps. The parser MUST anchor on a procurement-award signal and **refuse everything else** (return None) — the negative fixtures are load-bearing.
- Award winner is EP-specific: "award of Contract No. X (RFT No. EP###-YYYY) **to WINNER for the <project>** in the amount of $AMOUNT" — text sits between WINNER and the amount, so the winner regex stops at " for "/the amount phrase, NOT the shared Zoo "to WINNER <phrase> $" pattern (which over-captures here).
- `native_ref` is the EP solicitation ref: `RFT No. EP###-YYYY` (primary) → `Contract No. <token>` → the passed `fallback_ref`. Match the shape, not the label vocabulary (#96/#138).
- Confidential reports (Confidential Attachment) keep a publicly-named winner, `value_confidential=1`, NULL amount (the Zoo rule); a redacted counterparty ("a Consumer Show Client", lowercase) → winner None, still recorded.
- Amounts: raw `$` token verbatim + numeric via the model; never aggregate raw TEXT. Bid rows refuse rather than guess (the #94 rule).
- Discovery needs the `council` extra + a display; NEVER on the `tb nightly` path. Per-body isolation: EP failing never stops trca/zoo.
- Tests offline, fixture-based. Commit messages end with the project's Co-Authored-By + Claude-Session trailer. Branch: `feat-130-exhibition-place` (checked out; spec + 5 real EP fixtures already committed there).

---

### Task 1: extract shared `agency_report.py` helpers; repoint `zoo_board`

**Files:**
- Create: `scrapers/toronto_bids/sources/agency_report.py`
- Modify: `scrapers/toronto_bids/sources/zoo_board.py` (import the shared names, delete the local copies)
- Test: `scrapers/tests/test_agency_report.py`

**Interfaces:**
- Produces: in `agency_report.py` — `AMOUNT_PHRASE: str`, `MONEY: str`, `AMOUNT_RE: re.Pattern` (matches `<amount-phrase> $X`), `CONFIDENTIAL_RE: re.Pattern`, `amount_or_none(text, m) -> str | None`. `zoo_board` and `ep_board` import these.

- [ ] **Step 1: Write the failing test**

```python
# scrapers/tests/test_agency_report.py
import re

from toronto_bids.sources.agency_report import (
    AMOUNT_RE, CONFIDENTIAL_RE, amount_or_none,
)


def test_amount_re_reads_in_the_amount_of():
    m = AMOUNT_RE.search("awarded in the amount of $410,563.00 to the firm")
    assert m and m.group(1) == "$410,563.00"


def test_amount_or_none_refuses_european_million_shorthand():
    # "$1,25 million" captures only "$1" under thousands-grouping — refuse it.
    m = AMOUNT_RE.search("in the amount of $1,25 million")
    assert amount_or_none("in the amount of $1,25 million", m) is None


def test_confidential_re_detects_attachment():
    assert CONFIDENTIAL_RE.search("This report has a CONFIDENTIAL ATTACHMENT with value")
    assert not CONFIDENTIAL_RE.search("no such thing here")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_agency_report.py -v`
Expected: FAIL — `ModuleNotFoundError: toronto_bids.sources.agency_report`.

- [ ] **Step 3: Create `agency_report.py`**

Move the shared primitives out of `zoo_board.py` verbatim (no behavior change):

```python
"""Pure regex primitives shared by the agency board-report parsers (Zoo, Exhibition Place, …).

Amounts are written many ways and "in the amount of" dominates the corpus; a truncated
"$1,25 million" shorthand (comma decimal + scale word) captures a bogus "$1" and is refused.
"""
import re

AMOUNT_PHRASE = (
    r"(?:in\s+the\s+amount\s+of|at\s+a\s+(?:total\s+)?cost(?:\s+not\s+to\s+exceed)?(?:\s+of)?"
    r"|for\s+the\s+(?:total\s+)?(?:sum|amount)\s+of|in\s+an\s+amount\s+not\s+to\s+exceed"
    r"|total\s+cost\s+(?:not\s+to\s+exceed\s+)?(?:of\s+)?)")
MONEY = r"(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)"
AMOUNT_RE = re.compile(r"(?i:" + AMOUNT_PHRASE + r")\s*" + MONEY)
CONFIDENTIAL_RE = re.compile(r"CONFIDENTIAL\s+ATTACHMENT", re.I)
_TRUNCATED_AMOUNT = re.compile(r"\s*(?:[.,]\d|million|billion)", re.I)


def amount_or_none(text: str, m) -> str | None:
    """The matched money string with spaces stripped, or None if the match is a truncated
    "$X,YY million" shorthand (a bogus tiny figure) — refuse rather than store $1."""
    if m is None or _TRUNCATED_AMOUNT.match(text, m.end()):
        return None
    return m.group(m.lastindex).replace(" ", "")
```

- [ ] **Step 4: Repoint `zoo_board.py`**

In `zoo_board.py`: delete the local `_AMOUNT_PHRASE`, `_MONEY`, `_ZOO_AMOUNT`, `_CONFIDENTIAL`, `_TRUNCATED_AMOUNT`, `_amount_or_none`, and add:

```python
from toronto_bids.sources.agency_report import (
    AMOUNT_PHRASE as _AMOUNT_PHRASE, AMOUNT_RE as _ZOO_AMOUNT,
    CONFIDENTIAL_RE as _CONFIDENTIAL, MONEY as _MONEY, amount_or_none as _amount_or_none,
)
```

(`_ZOO_AWARD` stays in `zoo_board` — it is Zoo-specific — and still builds from the imported `_AMOUNT_PHRASE`/`_MONEY`.) Verify `_ZOO_AWARD`'s definition still references `_AMOUNT_PHRASE` and `_MONEY` (now imported), unchanged.

- [ ] **Step 5: Run the tests**

Run: `cd scrapers && uv run pytest tests/test_agency_report.py tests/test_zoo_reports.py -v` then `uv run pytest`
Expected: all PASS — the existing zoo tests prove the extraction is behavior-preserving.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/sources/agency_report.py scrapers/toronto_bids/sources/zoo_board.py \
        scrapers/tests/test_agency_report.py
git commit -m "refactor(agencies): extract shared amount/confidential helpers to agency_report.py (#130)"
```

---

### Task 2: `exhibition-place` buyer + EP discovery/download

**Files:**
- Modify: `scrapers/toronto_bids/buyers.py` (append a Buyer)
- Modify: `scrapers/toronto_bids/config.py` (append EP dirs)
- Create: `scrapers/toronto_bids/sources/ep_board.py` (discovery + download half)
- Test: `scrapers/tests/test_ep_reports.py`

**Interfaces:**
- Consumes: `bid_award_panel.scrape_agendas`/`cached_agendas`/`parse_agenda_pdfs`, `trca_board._store_pending_pdfs`, `BackgroundPdf`, `db`.
- Produces: `EP_TERM_STARTS`, `scrape_ep_agendas(virtual_display, log) -> dict`, `cached_ep_agendas() -> dict`, `download_ep_reports(conn, http, agendas, log) -> int`. Tasks 3-5 consume `EP_TERM_STARTS` and the buyer.

- [ ] **Step 1: Write the failing test**

```python
# scrapers/tests/test_ep_reports.py
from toronto_bids.sources.bid_award_panel import discover_meetings
from toronto_bids.sources.ep_board import EP_TERM_STARTS


def test_ep_terms_probe_the_ep_committee_series():
    calls = []

    def fetch(ref):
        calls.append(ref)
        return "The published report was not found"     # every probe misses

    found = discover_meetings(fetch, term_starts=[("EP", 2023, "2022-2026", 1)],
                              stop_after_misses=2)
    assert found == {}
    assert calls[0] == "2023.EP1"                        # probes EP, not ZB/BA


def test_ep_terms_cover_the_confirmed_terms():
    series = {t[0] for t in EP_TERM_STARTS}
    years = {t[1] for t in EP_TERM_STARTS}
    assert series == {"EP"}
    assert 2023 in years                                 # 2022-2026 term (2025.EP18 seen live)


def test_ep_buyer_seeded():
    import sqlite3
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.store import db
    conn = db.connect(":memory:"); db.init_db(conn)
    ids = seed_buyers(conn)
    assert "exhibition-place" in ids
    row = conn.execute("SELECT kind, partnered FROM buyer WHERE slug='exhibition-place'").fetchone()
    assert row["kind"] == "agency" and row["partnered"] == 0
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_ep_reports.py -v`
Expected: FAIL — `ep_board` / the seeded buyer don't exist.

- [ ] **Step 3: Add the buyer**

In `buyers.py`, append to `DEFAULT_BUYERS`:

```python
    Buyer(slug="exhibition-place", name="Exhibition Place", kind="agency", partnered=0,
          funding_share=None, platform="Bonfire",
          notes="City agency (Board of Governors); left the PMMD feed in 2019 for its own "
                "Bonfire portal. Awards captured from Board of Governors reports on legdocs "
                "(TMMIS EP committee); the Bonfire portal is gated (#134)."),
```

- [ ] **Step 4: Add config dirs**

Append to `config.py`:

```python
# Exhibition Place Board of Governors (#130): the EP committee on TMMIS, same infrastructure
# as the Zoo's ZB series — headed-browser discovery, plain-HTTP legdocs report PDFs.
EP_AGENDAS_DIR = DATA_DIR / "agencies" / "ep" / "agendas"
EP_REPORTS_DIR = DATA_DIR / "agencies" / "ep"
```

- [ ] **Step 5: Create the discovery/download half of `ep_board.py`**

```python
"""Exhibition Place Board of Governors capture (#130): the EP committee on TMMIS.

Same infrastructure as the Zoo's ZB series (#135): TMMIS agendas need the headed browser
(Akamai-gated), report PDFs are plain-HTTP legdocs (/legdocs/mmis/YYYY/ep/bgrd/...). Reuses
the bid_award_panel prober; the EP reference format is YYYY.EP<meeting>.<item> (confirmed
live). Most EP board reports are NOT procurement awards, so the parsers (added next) refuse
non-awards.
"""
from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.bid_award_panel import (cached_agendas, parse_agenda_pdfs,
                                                  scrape_agendas)
from toronto_bids.sources.trca_board import _store_pending_pdfs
from toronto_bids.store import db

# EP meetings reset numbering per council term, like ZB. Confirmed live: the 2022-2026 term
# runs EP1..EP23 (as of 2026-06). The 2018-2022 term is probed too (2022.EP25 seen).
EP_TERM_STARTS = [
    ("EP", 2019, "2018-2022", 1),
    ("EP", 2023, "2022-2026", 1),
]


def scrape_ep_agendas(virtual_display: bool = False, log=lambda _m: None) -> dict:
    return scrape_agendas(config.EP_AGENDAS_DIR, virtual_display=virtual_display,
                          log=log, term_starts=EP_TERM_STARTS)


def cached_ep_agendas() -> dict:
    return cached_agendas(config.EP_AGENDAS_DIR)


def download_ep_reports(conn, http, agendas: dict, log=lambda _m: None) -> int:
    """Index every bgrd PDF the EP agendas link, then fetch the ones not yet held. Plain HTTP,
    resilient (a dead URL is skipped), sha256-queued. EP reports live under /legdocs/.../ep/."""
    for meeting, html in agendas.items():
        for pdf in parse_agenda_pdfs(html, meeting):
            db.upsert_row(conn, BackgroundPdf(url=pdf["url"], reference=pdf["reference"],
                                              kind="agency_board"), overwrite=False)
    conn.commit()
    return _store_pending_pdfs(conn, http, config.EP_REPORTS_DIR, "%/ep/%", log, "ep")
```

Note: the download filter is `"%/ep/%"` (the EP committee legdocs path). Confirm during the live run (Step 7) that EP report URLs match it; if an EP agenda links reports under a second path, widen the filter and record it.

- [ ] **Step 6: Run the offline tests**

Run: `cd scrapers && uv run pytest tests/test_ep_reports.py tests/test_zoo_reports.py -v` then `uv run pytest`
Expected: PASS (the `term_starts` mechanism is already generalized; buyer seeded).

- [ ] **Step 7: Live discovery — record MORE real award fixtures (REQUIRED, not offline)**

The repo has 5 seed EP fixtures, but only ONE is a competitive award-with-table (`ep_award_with_table_2023.txt`). One example is not enough to trust the award/table parsers (the #136/#138 lesson). Run a live discovery and record **at least 2 more** competitive-award reports (RFT/Contract award with a bid table) as fixtures:

```bash
cd scrapers && TB_DATA_DIR=/tmp/ep-live uv run tb enrich-agencies --only ep --scrape --virtual-display 2>&1 | tail
# then, from the cached agendas, find reports containing "Table 1" / "Tender Price" / "award of Contract"
# across BOTH terms, pdftotext them, and copy 2+ real competitive-award reports into
# scrapers/tests/fixtures/agencies/ep_award_*.txt (name them by year/subject), noting them in SOURCES.md.
```

If, after scanning both terms, competitive-award-with-table reports are genuinely rare (EP's board handles mostly non-procurement business), record however many exist and **say so in the report** — do not fabricate. Commit the additional fixtures.

- [ ] **Step 8: Commit**

```bash
git add scrapers/toronto_bids/buyers.py scrapers/toronto_bids/config.py \
        scrapers/toronto_bids/sources/ep_board.py scrapers/tests/test_ep_reports.py \
        scrapers/tests/fixtures/agencies/
git commit -m "feat(ep): exhibition-place buyer + EP discovery/download; more award fixtures (#130)"
```

---

### Task 3: `parse_ep_report` — award / confidential / refuse-non-awards

**Files:**
- Modify: `scrapers/toronto_bids/sources/ep_board.py` (append)
- Test: `scrapers/tests/test_ep_reports.py` (append)

**Interfaces:**
- Consumes: `agency_report` helpers; the committed EP fixtures.
- Produces: `parse_ep_report(text: str, fallback_ref: str) -> dict | None` → `{native_ref, title, winner, amount, confidential, report_url}`. Task 5 consumes it.

- [ ] **Step 1: Write the failing tests**

```python
import pathlib

from toronto_bids.sources.ep_board import parse_ep_report

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _read(name):
    return (FIXTURES / name).read_text()


def test_competitive_award_winner_ref_amount():
    got = parse_ep_report(_read("ep_award_with_table_2023.txt"), fallback_ref="2023.EP1.1")
    assert got is not None
    assert got["native_ref"] == "EP110-2023"            # RFT No., not the Contract No.
    assert got["winner"] == "Powell Fence Limited"       # stops at " for ", not the project text
    from toronto_bids.amount import parse_amount
    assert parse_amount(got["amount"]) == 1484065.00
    assert got["confidential"] == 0


def test_confidential_award_keeps_named_winner_nulls_amount():
    got = parse_ep_report(_read("ep_confidential_decision_2025.txt"), fallback_ref="2025.EP18.9")
    assert got is not None and got["confidential"] == 1
    assert got["amount"] is None
    assert "Coca-Cola" in got["winner"]


def test_confidential_with_redacted_counterparty_has_no_winner_but_is_kept():
    got = parse_ep_report(_read("ep_confidential_agreement.txt"), fallback_ref="2023.EP7.2")
    assert got is not None and got["confidential"] == 1
    assert got["winner"] is None                         # "a Consumer Show Client" is redacted, not a firm
    assert got["native_ref"] == "2023.EP7.2"             # no RFT/Contract ref -> fallback


def test_wsib_safety_report_is_refused_despite_dollar_amounts():
    assert parse_ep_report(_read("ep_non_award_wsib_report.txt"), fallback_ref="2023.EP1.4") is None


def test_procurement_status_update_is_refused():
    assert parse_ep_report(_read("ep_non_award_procurement_status.txt"), fallback_ref="2023.EP1.5") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_ep_reports.py -k "award or refused or redacted or wsib or status" -v`
Expected: FAIL — `parse_ep_report` undefined.

- [ ] **Step 3: Implement `parse_ep_report`**

Append to `ep_board.py`:

```python
import re

from toronto_bids.sources.agency_report import AMOUNT_RE, CONFIDENTIAL_RE, amount_or_none

# The EP solicitation ref: RFT No. EP###-YYYY (primary) or a Contract No. token. Match the
# shape, not the label vocabulary.
_EP_RFT = re.compile(r"RF[TPQ]\s*No\.?\s*(EP\d+-\d{4})", re.I)
_EP_CONTRACT = re.compile(r"Contract\s+No\.?\s*([0-9][0-9A-Za-z\-]{3,})", re.I)
# The competitive award clause: "award of ... to WINNER for the <project> ..." — WINNER is
# bounded and STOPS at " for " / a comma / the amount phrase (EP puts the project between the
# winner and the amount, so the shared Zoo "to WINNER <phrase> $" over-captures).
_EP_AWARD = re.compile(
    r"award\s+of\s+(?:the\s+)?(?:Contract|RF[TPQ]|Tender)\b[^$]{0,120}?\bto\s+"
    r"([A-Z][A-Za-z0-9&.,'’ \-]{2,55}?)\s+(?:for\b|,|in\s+the\s+amount)", re.I | re.S)
# A confidential agreement's counterparty, when publicly named ("agreement with Coca-Cola …").
# Capital-anchored, so a redacted "a Consumer Show Client" is correctly skipped.
_EP_AGREEMENT = re.compile(
    r"agreement\s+with\s+([A-Z][A-Za-z0-9&.,'’ \-]{2,45}?)(?:\s+(?:for|to|on|,)\b|\s*\()", re.S)
_EP_SUBJECT = re.compile(r"^([A-Z].*(?:Tender|Contract|Award|Agreement|RF[TPQ]).*)$", re.M)


def _ep_ref(text: str, fallback_ref: str) -> str:
    m = _EP_RFT.search(text)
    if m:
        return m.group(1).upper()
    m = _EP_CONTRACT.search(text)
    return m.group(1) if m else fallback_ref


def parse_ep_report(text: str, fallback_ref: str, report_url: str | None = None) -> dict | None:
    """Map one EP board report to an award, or None. Most EP reports are NOT procurement awards
    (WSIB safety, status updates, governance) — refuse those. Keeps a confidential award (value
    withheld) when it names a real counterparty OR is an explicit procurement agreement."""
    confidential = 1 if CONFIDENTIAL_RE.search(text) else 0
    aw = _EP_AWARD.search(text)
    winner = aw.group(1).strip() if aw else None
    amount = None if confidential else amount_or_none(text, AMOUNT_RE.search(text)) if aw else None

    if not aw:
        # No competitive award clause. Keep ONLY a confidential procurement agreement.
        if confidential and re.search(r"\bagreement\b|\baward\b|\bcontract\b", text, re.I):
            ag = _EP_AGREEMENT.search(text)
            winner = ag.group(1).strip() if ag else None
        else:
            return None                              # not a procurement award — refuse

    ref_m = _EP_SUBJECT.search(text)
    return {
        "native_ref": _ep_ref(text, fallback_ref),
        "title": re.sub(r"\s+", " ", ref_m.group(1)).strip() if ref_m else None,
        "winner": winner,
        "amount": amount,
        "confidential": confidential,
        "report_url": report_url,
    }
```

- [ ] **Step 4: Run the tests; iterate the parser (not the assertions) against the real fixtures**

Run: `cd scrapers && uv run pytest tests/test_ep_reports.py -v`
Expected: PASS. The assertions encode the real fixtures' ground truth — if one fails, the parser is wrong. The likely first failures: the `_EP_AWARD` winner boundary (must yield exactly "Powell Fence Limited"), and the WSIB/status negatives (must return None despite `$`/"approved" text). Debug against the fixture bytes; keep the refuse-non-awards rule.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/ep_board.py scrapers/tests/test_ep_reports.py
git commit -m "feat(ep): parse_ep_report — award/confidential extraction, refuses non-awards (#130)"
```

---

### Task 4: `parse_ep_bid_table` — the Table 1 bidder prices

**Files:**
- Modify: `scrapers/toronto_bids/sources/ep_board.py` (append)
- Test: `scrapers/tests/test_ep_reports.py` (append)

**Interfaces:**
- Consumes: the committed EP fixtures.
- Produces: `parse_ep_bid_table(text: str) -> list[tuple[str, str]]` — `(bidder, price)` per Table 1 row. Task 5 consumes it.

- [ ] **Step 1: Write the failing test**

```python
def test_bid_table_extracts_all_three_bidders_with_prices():
    rows = parse_ep_bid_table(_read("ep_award_with_table_2023.txt"))
    assert rows == [
        ("Powell Fence Limited", "$1,484,065.00"),
        ("M.J.K. Construction Incorporated", "$1,619,001.00"),
        ("Clearway Construction Incorporated", "$1,851,100.00"),
    ]


def test_bid_table_absent_returns_empty():
    rows = parse_ep_bid_table(_read("ep_non_award_wsib_report.txt"))
    assert rows == []                                    # no Table 1 -> no bids
```

(add `from toronto_bids.sources.ep_board import parse_ep_bid_table` to the test file's imports.)

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_ep_reports.py -k bid_table -v`
Expected: FAIL — `parse_ep_bid_table` undefined.

- [ ] **Step 3: Implement `parse_ep_bid_table`**

Append to `ep_board.py`:

```python
# A Table 1 row: a bidder name (letters/&/./,/spaces) followed by its first $ price. The winner
# row carries a second $ (recommended contract price); take the FIRST. Column-header lines
# ("Base Bid Price", "Received", "Recommended Contract Price") have no leading firm name + price
# on the same run and are skipped by requiring a name that ends in a company word OR a name-then-$
# on one line. Refuse a line that isn't a clean name+price (the #94 rule).
_EP_BID_ROW = re.compile(
    r"^\s*([A-Z][A-Za-z0-9&.,'’ \-]{3,60}?)\s+(\$\s?\d{1,3}(?:,\d{3})*\.\d{2})", re.M)
_EP_TABLE_HEAD = re.compile(r"Table\s+\d[^\n]*Tender\s+Price\s+Submission", re.I)


def parse_ep_bid_table(text: str) -> list[tuple[str, str]]:
    """Every (bidder, base-bid-price) in an EP 'Table 1: Tender Price Submission'. Empty if the
    report has no such table."""
    head = _EP_TABLE_HEAD.search(text)
    if not head:
        return []
    # Scope to the region after the header up to a blank-line gap / the next section.
    tail = text[head.end():head.end() + 1500]
    rows = []
    for m in _EP_BID_ROW.finditer(tail):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        price = m.group(2).replace(" ", "")
        # Skip a column-header fragment that slipped through (no company suffix and generic words).
        if name.lower() in {"base bid price", "recommended contract price", "received"}:
            continue
        rows.append((name, price))
    return rows
```

- [ ] **Step 4: Run the tests; iterate against the real fixture**

Run: `cd scrapers && uv run pytest tests/test_ep_reports.py -k bid_table -v`
Expected: PASS with exactly the three rows. If the column-header row ("Bidder … Base Bid Price Received Recommended Contract Price") leaks in, tighten the skip set or require the price to be a `.\d{2}` decimal (bids are, headers aren't). The assertion is ground truth — fix the parser.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/ep_board.py scrapers/tests/test_ep_reports.py
git commit -m "feat(ep): parse_ep_bid_table — Table 1 bidder prices (#130)"
```

---

### Task 5: `store_ep_reports` + CLI wiring + live measurement + docs

**Files:**
- Modify: `scrapers/toronto_bids/sources/ep_board.py` (append `store_ep_reports`)
- Modify: `scrapers/toronto_bids/cli.py` (`--only` choices, body loop, EP block)
- Modify: `CLAUDE.md` (extend the agency-capture subsection)
- Test: `scrapers/tests/test_ep_reports.py` (append)

**Interfaces:**
- Consumes: `parse_ep_report`, `parse_ep_bid_table`, `AgencySolicitation`/`AgencyAward`/`AgencyBid`, `seed_buyers`.
- Produces: `store_ep_reports(conn, buyer_id) -> dict` (`solicitations`, `awards`, `bids`); `tb enrich-agencies --only ep [--scrape]`.

- [ ] **Step 1: Write the failing test**

```python
def test_store_ep_reports_lands_award_and_bids(conn):
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.sources.ep_board import store_ep_reports
    ids = seed_buyers(conn)
    conn.execute("INSERT INTO background_pdf (url, kind, sha256, text) VALUES "
                 "('https://www.toronto.ca/legdocs/mmis/2023/ep/bgrd/backgroundfile-240943.pdf',"
                 " 'agency_board', 'x', ?)", (_read("ep_award_with_table_2023.txt"),))
    conn.commit()
    got = store_ep_reports(conn, ids["exhibition-place"])
    assert got["solicitations"] == 1 and got["awards"] == 1 and got["bids"] == 3
    aw = conn.execute("SELECT supplier_name_raw, award_amount_numeric FROM agency_award "
                      "WHERE native_ref='EP110-2023'").fetchone()
    assert aw["supplier_name_raw"] == "Powell Fence Limited"
    assert aw["award_amount_numeric"] == 1484065.00
    bids = conn.execute("SELECT COUNT(*) FROM agency_bid WHERE bid_price_numeric IS NOT NULL "
                        "AND native_ref='EP110-2023'").fetchone()[0]
    assert bids == 3
```

(`conn` fixture: reuse the one from `test_bids_tenders.py`'s pattern — an in-memory `db.connect(":memory:")` with `db.init_db`. Add it to this test file if not already present.)

- [ ] **Step 2: Run to verify failure**

Run: `cd scrapers && uv run pytest tests/test_ep_reports.py -k store_ep -v`
Expected: FAIL — `store_ep_reports` undefined.

- [ ] **Step 3: Implement `store_ep_reports`**

Append to `ep_board.py`:

```python
from toronto_bids.models import AgencyAward, AgencyBid, AgencySolicitation


def store_ep_reports(conn, buyer_id: int) -> dict:
    """Parse held EP reports into agency rows. One AgencySolicitation + AgencyAward per award
    report (confidential ones keep the winner, NULL amount), and one AgencyBid per Table 1 row.
    Non-award reports are refused by parse_ep_report and contribute nothing."""
    counts = {"solicitations": 0, "awards": 0, "bids": 0}
    for row in conn.execute(
            "SELECT reference, url, text FROM background_pdf WHERE kind='agency_board' "
            "AND url LIKE '%/ep/%' AND text IS NOT NULL ORDER BY url").fetchall():
        got = parse_ep_report(row["text"], fallback_ref=row["reference"] or row["url"],
                              report_url=row["url"])
        if got is None:
            continue
        db.upsert_row(conn, AgencySolicitation(
            buyer_id=buyer_id, native_ref=got["native_ref"], title=got["title"],
            status="awarded", posted_date=None, closing_date=None, portal_url=None,
            source="ep_board"), overwrite=False)
        counts["solicitations"] += 1
        db.upsert_row(conn, AgencyAward(
            buyer_id=buyer_id, native_ref=got["native_ref"], supplier_name_raw=got["winner"],
            award_amount=got["amount"], value_confidential=got["confidential"], award_date=None,
            report_url=got["report_url"], source="ep_board"), overwrite=True)
        counts["awards"] += 1
        for bidder, price in parse_ep_bid_table(row["text"]):
            db.upsert_row(conn, AgencyBid(
                buyer_id=buyer_id, native_ref=got["native_ref"], bidder_name_raw=bidder,
                bid_price=price, report_url=row["url"], source="ep_board"), overwrite=True)
            counts["bids"] += 1
    conn.commit()
    return counts
```

- [ ] **Step 4: Wire the CLI**

In `cli.py` `build_parser`, change the `enrich-agencies --only` choices to include `ep`:

```python
    p_ag.add_argument("--only", choices=["zoo", "trca", "ep"],
                      help="Run one body instead of all")
```

In `_cmd_enrich_agencies`, change the default body list and add an EP block after the `zoo` block (mirroring it — EP uses the same scrape/cached/download shape):

```python
        bodies = [args.only] if args.only else ["trca", "zoo", "ep"]
        ...
        if "ep" in bodies:
            try:
                from toronto_bids.sources.ep_board import (
                    cached_ep_agendas, download_ep_reports, scrape_ep_agendas, store_ep_reports)
                agendas = (scrape_ep_agendas(virtual_display=args.virtual_display, log=out)
                           if args.scrape else cached_ep_agendas())
                print(f"  ep EP agendas        : {len(agendas)}"
                      f" ({'scraped' if args.scrape else 'cached'})")
                if agendas and (args.fetch or args.scrape):
                    http = HttpClient()
                    try:
                        print(f"  ep reports fetched   : "
                              f"{download_ep_reports(conn, http, agendas, log=out)}")
                    finally:
                        http.close()
                got = store_ep_reports(conn, ids["exhibition-place"])
                print(f"  ep stored            : {got['solicitations']} solicitations, "
                      f"{got['awards']} awards, {got['bids']} bids")
            except Exception as exc:
                failures.append(("ep", str(exc)))
```

- [ ] **Step 5: Run tests + full suite**

Run: `cd scrapers && uv run pytest tests/test_ep_reports.py -v` then `uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Live measurement (REQUIRED — the #136/#138 discipline)**

```bash
cd scrapers && TB_DATA_DIR=/tmp/ep-live uv run tb enrich-agencies --only ep --scrape --virtual-display 2>&1 | tail
```

Then measure: over all held EP reports, how many `parse_ep_report` accepts vs refuses, and eyeball a sample of the accepted awards for **clean winner names** (no project-description over-capture, no WSIB/status false positives) and the bid rows for sane prices. Record the counts in the task report. A high refusal rate is EXPECTED and correct (most EP reports aren't awards) — but a *named* award must be clean. If false positives appear, tighten `_EP_AWARD`/the refuse rule and re-measure. Clean up `/tmp/ep-live` after.

- [ ] **Step 7: Update CLAUDE.md**

In the agency-capture subsection, append:

```markdown
Exhibition Place (`sources/ep_board.py`, #130) reuses the Zoo pattern: TMMIS EP committee
(`YYYY.EP<n>.<n>`, headed-browser discovery via the shared prober) → plain-HTTP legdocs
`bgrd` PDFs → `parse_ep_report` + `parse_ep_bid_table`. **Most EP board reports are not
procurement awards** (WSIB safety, status updates, governance), so the parser anchors on an
"award of Contract/RFT … to WINNER" clause and refuses the rest; the WSIB negative fixtures
(which carry `$` amounts) guard against false positives. EP's award clause puts the project
between the winner and the amount ("to WINNER **for the <project>** in the amount of $X"), so
the winner regex is EP-specific, not the shared Zoo pattern. Amount/confidential primitives are
shared via `sources/agency_report.py`. EP is the first agency source with a structured bidder
price table (Table 1 → `agency_bid` with prices). On-demand (`--only ep --scrape`), never on
the browser-free nightly path. The pre-2019 City-spine EP slice (Client_Division "Exhibition
Place") and these post-2019 Board-of-Governors awards are separate coexisting keyspaces (#130).
```

- [ ] **Step 8: Commit**

```bash
git add scrapers/toronto_bids/sources/ep_board.py scrapers/toronto_bids/cli.py CLAUDE.md \
        scrapers/tests/test_ep_reports.py
git commit -m "feat(ep): store_ep_reports + tb enrich-agencies --only ep; document (#130)"
```

---

## After the last task

Run the full suite (`cd scrapers && uv run pytest`). The live `--only ep --scrape` run from Task 5 Step 6 is the real-world verification; its refusal-vs-accept counts and a clean-name spot-check are the evidence. Use superpowers:finishing-a-development-branch. On completion, comment on #130 with what landed (EP awards + bidder prices recovered, the pre/post-2019 coexistence answered) and close it.
