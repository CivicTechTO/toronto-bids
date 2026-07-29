# PDF Cell Tables Implementation Plan (#151, #203)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract EP board-report bid tables from PDF *cells* instead of regex over flattened text, and audit the remaining regex-over-flattened-text parsers to decide, per corpus, whether they should switch too.

**Architecture:** A new `sources/pdf_tables.py` holds every structural rule as **pure** functions (caption anchoring with page-break handling; positional column zipping) plus two thin pdfplumber I/O readers. `sources/ep_board.py`'s bid-table parser becomes pure over cells; `award_summary.py` is rewired onto the shared rules so there is one copy. `agency_bid` becomes a derived table rebuilt from held PDFs on every store pass.

**Tech Stack:** Python 3.12+, `uv`, pytest, pdfplumber 0.11.10, SQLite.

## Global Constraints

- Work on branch `fix-151-203-pdf-cells`. **Never commit to `main`** — this checkout is production; `main` deploys to live systemd timers.
- All commands run from `/home/alex/toronto-bids/scrapers`.
- The live corpus is at `TB_DATA_DIR=/home/alex/tb-data` (1,200 EP / 859 Zoo / 3,411 TRCA held reports). Tests must **never** require it.
- No lint/format/typecheck exists in this repo. Do not invent one.
- **Future maintainability, not past compatibility.** No shims, no deprecated aliases, no old-signature wrappers, no regex fallback, no inert dead code. Delete what is replaced.
- **Never chase edge cases** (CLAUDE.md, "Parsing discipline"). A wrinkle that does not collapse into an existing rule is a signal to stop and report, not to add a rule.
- **Refuse and log, never guess.** A refused row is a known gap; a guessed row is silent corruption.
- Every task ends green: `uv run pytest` passes before commit.

---

### Task 1: `choose_tables` — caption anchoring and page-break walking (pure)

This is the highest-risk logic in the change and it is deliberately pure, so it is tested with synthetic geometry and no PDF.

**Files:**
- Create: `toronto_bids/sources/pdf_tables.py`
- Test: `tests/test_pdf_tables.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `is_price(cell: str | None) -> bool`, `is_continuation(rows: list[list]) -> bool`, `choose_tables(pages) -> list[list[list]]` where `pages` is `[(caption_tops: list[float], tables: list[tuple[float, rows]]), ...]` in document order.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pdf_tables.py`:

```python
from toronto_bids.sources.pdf_tables import choose_tables, is_continuation, is_price

HDR = ["Bidder", "Bid Price Received", "Recommended\nContract Price"]
R1 = ["Powell Fence Limited", "$1,484,065.00", "$1,484,065.00"]
R2 = ["M.J.K. Construction Incorporated", "$1,619,001.00", ""]


def test_is_price_accepts_the_shapes_the_city_publishes():
    assert is_price("$1,484,065.00")
    assert is_price("$4,365,534")            # no cents (backgroundfile-229405)
    assert is_price("*$792,900.00")          # leading marker (backgroundfile-244900)
    assert is_price("$470,700.00*")          # trailing marker (backgroundfile-137241)
    assert is_price("$ 449,000.00")          # space after the sign
    assert not is_price("*Non-compliant")
    assert not is_price("Bid Price Received")
    assert not is_price("")
    assert not is_price(None)


def test_choose_tables_takes_the_first_table_below_each_caption():
    # backgroundfile-254716: two captions, two tables, on one page.
    pages = [([195.2, 386.5], [(219.8, [HDR, R1]), (411.1, [HDR, R2])])]
    assert choose_tables(pages) == [[HDR, R1], [HDR, R2]]


def test_choose_tables_ignores_a_table_above_its_caption():
    # backgroundfile-139154: an unrelated cost-breakdown table sits above the caption.
    decoy = [["Item", "Amount", "Comments"], ["Exterior Windows", "373,000", "..."]]
    pages = [([427.6], [(54.3, decoy), (452.6, [HDR, R1])])]
    assert choose_tables(pages) == [[HDR, R1]]


def test_caption_at_page_foot_finds_its_table_overleaf():
    # backgroundfile-131331: caption at y=705.8 on page 1, whole table at y=54.2 on page 2.
    pages = [([705.8], []), ([], [(54.2, [HDR, R1])])]
    assert choose_tables(pages) == [[HDR, R1]]


def test_a_table_broken_across_a_page_absorbs_its_continuation():
    # backgroundfile-244929: 2 rows on page 1, 7 more on page 2 as a headerless table.
    cont = [["Crawford Roofing Corporation", "$1,660,000.00", "$1,720,000.00"]]
    pages = [([624.7], [(635.5, [HDR, R1])]), ([], [(54.2, cont[0:1])])]
    assert choose_tables(pages) == [[HDR, R1, cont[0]]]


def test_a_next_page_with_its_own_caption_is_not_absorbed():
    # The next page's table belongs to that page's caption, not to ours.
    pages = [([624.7], [(635.5, [HDR, R1])]), ([100.0], [(120.0, [HDR, R2])])]
    assert choose_tables(pages) == [[HDR, R1], [HDR, R2]]


def test_a_next_page_opening_with_a_header_is_a_new_table_not_a_continuation():
    pages = [([624.7], [(635.5, [HDR, R1])]), ([], [(54.2, [HDR, R2])])]
    assert choose_tables(pages) == [[HDR, R1]]


def test_a_caption_with_no_table_anywhere_yields_nothing():
    assert choose_tables([([100.0], [])]) == []


def test_is_continuation_needs_a_price_in_the_second_column():
    assert is_continuation([["Crawford Roofing Corporation", "$1,660,000.00", ""]])
    assert not is_continuation([HDR])
    assert not is_continuation([])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pdf_tables.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'toronto_bids.sources.pdf_tables'`

- [ ] **Step 3: Write the implementation**

Create `toronto_bids/sources/pdf_tables.py`:

```python
"""Reading tables out of PDFs that HAVE tables (#151, #203).

The rule this module serves is #116's: **read cells where the PDF HAS cells** — never
"pdfplumber is better". Whether a given corpus qualifies is a per-corpus measurement
(CLAUDE.md, "Parsing discipline"); #83 measured a corpus where cells are *worse*. This module
is only the machinery for the corpora that qualify.

Split so the risky part needs no PDF to test: `choose_tables` and `zip_columns` are pure and
carry every structural rule; `all_tables`/`caption_tables` do the I/O and nothing else — the
same fetch/normalize seam `sources/base.py` draws for a Source.
"""
import re

# A published price, with or without cents, with a compliance marker on either side, and with
# or without a space after the sign. Every shape here is live-measured on the EP corpus: the
# old regex required `\.\d{2}` and so read `$4,365,534` (backgroundfile-229405) as "no bids".
_PRICE = re.compile(r"^[*\s]*\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?[*\s]*$")


def is_price(cell) -> bool:
    return bool(_PRICE.match((cell or "").strip()))


def is_continuation(rows) -> bool:
    """True when a table opens with DATA rather than a header — the signature of a table that
    broke across a page boundary and resumed at the top of the next page."""
    return bool(rows) and len(rows[0]) > 1 and is_price(rows[0][1])


def choose_tables(pages):
    """One table per caption, page-break continuations absorbed.

    `pages` is `[(caption_tops, [(table_top, rows), ...]), ...]` in document order — the
    geometry, with pdfplumber already out of the picture.

    A caption claims the first table BELOW it on its own page. Two page-break shapes then
    complicate that, and both are the same event so both are handled by one walk:

      - the caption sits at the foot of its page and the whole table is overleaf
        (backgroundfile-131331: caption y=705.8 page 1, table y=54.2 page 2);
      - the caption's table starts on its page and the remaining rows land at the top of the
        next page as a SEPARATE table object with no header row
        (backgroundfile-244929: 2 rows, then 7).

    Absorption requires the next page to have no caption of its own competing for the table,
    and the table to open with data rather than a header. Without this, 131331 loses its only
    bidder and 244929 seven of its nine.
    """
    out = []
    for i, (captions, tables) in enumerate(pages):
        for top in sorted(captions):
            below = [t for t in tables if t[0] > top]
            j = i
            if below:
                rows = list(min(below, key=lambda t: t[0])[1])
            elif i + 1 < len(pages) and pages[i + 1][1] and not pages[i + 1][0]:
                j = i + 1
                rows = list(min(pages[j][1], key=lambda t: t[0])[1])
            else:
                continue          # a caption with no table is absent, not someone else's rows
            while j + 1 < len(pages) and pages[j + 1][1] and not pages[j + 1][0]:
                nxt = min(pages[j + 1][1], key=lambda t: t[0])[1]
                if not is_continuation(nxt):
                    break
                rows.extend(nxt)
                j += 1
            out.append(rows)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pdf_tables.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add toronto_bids/sources/pdf_tables.py tests/test_pdf_tables.py
git commit -m "feat(pdf_tables): caption anchoring with page-break walking, pure and tested

choose_tables takes page geometry and returns one table per caption. A bid
table breaks across pages in two shapes — caption stranded at a page foot
(131331), and rows continuing overleaf as a separate headerless table
(244929, 7 of 9 rows) — and both are one event, so one walk covers both.

Pure by construction: the geometry is passed in, so the riskiest rule in
this change is tested with no PDF and no pdfplumber."
```

---

### Task 2: `zip_columns` — the positional-zip rule, one canonical copy

**Files:**
- Modify: `toronto_bids/sources/pdf_tables.py`
- Modify: `toronto_bids/sources/award_summary.py:180-215` (delete `_zip_cell`, import `zip_columns`)
- Test: `tests/test_pdf_tables.py`

**Interfaces:**
- Consumes: `toronto_bids.sources.pdf_tables` from Task 1.
- Produces: `zip_columns(name_cell: str, price_cell: str) -> list[tuple[str, str | None]]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pdf_tables.py`:

```python
from toronto_bids.sources.pdf_tables import zip_columns


def test_one_price_line_is_one_bid_even_when_the_name_wraps():
    # #116: reading a wrapped name's two lines as two names dropped a bidder from 4 forms.
    assert zip_columns("2489960 Ontario Inc.\no/a Kore Infrastructure Group",
                       "$3,198,000.00") == [
        ("2489960 Ontario Inc. o/a Kore Infrastructure Group", "$3,198,000.00")]


def test_a_multi_package_column_zips_positionally():
    assert zip_columns("26TW-CPI-17CWD (Package A):\nClean Water Works Inc.*\nAqua Tech Inc.",
                       "$3,551,718.88\n$3,978,656.19") == [
        ("Clean Water Works Inc.*", "$3,551,718.88"),
        ("Aqua Tech Inc.", "$3,978,656.19")]


def test_unequal_columns_are_refused_rather_than_guessed():
    # #94: pairing is positional, so one stray line misattributes every bid after it.
    assert zip_columns("A Ltd.\nB Ltd.\nC Ltd.", "$1.00\n$2.00") == []


def test_a_proponent_with_no_price_at_all_is_still_a_bid():
    # An RFP publishes proponents with "NOTE: Not applicable for RFP" (#84 stores price NULL).
    assert zip_columns("Some Consulting Inc.", "") == [("Some Consulting Inc.", None)]


def test_an_empty_name_yields_nothing():
    assert zip_columns("", "$1.00") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pdf_tables.py -k zip -v`
Expected: FAIL — `ImportError: cannot import name 'zip_columns'`

- [ ] **Step 3: Add `zip_columns` to `pdf_tables.py`**

Append to `toronto_bids/sources/pdf_tables.py`:

```python
def zip_columns(name_cell, price_cell):
    """Pair one row's bidder lines against its price lines.

    Usually a row is one bidder. A multi-package tender instead puts a whole column in a single
    cell, exactly as the BD agendas did (#94). Same rule, same reason: pairing is positional, so
    one stray line misattributes every bid after it. Zip the columns and REFUSE an unequal pair
    rather than guess. A package heading is dropped first — it ends in ':' and has no price.

    **THE PRICE CELL'S LINE COUNT SAYS HOW MANY BIDS THE ROW HOLDS.** A newline inside a name
    cell is otherwise ambiguous, and guessing costs real bids: pdfplumber wraps a long name
    within its own cell, so

        ['2489960 Ontario Inc.\\no/a Kore Infrastructure Group', '$3,198,000.00']

    is ONE bidder, and reading its two lines as two names refused the pair and silently dropped
    a bidder from each of 4 forms (#116). One price, one bid — join the name.
    """
    prices = [ln.strip() for ln in (price_cell or "").split("\n") if ln.strip()]
    names = [ln.strip() for ln in (name_cell or "").split("\n") if ln.strip()]
    names = [n for n in names if not n.endswith(":")]
    if len(prices) <= 1:
        name = " ".join(names)
        if not name:
            return []
        # An RFP publishes its proponents with NO price at all ("NOTE: Not applicable for
        # RFP"). #84 already stores those as bid_price NULL — requiring a price here dropped
        # every proponent on every scored RFP.
        return [(name, prices[0] if prices else None)]
    if len(names) != len(prices):
        return []
    return list(zip(names, prices))
```

- [ ] **Step 4: Rewire `award_summary.py` onto it**

In `toronto_bids/sources/award_summary.py`, **delete the entire `_zip_cell` function** (lines 180-215) and add the import at the top of the `# --- parsing ---` block:

```python
from toronto_bids.sources.pdf_tables import zip_columns
```

Then in `parse_award_summary`, change the one call site:

```python
        for name, price in zip_columns(label, value):
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. `tests/test_award_summary.py` must still be green — it exercises the wrapped-name, multi-package and unequal-pair cases through `parse_award_summary`, which is the proof the rewire is behaviour-preserving.

- [ ] **Step 6: Commit**

```bash
git add toronto_bids/sources/pdf_tables.py toronto_bids/sources/award_summary.py tests/test_pdf_tables.py
git commit -m "refactor(pdf_tables): one canonical copy of #94's positional-zip rule

The rule has now been independently re-derived three times (#94's BD agenda
tables, #116's Award Summary Forms, and EP), which is the signal that it is
one rule rather than three similar ones. award_summary._zip_cell is deleted
and that module imports zip_columns instead.

Two correct copies are exactly the thing that diverges the first time one is
fixed; the existing award_summary tests pass unchanged, which is the proof
the rewire preserves behaviour."
```

---

### Task 3: the pdfplumber readers

**Files:**
- Modify: `toronto_bids/sources/pdf_tables.py`
- Modify: `toronto_bids/sources/award_summary.py:165-177` (delete `form_rows`'s body, delegate)
- Test: `tests/test_pdf_tables.py`

**Interfaces:**
- Consumes: `choose_tables` from Task 1.
- Produces: `all_tables(path) -> list[list[str]]` (flattened non-empty rows, empty cells dropped), `caption_tables(path, caption_re: re.Pattern) -> list[list[list]]` (raw cells, empties **kept**).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pdf_tables.py`:

```python
import pathlib
import re

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_caption_tables_on_a_pdf_with_no_tables_returns_nothing():
    pytest.importorskip("pdfplumber")
    from toronto_bids.sources.pdf_tables import caption_tables
    assert caption_tables(FIXTURES / "tiny.pdf", re.compile(r"Table\s+1", re.I)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pdf_tables.py -k caption_tables -v`
Expected: FAIL — `ImportError: cannot import name 'caption_tables'`

- [ ] **Step 3: Add the readers**

Append to `toronto_bids/sources/pdf_tables.py`:

```python
def all_tables(path):
    """Every non-empty row of every table in the PDF, as stripped cells with empties DROPPED.

    Column position is not preserved — this is for forms read as (label, value) pairs, where a
    blank trailing cell is noise. Use `caption_tables` when column INDEX matters. Does I/O.
    """
    import pdfplumber

    rows = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    cells = [(c or "").strip() for c in row]
                    if any(cells):
                        rows.append([c for c in cells if c])
    return rows


def caption_tables(path, caption_re):
    """The table under each `caption_re` match, as RAW cells — empties KEPT.

    Column index is load-bearing here (column 1 is the price column whether or not column 2 is
    blank), so unlike `all_tables` this must not compact a row. Does I/O; every structural
    decision belongs to `choose_tables`, which is pure.
    """
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            captions = [m["top"] for m in
                        page.search(caption_re.pattern, regex=True, case=False)]
            tables = [(t.bbox[1], t.extract()) for t in page.find_tables()]
            pages.append((captions, tables))
    return choose_tables(pages)
```

- [ ] **Step 4: Delegate `award_summary.form_rows`**

Replace the body of `form_rows` in `toronto_bids/sources/award_summary.py` (keep the name — it is the domain term used throughout that module):

```python
def form_rows(path) -> list[list[str]]:
    """Every non-empty row of every ruled table in the form, as stripped cells. Does I/O."""
    from toronto_bids.sources.pdf_tables import all_tables

    return all_tables(path)
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add toronto_bids/sources/pdf_tables.py toronto_bids/sources/award_summary.py tests/test_pdf_tables.py
git commit -m "feat(pdf_tables): pdfplumber readers, with the empty-cell rule made explicit

all_tables drops empty cells (forms read as label/value pairs); caption_tables
keeps them, because for a bid table column INDEX is load-bearing — column 1 is
the price column whether or not column 2 is blank. Conflating those two would
silently shift a row's columns.

award_summary.form_rows delegates to all_tables and keeps its name, which is
the domain term the rest of that module uses."
```

---

### Task 4: EP bid table reads cells (pure parser + fixtures)

**Files:**
- Modify: `toronto_bids/sources/ep_board.py:141-167` (delete `_EP_BID_ROW` and the 1,500-char window; rewrite `parse_ep_bid_table`)
- Create: `tests/fixtures/agencies/ep_bid_table_2023_three_bidders.json`
- Create: `tests/fixtures/agencies/ep_bid_table_2019_five_bidders.json`
- Create: `tests/fixtures/agencies/ep_bid_table_noncompliant.json`
- Create: `tests/fixtures/agencies/ep_bid_table_no_cents.json`
- Modify: `tests/test_ep_reports.py:108-132` (rewrite the three bid-table tests)

**Interfaces:**
- Consumes: `pdf_tables.is_price` (Task 1).
- Produces: `parse_ep_bid_table(tables: list[list[list]]) -> list[tuple[str, str | None]]` — **pure**, takes what `caption_tables` returned.

- [ ] **Step 1: Create the fixtures**

These are the real cells from the live corpus. `tests/fixtures/agencies/ep_bid_table_2023_three_bidders.json` (backgroundfile-240943):

```json
[[["Bidder", "Base Bid Price\nReceived", "Recommended\nContract Price"],
  ["Powell Fence Limited", "$1,484,065.00", "$1,484,065.00"],
  ["M.J.K. Construction Incorporated", "$1,619,001.00", ""],
  ["Clearway Construction Incorporated", "$1,851,100.00", ""]]]
```

`tests/fixtures/agencies/ep_bid_table_2019_five_bidders.json` (backgroundfile-137241 — note the header says `Tenderer`, and the marker is *inside* the price cell):

```json
[[["Tenderer", "Tender Price\nReceived", "Recommended\nContract Price"],
  ["Sutherland-Schultz Ltd.", "$418,854.47", "$403,854.47"],
  ["Ontario Electrical Construction Co. Ltd.", "$461,522.00", ""],
  ["Modern Niagara Toronto Inc.", "$470,700.00*", ""],
  ["Stevens & Black Electrical Contractors Ltd.", "$518,000.00", ""],
  ["Rogol Electric Company Limited", "$546,350.00", ""]]]
```

`tests/fixtures/agencies/ep_bid_table_noncompliant.json` (backgroundfile-238906 — declares 8 bids, and only reaches 8 because the outcome rows count):

```json
[[["Bidder", "Base Bid Price\nReceived", "Additional\nPrices\nIncluded", "Recommended\nTotal Contract\nPrice"],
  ["CMS Electrical Group Ltd.", "$1,095,900.00", "$860,239.00", "$1,956,139.00"],
  ["Energy Network Services Inc.", "$1,223,029.36", "", ""],
  ["Kudlak-Baird (1982) Limited", "$2,334,973.00", "", ""],
  ["Modern Niagara Toronto Inc.", "$1,496,274.61", "", ""],
  ["OZZ Electric Inc.", "$1,328,914.00", "", ""],
  ["Stevens & Black Electrical\nContractors Ltd.", "$1,576,400.00", "", ""],
  ["* Plaza Electric Ltd.", "*Non-compliant", "", ""],
  ["* Trade Electrical Contractors Inc.", "*Non-compliant", "", ""]]]
```

`tests/fixtures/agencies/ep_bid_table_no_cents.json` (backgroundfile-229405):

```json
[[["Bidder", "Base Bid Price\nReceived", "Recommended Contract\nPrice"],
  ["Black & McDonald Limited", "$2,619,221", "$2,619,221"]]]
```

- [ ] **Step 2: Write the failing tests**

Replace `tests/test_ep_reports.py` lines 108-132 (the three `parse_ep_bid_table` tests) with:

```python
import json


def _rows(name):
    return json.loads((FIXTURES / name).read_text())


def test_bid_table_extracts_all_three_bidders_with_prices():
    assert parse_ep_bid_table(_rows("ep_bid_table_2023_three_bidders.json")) == [
        ("Powell Fence Limited", "$1,484,065.00"),
        ("M.J.K. Construction Incorporated", "$1,619,001.00"),
        ("Clearway Construction Incorporated", "$1,851,100.00"),
    ]


def test_bid_table_2019_five_bidders_strips_a_marker_inside_the_price_cell():
    # The header here says "Tenderer", not "Bidder" — the header row is rejected because its
    # price column holds no price, never by a denylist of header words.
    assert parse_ep_bid_table(_rows("ep_bid_table_2019_five_bidders.json")) == [
        ("Sutherland-Schultz Ltd.", "$418,854.47"),
        ("Ontario Electrical Construction Co. Ltd.", "$461,522.00"),
        ("Modern Niagara Toronto Inc.", "$470,700.00"),      # trailing * stripped
        ("Stevens & Black Electrical Contractors Ltd.", "$518,000.00"),
        ("Rogol Electric Company Limited", "$546,350.00"),
    ]


def test_bid_table_keeps_a_non_compliant_bidder_with_a_null_price():
    # #94's rule: the City writes OUTCOMES in the price column. The bidder is still a bid.
    # backgroundfile-238906 declares 8 bids and only reaches 8 because these two count.
    got = parse_ep_bid_table(_rows("ep_bid_table_noncompliant.json"))
    assert len(got) == 8
    assert got[-2:] == [("Plaza Electric Ltd.", None),        # leading marker stripped from name
                        ("Trade Electrical Contractors Inc.", None)]


def test_bid_table_accepts_a_price_with_no_cents():
    # The old regex required \\.\\d{2} and read this report as having no bids at all.
    assert parse_ep_bid_table(_rows("ep_bid_table_no_cents.json")) == [
        ("Black & McDonald Limited", "$2,619,221")]


def test_bid_table_wrapped_name_arrives_whole():
    rows = [[["Bidder", "Bid Price Received", "Recommended\nContract Price"],
             ["Enercare Home and\nCommercial Services Limited Partnership", "$653,323.00", ""]]]
    assert parse_ep_bid_table(rows) == [
        ("Enercare Home and Commercial Services Limited Partnership", "$653,323.00")]


def test_bid_table_absent_returns_empty():
    assert parse_ep_bid_table([]) == []


def test_bid_table_refuses_a_row_whose_price_column_is_neither_price_nor_outcome():
    # Refuse, never guess — a row we cannot read is a gap, not an invented bid.
    rows = [[["Bidder", "Bid Price Received"], ["Some Firm Ltd.", "see Appendix B"]]]
    assert parse_ep_bid_table(rows) == []
```

Also delete the now-unused `_read`-based bid-table calls; `_read` stays because `parse_ep_report` still uses it.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_ep_reports.py -k bid_table -v`
Expected: FAIL — the current `parse_ep_bid_table` takes text, so passing a list raises `TypeError: expected string or bytes-like object`.

- [ ] **Step 4: Rewrite the parser**

In `toronto_bids/sources/ep_board.py`, **delete `_EP_BID_ROW` entirely** and replace the `parse_ep_bid_table` block (lines 141-167) with:

```python
# The Table 1 caption. UNCHANGED from the regex era, and it already excludes
# "Table 2: Tender Separate Price Submission" — after "Table 2" that text reads "Tender
# Separate Price Submission", so there is no "Tender Price Submission" to match. That is what
# identifies Table 1 without hardcoding the digit.
_EP_TABLE_HEAD = re.compile(r"Table\s+\d[^\n]*Tender\s+Price\s+Submission", re.I)
# The City writes OUTCOMES in the price column instead of a number — the same practice #94
# documented on the BD agendas, where the raw string is kept and bid_price_numeric is NULL for
# exactly those. Such a row is still a bid; its price is simply not a number.
_EP_OUTCOME = re.compile(r"^[*\s]*(?:non[-\s]?compliant|no\s+bid|not\s+applicable|withdrawn|"
                         r"disqualified|incomplete)\b", re.I)
# Compliance markers leading or trailing a name ("* Plaza Electric Ltd."), stripped so the firm
# keys consistently in the supplier dimension.
_MARKERS = re.compile(r"^[\s*^+†‡§]+|[\s*^+†‡§]+$")


def ep_bid_tables(path):
    """The report's 'Table 1: Tender Price Submission' as cells. Does I/O."""
    return pdf_tables.caption_tables(path, _EP_TABLE_HEAD)


def parse_ep_bid_table(tables) -> list[tuple[str, str | None]]:
    """Every (bidder, price) in an EP Table 1. Pure over the cells ep_bid_tables() read.

    Four rules, and they were measured to converge rather than proliferate (#151): the caption
    anchor and page-break walk live in pdf_tables.choose_tables; this function is the row rule
    and the normalisation.

    A row is a bid when column 0 names something and column 1 holds either a price or an
    outcome. The header row is rejected structurally by that same test, never by a denylist of
    header words — column 0 is variously "Bidder" and "Tenderer", and column 1 variously
    "Bid Price Received", "Base Bid Price\\nReceived" and "Initial Base Bid\\nPrice Received".

    Anything else is REFUSED rather than guessed at: backgroundfile-254543 carries a malformed
    price in the City's own PDF ($1,479,386,.57) and yields one bid instead of two, which is
    the document's defect and is not something to add a rule for.
    """
    if not tables:
        return []
    out = []
    for row in tables[0]:
        cells = [(c or "").strip() for c in row]
        if len(cells) < 2 or not cells[0]:
            continue
        name = _MARKERS.sub("", _WS.sub(" ", cells[0]).strip())
        if not name:
            continue
        if pdf_tables.is_price(cells[1]):
            out.append((name, cells[1].strip("* ").replace(" ", "")))
        elif _EP_OUTCOME.match(cells[1]):
            out.append((name, None))
    return out
```

Add the import at the top of `ep_board.py`, beside the existing source imports:

```python
from toronto_bids.sources import pdf_tables
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_ep_reports.py -v`
Expected: the seven bid-table tests PASS. `test_store_ep_reports_lands_award_and_bids` FAILS — it feeds `background_pdf.text` and expects 3 bids, and that path no longer exists. Task 5 fixes it.

- [ ] **Step 6: Commit**

```bash
git add toronto_bids/sources/ep_board.py tests/test_ep_reports.py tests/fixtures/agencies/ep_bid_table_*.json
git commit -m "feat(ep): read the bid table as cells; delete the regex and its 1,500-char window

#151's window truncated nothing (longest real table 732 chars) and instead
over-reached, pulling Table 2 duplicates and prose into agency_bid. Cells make
both impossible by construction.

The row rule is structural: column 0 names something, column 1 holds a price
or an outcome. That rejects the header row without a denylist, which matters
because the header is variously Bidder/Tenderer and Bid Price Received/Base
Bid Price Received/Initial Base Bid Price Received.

Two rules carried over rather than invented: an outcome in the price column is
still a bid with a NULL price (#94), and a numeric-leading firm name is real
(#87/#116). Both were bidders the regex silently dropped.

_EP_BID_ROW and the window are deleted outright — no flag, no fallback. A
fallback would reimport exactly the prose contamination this removes.

Test failure in test_store_ep_reports_lands_award_and_bids is expected and is
fixed in the next commit."
```

---

### Task 5: `rebuild_agency_bids` and the store pass

**Files:**
- Modify: `toronto_bids/store/db.py` (add `rebuild_agency_bids`)
- Modify: `toronto_bids/sources/ep_board.py:170-198` (`store_ep_reports`)
- Test: `tests/test_ep_reports.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `parse_ep_bid_table`, `ep_bid_tables` (Task 4).
- Produces: `db.rebuild_agency_bids(conn, source: str, rows: list[AgencyBid]) -> int`; `store_ep_reports(conn, buyer_id, tables_for=ep_bid_tables) -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_rebuild_agency_bids_replaces_only_its_own_source(conn):
    from toronto_bids.models import AgencyBid
    from toronto_bids.store import db
    keep = AgencyBid(buyer_id=1, native_ref="T-1", bidder_name_raw="Keep Ltd.",
                     bid_price="$1.00", source="trca_board")
    stale = AgencyBid(buyer_id=1, native_ref="E-1", bidder_name_raw="Phantom",
                      bid_price="$2.00", source="ep_board")
    db.upsert_row(conn, keep, overwrite=True)
    db.upsert_row(conn, stale, overwrite=True)
    fresh = [AgencyBid(buyer_id=1, native_ref="E-1", bidder_name_raw="Real Ltd.",
                       bid_price="$3.00", source="ep_board")]
    assert db.rebuild_agency_bids(conn, "ep_board", fresh) == 1
    names = {r["bidder_name_raw"] for r in conn.execute("SELECT bidder_name_raw FROM agency_bid")}
    assert names == {"Keep Ltd.", "Real Ltd."}          # phantom gone, TRCA untouched


def test_rebuild_agency_bids_deletes_nothing_when_it_derived_nothing(conn):
    from toronto_bids.models import AgencyBid
    from toronto_bids.store import db
    db.upsert_row(conn, AgencyBid(buyer_id=1, native_ref="E-1", bidder_name_raw="Held Ltd.",
                                  bid_price="$1.00", source="ep_board"), overwrite=True)
    # A machine that holds no PDFs derives no rows, and must therefore delete none.
    assert db.rebuild_agency_bids(conn, "ep_board", []) == 0
    assert conn.execute("SELECT COUNT(*) FROM agency_bid").fetchone()[0] == 1
```

Replace `test_store_ep_reports_lands_award_and_bids` in `tests/test_ep_reports.py`:

```python
def test_store_ep_reports_lands_award_and_bids(conn):
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.sources.ep_board import store_ep_reports
    ids = seed_buyers(conn)
    conn.execute("INSERT INTO background_pdf (url, kind, sha256, local_path, text) VALUES "
                 "('https://www.toronto.ca/legdocs/mmis/2023/ep/bgrd/backgroundfile-240943.pdf',"
                 " 'agency_board', 'x', '/nowhere/x.pdf', ?)",
                 (_read("ep_award_with_table_2023.txt"),))
    conn.commit()
    # The bid table now comes from the PDF's cells, so the reader is injected: a real EP report
    # is ~113 KB, too heavy to commit as a fixture, and the seam is the same fetch/normalize
    # boundary sources/base.py draws.
    got = store_ep_reports(conn, ids["exhibition-place"],
                           tables_for=lambda _p: _rows("ep_bid_table_2023_three_bidders.json"))
    assert got["solicitations"] == 1 and got["awards"] == 1 and got["bids"] == 3
    aw = conn.execute("SELECT supplier_name_raw, award_amount_numeric FROM agency_award "
                      "WHERE native_ref='EP110-2023'").fetchone()
    assert aw["supplier_name_raw"] == "Powell Fence Limited"
    assert aw["award_amount_numeric"] == 1484065.00
    assert conn.execute("SELECT COUNT(*) FROM agency_bid WHERE bid_price_numeric IS NOT NULL "
                        "AND native_ref='EP110-2023'").fetchone()[0] == 3


def test_store_ep_reports_survives_an_unreadable_pdf(conn):
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.sources.ep_board import store_ep_reports
    ids = seed_buyers(conn)
    conn.execute("INSERT INTO background_pdf (url, kind, sha256, local_path, text) VALUES "
                 "('https://www.toronto.ca/legdocs/mmis/2023/ep/bgrd/backgroundfile-240943.pdf',"
                 " 'agency_board', 'x', '/nowhere/x.pdf', ?)",
                 (_read("ep_award_with_table_2023.txt"),))
    conn.commit()

    def boom(_path):
        raise OSError("no such file")

    logged = []
    got = store_ep_reports(conn, ids["exhibition-place"], tables_for=boom,
                           log=logged.append)
    assert got["awards"] == 1 and got["bids"] == 0     # the award still lands
    assert any("240943" in m for m in logged)          # and the gap is never silent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k rebuild tests/test_ep_reports.py -k store -v`
Expected: FAIL — `AttributeError: module 'toronto_bids.store.db' has no attribute 'rebuild_agency_bids'` and `store_ep_reports() got an unexpected keyword argument 'tables_for'`.

- [ ] **Step 3: Add `rebuild_agency_bids`**

Append to `toronto_bids/store/db.py`:

```python
def rebuild_agency_bids(conn, source: str, rows) -> int:
    """Replace every `agency_bid` row for one source with a freshly derived set.

    `agency_bid` is DERIVED from held PDFs, so it is rebuilt from the bytes rather than
    diff-upserted — the sanctioned exception to "rows are never deleted" that
    `build_supplier_dimension` and `enrich-ariba-attachments --reindex` already take, and for
    the same reason.

    This is a permanent contract, not a migration. A table whose contents must be corrected
    once by hand is a table whose derivation should simply be re-run: a migration would encode
    today's list of known-bad rows and need a successor after the next parser fix. Rebuilding
    means every parser fix self-heals, with no list written down anywhere.

    **Derive first, delete only on success.** `rows` is already materialised by the caller, and
    an empty set deletes NOTHING — a machine that does not hold the PDFs re-derives nothing and
    must not thereby erase the archive. Scoped to one `source`, so rebuilding EP cannot touch
    TRCA's or the Zoo's rows.
    """
    rows = list(rows)
    if not rows:
        return 0
    try:
        conn.execute("DELETE FROM agency_bid WHERE source=?", (source,))
        for row in rows:
            upsert_row(conn, row, overwrite=True)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)
```

- [ ] **Step 4: Rewrite `store_ep_reports`**

Replace `store_ep_reports` in `toronto_bids/sources/ep_board.py`:

```python
def store_ep_reports(conn, buyer_id: int, tables_for=None, log=lambda _m: None) -> dict:
    """Parse held EP reports into agency rows. One AgencySolicitation + AgencyAward per award
    report (confidential ones keep the winner, NULL amount), and one AgencyBid per Table 1 row.
    Non-award reports are refused by parse_ep_report and contribute nothing.

    Bids are read from the PDF's own CELLS (#151), so `tables_for` is injected: it defaults to
    `ep_bid_tables` and is overridden in tests, which must not need a 113 KB PDF on disk.

    `agency_bid` for this source is REBUILT, not upserted (see db.rebuild_agency_bids): the
    whole set is derived first and only then replaces what is stored, so a parser fix
    self-heals and a missing corpus deletes nothing.
    """
    tables_for = tables_for if tables_for is not None else ep_bid_tables
    counts = {"solicitations": 0, "awards": 0, "bids": 0}
    bids: list[AgencyBid] = []
    for row in conn.execute(
            "SELECT reference, url, text, local_path FROM background_pdf WHERE kind='agency_board' "
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
        if not row["local_path"]:
            continue
        # `local_path` is an ABSOLUTE path baked in on whichever machine fetched the report, and
        # this archive is designed to migrate. The file's identity is its content-addressed
        # <sha256>.pdf name; its location is deterministic under the CURRENT data dir.
        path = config.EP_REPORTS_DIR / pathlib.Path(row["local_path"]).name
        try:
            tables = tables_for(path)
        except Exception as exc:              # noqa: BLE001 — one unreadable PDF must never
            # abort the pass, but it must never be silent either: a skip nobody can see is a
            # skip nobody fixes (#175).
            log(f"    ep unreadable {row['url'].rsplit('/', 1)[-1]}: {exc}")
            continue
        for bidder, price in parse_ep_bid_table(tables):
            bids.append(AgencyBid(
                buyer_id=buyer_id, native_ref=got["native_ref"], bidder_name_raw=bidder,
                bid_price=price, report_url=row["url"], source="ep_board"))
    conn.commit()
    counts["bids"] = db.rebuild_agency_bids(conn, "ep_board", bids)
    return counts
```

Add `import pathlib` to the top of `ep_board.py`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add toronto_bids/store/db.py toronto_bids/sources/ep_board.py tests/test_db.py tests/test_ep_reports.py
git commit -m "feat(ep): derive agency_bid from held PDFs every run instead of upserting

agency_bid for a source is now REBUILT from the bytes, the same sanctioned
exception build_supplier_dimension and --reindex already take. That is a
permanent contract rather than a migration: a one-off cleanup would encode
today's list of known-bad rows and need a successor after the next parser fix,
whereas rebuilding means every fix self-heals. The 13 phantom 'The recommended
construction price of' rows disappear as an ordinary consequence.

Derive first, delete only on success — an empty derivation deletes nothing, so
a machine without the PDFs cannot erase the archive.

store_ep_reports takes its table reader as a parameter (defaulting to
ep_bid_tables): a real EP report is ~113 KB, too heavy to commit, and the seam
is the fetch/normalize boundary sources/base.py already draws. An unreadable
PDF is logged and skipped; the award still lands."
```

---

### Task 6: verify against the live corpus

Tests prove the rules; only the corpus proves the outcome. This task writes no shipped code.

**Files:**
- Create: `/tmp/claude-1000/-home-alex-toronto-bids/29bdc5d2-8380-4ad0-af3a-6c0de52f2f55/scratchpad/verify_ep.py` (throwaway, not committed)

- [ ] **Step 1: Run the store pass against the real corpus**

```bash
cd /home/alex/toronto-bids/scrapers
TB_DATA_DIR=/home/alex/tb-data uv run tb enrich-agencies --only ep
```

Expected: completes without traceback, and prints an EP line whose bid count is **153** (up from 115 stored under the regex).

- [ ] **Step 2: Confirm the contamination is gone and the gains are present**

```bash
TB_DATA_DIR=/home/alex/tb-data uv run python -c "
from toronto_bids.store import db
from toronto_bids import config
c = db.connect(config.DB_PATH)
q = lambda s, *a: c.execute(s, a).fetchone()[0]
print('ep bids            :', q(\"SELECT COUNT(*) FROM agency_bid WHERE source='ep_board'\"))
print('prose phantoms     :', q(\"SELECT COUNT(*) FROM agency_bid WHERE source='ep_board' AND bidder_name_raw LIKE 'The recommended%'\"))
print('truncated fragment :', q(\"SELECT COUNT(*) FROM agency_bid WHERE source='ep_board' AND bidder_name_raw='Triumph Roofing & Sheet'\"))
print('numeric-led firms  :', q(\"SELECT COUNT(*) FROM agency_bid WHERE source='ep_board' AND bidder_name_raw GLOB '[0-9]*'\"))
print('null-price bids    :', q(\"SELECT COUNT(*) FROM agency_bid WHERE source='ep_board' AND bid_price IS NULL\"))
print('trca untouched     :', q(\"SELECT COUNT(*) FROM agency_bid WHERE source='trca_board'\"))
"
```

Expected: `ep bids` ≈ 153; `prose phantoms` **0**; `truncated fragment` **0**; `numeric-led firms` ≥ 2; `null-price bids` ≈ 16; `trca untouched` **408**.

- [ ] **Step 3: Confirm idempotency**

Run the same `tb enrich-agencies --only ep` a second time. Expected: the same bid count, not double. This is what the rebuild contract guarantees.

- [ ] **Step 4: Time it**

Expected from the audit: ~15s for the EP store pass. If it materially exceeds that, **do not add a "skip if rows exist" guard** — that breaks the rebuild contract. Record the figure and stop; the cache design (sha256 + parser-version stamp) is a separate decision for the user.

- [ ] **Step 5: Commit nothing; report the numbers**

No code changes here. Report the measured figures — they go into the #151 issue comment in Task 10.

---

### Task 7: audit TRCA (#203) — measure, then decide

**STOP-AND-ASK GATE.** One measurement pass and **one** refinement pass. Track the rule count. If it climbs — each fix revealing a wrinkle somewhere else — **stop, do not write the next rule, report to the user**, and default to keeping the regex. "TRCA cannot be cleanly extracted" is a complete, acceptable answer.

**Files:**
- Create: `.../scratchpad/audit_trca.py` (throwaway, not committed)

- [ ] **Step 1: Find TRCA's own ground truth**

The incumbent parser is not a baseline. Locate a declared count in the report text:

```bash
cd /home/alex/toronto-bids/scrapers
TB_DATA_DIR=/home/alex/tb-data uv run python -c "
import re
from toronto_bids.store import db
from toronto_bids import config
c = db.connect(config.DB_PATH)
rows = c.execute(\"SELECT text FROM background_pdf WHERE kind='agency_board' AND url LIKE '%escribemeetings%' AND text IS NOT NULL\").fetchall()
print('trca reports with text:', len(rows))
for label, p in {
  'N bids received': r'(\w+)\s*\(?\d*\)?\s+(?:bids|tenders|proposals|submissions)\s+(?:were|was)\s+received',
  'Number of Bids': r'Number\s+of\s+(?:Bids|Tenders|Proposals)',
  'RESULTS table': r'\bRESULTS?\b',
}.items():
    print(f'  {label:20s}', sum(1 for r in rows if re.search(p, r['text'], re.I)))
"
```

If no ground-truth anchor exists on a usable fraction of the corpus, **say so and stop** — without it, step 3 cannot distinguish a good parser from a confident one, and that is itself a finding worth reporting.

- [ ] **Step 2: Measure ruled-table coverage on the RESULTS table specifically**

Not "any table on the page" — the specific table the parser targets. Adapt `scratchpad/audit_ep.py`, replacing the caption regex with TRCA's results-table caption and pointing `config.TRCA_REPORTS_DIR` at the corpus. Report: reports with the target table / reports where cells found it.

- [ ] **Step 3: Run the four checks**

Junk scan, duplicate scan, declared-count agreement, and disagreement classification against the current parser. Same script shape as `audit_ep.py`.

- [ ] **Step 4: Apply the switch criterion**

Switch iff the target table is found on effectively every report that has one, output carries no junk and no duplicates, it agrees with declared counts except where the document is at fault, and **no real rows are lost**. TRCA's specific sub-fork: its 408 bids currently carry **zero prices** and come from the bullet list, so a good result here is a data *gain*. If cells reach reports the bullet list cannot and vice versa, a union is legitimate — but it ships as **one code path with a documented precedence**, never two parsers racing, and if the numbers land ambiguously **return the choice to the user**.

- [ ] **Step 5: Record the verdict**

Write the measurement into the #203 comment draft (Task 10) whichever way it goes. A corpus abandoned on measurement is a success of this audit.

- [ ] **Step 6: If and only if the criterion is met, implement**

Follow Tasks 4-6's shape exactly: pure parser over cells with JSON-rows fixtures, `tables_for` injection, `db.rebuild_agency_bids(conn, "trca_board", rows)`, corpus verification. Commit separately from the audit.

---

### Task 8: audit Zoo (#203)

- [ ] **Step 1: Repeat Task 7's steps 1-5 against the Zoo corpus**

859 held reports, `config.ZOO_REPORTS_DIR`, `url LIKE '%/zb/%'`, source `zoo_board`. Note the Zoo currently stores **0** `agency_bid` rows, so there is no incumbent output to compare against — ground truth and the junk scan carry the whole verdict here, which makes step 1 mandatory rather than merely advisable.

- [ ] **Step 2: Apply the same gate and criterion**

Same stop-and-ask rule. Same "prose is a valid answer" outcome.

- [ ] **Step 3: Implement only if the criterion is met**, following Tasks 4-6.

---

### Task 9: audit committee award reports (#203)

- [ ] **Step 1: Measure, with the ceiling stated up front**

8 held reports. #164 already measured that **RFTs/RFQs tabulate bids while RFPs narrate them**, and that 6 of 8 yield nothing for that reason. Report the ruled-table finding against that known ceiling rather than presenting 8 reports as a statistical result.

- [ ] **Step 2: Expect and accept "too small to switch"**

A corpus this size cannot justify a parser migration on its own. The likely correct outcome is a recorded measurement and no code change — which is a complete answer, exactly as #83's was.

---

### Task 10: document the outcomes

**Files:**
- Modify: `CLAUDE.md` (the `### Agency capture` section's EP paragraph, and `### Committee/Council awards` if Task 9 changes anything)
- Create: `docs/superpowers/notes/2026-07-28-parser-audit-findings.md` (the issue-comment drafts)

- [ ] **Step 1: Update CLAUDE.md's EP paragraph**

`### Agency capture` currently says EP is "the first agency source with a structured bidder price table (Table 1 → `agency_bid` with prices)". Extend it to record that EP reads **cells**, that the corpus is ruled 47/47, and the two rules that carried over rather than being invented (page-break walk; outcome-in-the-price-column is #94's rule). Keep it to the load-bearing facts — the reasoning lives in the spec.

- [ ] **Step 2: Write the issue comments**

Draft one comment for #151 (the EP result: 47/47, 153 rows, 0 junk, 0 duplicates, 32/35 against declared counts, 0 losses, 14+ recoveries, and the three document-defect residuals) and one for #203 (the per-corpus verdicts from Tasks 7-9, including any "kept as prose" outcomes and why).

- [ ] **Step 3: Post them**

The user authorised posting comments to #151 and #203 directly for this task.

```bash
gh api repos/CivicTechTO/toronto-bids/issues/151/comments -f body="$(cat docs/superpowers/notes/2026-07-28-parser-audit-findings.md)"
```

Post the #203 half to `issues/203/comments`. Note `gh issue create` is blocked by a classifier — `gh api` is the working path.

- [ ] **Step 4: Commit and open the PR**

```bash
git add CLAUDE.md docs/
git commit -m "docs: record the EP cell switch and the #203 per-corpus verdicts"
git push -u origin fix-151-203-pdf-cells
gh pr create --title "Read EP bid tables as cells (#151); audit the remaining regex parsers (#203)" --body "..."
```

---

## Self-Review

**Spec coverage.** §1 `pdf_tables` → Tasks 1-3. §2 EP cells → Task 4. §3 rebuild contract → Task 5. §4 audit + stop-and-ask gate → Tasks 7-9. §5 testing → folded into each task. §6 sequencing → task order. Risks: caption anchoring → Task 1's decoy-table test plus Task 6; rebuild safety → Task 5's empty-derivation test; pdfplumber cost → Task 6 step 4; partly-ruled corpus → Task 7 step 4's union rule.

**Placeholders.** None. The audit tasks (7-9) specify exact commands and an exact decision rule; their *outcome* is unknown by design, which is what an audit is, and the gate makes both outcomes actionable.

**Type consistency.** `choose_tables(pages)` takes `[(caption_tops, [(table_top, rows)])]` in Task 1 and is called that way by `caption_tables` in Task 3. `parse_ep_bid_table(tables)` takes the list-of-tables `caption_tables` returns in both Task 4 and Task 5. `rebuild_agency_bids(conn, source, rows)` matches between Task 5's definition and its two call sites. `is_price` is used by `is_continuation` (Task 1) and `parse_ep_bid_table` (Task 4) with the same signature.
