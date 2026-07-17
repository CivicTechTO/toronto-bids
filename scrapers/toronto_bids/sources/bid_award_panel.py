"""Mine solicitation titles from Bid Award Panel agendas (#65, and fills #68's council_item).

The City publishes the document number *as* the title for ~72% of solicitations, so
`solicitation.title` is NULL for 5,391 of 7,444 (see title.py / #70). The subject exists in
exactly one accessible place: the Bid Award Panel agenda that approved the award.

    BA189.1 - Award of Ariba Document Number 3234668279 to GHD Limited for the Aeration
              Blower System Upgrades at the Humber Treatment Plant

Reach is bounded by history, not effort. Toronto adopted Ariba around 2019; before that the
same agendas identify awards by Call Number (2017-01-04, BA1.2: "Award of Call Number
6032-16-3114 to MeteoGroup..."), and our spine is keyed on the 10-digit Ariba number that was
backfilled later. `Contract_Number_Purchase_Order` is empty on all 7,592 feed records, so
there is no join key in either direction for 2012-2018. Those ~4,100 title-less solicitations
are unreachable from here, and no amount of scraping changes that.

Rather than hardcode a cutoff year, this reads every agenda and extracts whatever 10-digit
numbers it finds. Pre-Ariba meetings simply yield nothing, so the data draws the boundary
instead of a guess baked into a constant.

TMMIS is Akamai-gated: plain HTTP gets 403 (verified), as does anything without a real
browser. So fetching needs the headed Chromium behind the `council` extra, exactly as
sources/council.py already does. Parsing is pure and testable against saved HTML.
"""
import pathlib
import re
import shutil
from contextlib import contextmanager

from lxml import etree
from lxml import html as _html
from lxml.html import HtmlComment

from toronto_bids import config
from toronto_bids.linking.call_number import normalize_call_number
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.linking.supplier import supplier_key
from toronto_bids.amount import parse_amount
from toronto_bids.sources.council import pdf_kind
from toronto_bids.models import BackgroundPdf, Bid, CompositeAward, CouncilItem
from toronto_bids.store import db

AGENDA_URL = "https://secure.toronto.ca/council/report.do"

# "BA189.1 - Award of Ariba Document Number 3234668279 to GHD Limited for the ..."
# BA = Bid Award Panel (2017-01-04 onward). BD = Bid Committee, its predecessor, which ran
# 2009-02-04 to 2016-12-21 — three weeks before BA's first meeting (#90). Same agenda
# structure throughout: same "Award of <id> to <supplier> for <subject>" heading, same
# "Contract Award Value ... net of all applicable taxes" block, same bid tables, same
# background-file PDFs. One series succeeded the other; nothing else changed.
_ITEM_HEADING = re.compile(r"^\s*(?P<ref>B[AD]\d+\.\d+)\s*-\s*(?P<title>.+?)\s*$", re.S)
_TEN_DIGIT = re.compile(r"\d{10}")
_WS = re.compile(r"\s+")
# Collapse spaces/tabs but keep newlines: item headings are found by line.
_WS_LINES = re.compile(r"[ \t]+")

# Item titles that are panel housekeeping, never an award.
_NOT_AN_AWARD = re.compile(r"^(election of|confirmation of minutes|declarations? of)", re.I)


def _clean(text):
    return _WS.sub(" ", text or "").strip()


def parse_agenda(html: str, meeting: str) -> list[dict]:
    """Every award item on one agenda page.

    Returns [{"reference", "meeting", "title", "document_numbers": [...]}, ...].
    Items with no 10-digit number are still returned (with an empty list) so a caller can
    see the pre-Ariba years for what they are rather than mistaking them for a fetch failure.
    """
    root = _html.fromstring(html)
    items = []
    for node in root.xpath("//h1|//h2|//h3|//h4|//h5|//h6"):
        m = _ITEM_HEADING.match(_clean(node.text_content()))
        if not m:
            continue
        title = _clean(m.group("title"))
        if _NOT_AN_AWARD.match(title):
            continue
        docs = []
        for hit in _TEN_DIGIT.findall(title):
            doc = normalize_document_number(hit)
            if doc and doc not in docs:
                docs.append(doc)
        items.append({
            "reference": f"{meeting}.{m.group('ref').split('.', 1)[1]}",
            "meeting": meeting,
            "title": title,
            "document_numbers": docs,
        })
    return items


# TMMIS answers 200 with an error page for a reference that is not real, and it has more than
# one way of saying so. Missing either one records an error page as an agenda.
_MISSING_MARKERS = (
    "this meeting is not available",       # e.g. 2018.BA10
    "the published report was not found",  # e.g. 2007.BD1
)


def agenda_is_missing(html: str) -> bool:
    """True when TMMIS served an error page rather than an agenda.

    Meeting numbering restarts each council term and the year prefix is a session year, so
    enumerating references means probing. A miss is normal, not an error.
    """
    text = (html or "").lower()
    return any(marker in text for marker in _MISSING_MARKERS)


@contextmanager
def agenda_fetcher(virtual_display: bool = False):
    """Yield `fetch(meeting) -> html`, backed by ONE headed Chromium for the whole run.

    Akamai 403s plain HTTP and headless (both verified), so a real browser is unavoidable.
    But sources/council.py launches a browser per page, which is fine for 3 suspended firms
    and ruinous here: enumerating ~474 meetings means launching Chromium ~474 times, and
    startup would dominate the run. One browser, many pages.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Bid Award Panel enrichment needs the optional 'council' extra. "
            "Install it with: uv sync --extra council && uv run playwright install chromium"
        ) from exc

    display = None
    if virtual_display:
        from pyvirtualdisplay import Display
        display = Display(visible=False, size=(1440, 900))
        display.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False, args=["--disable-blink-features=AutomationControlled"]
            )
            try:
                page = browser.new_context().new_page()

                def fetch(meeting: str) -> str:
                    page.goto(f"{AGENDA_URL}?meeting={meeting}&type=agenda",
                              wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(700)
                    return page.content()

                yield fetch
            finally:
                browser.close()
    finally:
        if display is not None:
            display.stop()


def agenda_date(html: str) -> str | None:
    """The meeting date the page reports, as YYYY-MM-DD.

    This is how a probe confirms it landed on the meeting it meant to: references cannot be
    derived reliably (see meeting_date_index), so we guess a reference and check the date.
    """
    m = re.search(r"Meeting Date:\s*</?[^>]*>?\s*\w+day,\s*(\w+)\s+(\d{1,2}),\s*(\d{4})",
                  _clean(_html.fromstring(html).text_content()) if "<" in html else html)
    if not m:
        m = re.search(r"\w+day,\s*(\w+)\s+(\d{1,2}),\s*(\d{4})",
                      _clean(_html.fromstring(html).text_content()))
    if not m:
        return None
    month = ("january february march april may june july august september october "
             "november december").split().index(m.group(1).lower()) + 1
    return f"{m.group(3)}-{month:02d}-{int(m.group(2)):02d}"


# The first meeting of each council term, as (session_year, term_label). A term's meetings
# are numbered 1..N contiguously; the reference's year prefix is the SESSION year, which
# rolls over in November when a term starts. Confirmed by fetching: 2017.BA1 = 2017-01-04,
# 2022.BA189 = 2022-05-25, 2023.BA4 = 2022-12-07.
# (series, first session year of the term, term label). A term numbers its meetings 1..N
# contiguously and the year prefix is the SESSION year, rolling over each November.
# Confirmed by fetching: 2017.BA1 = 2017-01-04, 2022.BA189 = 2022-05-25,
# 2023.BA4 = 2022-12-07, 2016.BD106 = 2016-10-19.
#
# BD shares the 2014-2018 term with BA: the Bid Committee sat until 2016-12-21 and the
# Bid Award Panel restarted numbering at 1 on 2017-01-04, mid-term. So both are walked
# for that term, and each stops where its own meetings run out.
# (series, first session year, term label, first meeting number).
#
# first_n is 1 everywhere except BD's 2006-2010 term: the Bid Committee was already sitting
# when that term began, so its meetings there are numbered from 105 (2009.BD105 = 2009-02-04,
# the earliest the City's schedule lists). Walking from 1 finds nothing and gives up, silently
# losing every 2009-2010 meeting — which is exactly what happened on the first run.
TERM_STARTS = [
    ("BD", 2009, "2006-2010", 105),
    ("BD", 2011, "2010-2014", 1),
    ("BD", 2015, "2014-2018", 1),
    ("BA", 2017, "2014-2018", 1),
    ("BA", 2019, "2018-2022", 1),
    ("BA", 2023, "2022-2026", 1),
]


def discover_meetings(fetch, log=lambda _m: None, max_per_term=260, stop_after_misses=4):
    """Walk each term's meetings, returning {reference: html} for every agenda that exists.

    References cannot be derived from the City's published schedule: it lists dates but omits
    MTG # for every term before 2022-2026, and inferring numbers from date order is wrong in
    both directions (measured against four references confirmed by fetching: it landed 188 vs
    189, 51 vs 50, 99 vs 100, 2 vs 1). The schedule both duplicates and omits meetings, and
    the drift is not constant, so there is no offset to correct for.

    So: probe, and let each page's own date confirm what it is. Walking n upward within a
    term is cheap because numbering is contiguous; only the session-year prefix has to be
    guessed, and it only ever advances.
    """
    found = {}
    for series, start_year, term, first_n in TERM_STARTS:
        session = start_year
        misses = 0
        for n in range(first_n, first_n + max_per_term):
            html = ref = None
            # The prefix only ever advances, and only at a November boundary.
            for candidate in (session, session + 1):
                probe = f"{candidate}.{series}{n}"
                page = fetch(probe)
                if not agenda_is_missing(page):
                    html, ref, session = page, probe, candidate
                    break
            if html is None:
                misses += 1
                log(f"  {series} {term}: no meeting {n} (miss {misses}/{stop_after_misses})")
                if misses >= stop_after_misses:
                    log(f"  {series} {term}: stopping after {n - misses} meetings")
                    break
                continue
            misses = 0
            found[ref] = html
            log(f"  {ref:<12} {agenda_date(html)}  ({len(found)} so far)")
    return found


# Deliberately NOT trimming "Award of Ariba Document Number 3234668279 to GHD Limited for
# the ..." down to a bare subject. The obvious rules both break on real data: taking the
# last " for " mangles subjects that contain one ("... for Engineering and Construction
# Services" -> "Engineering and Construction Services"), and taking the first breaks on
# suppliers that contain one ("Centre for Excellence Inc."). There is no reliable rule, the
# full heading is already readable, and it names the supplier too. Store the City's words
# verbatim and let source='bid_award_panel' say where they came from.


def store_items(conn, agendas: dict) -> int:
    """Upsert every award item from {reference: html} into council_item. Idempotent.

    This is what finally populates council_item (#68), which has been in the schema and the
    export since the rewrite with nothing ever written to it.
    """
    n = 0
    for meeting, html in agendas.items():
        for item in parse_agenda(html, meeting):
            db.upsert_row(conn, CouncilItem(reference=item["reference"],
                                            title=item["title"]), overwrite=True)
            n += 1
    conn.commit()
    return n


def fill_titles_from_council(conn) -> int:
    """Give title-less solicitations the title of the council item that awarded them.

    Only fills NULLs — the City's own posting title always wins where it published one.
    Thanks to #70 a placeholder is spelled NULL, so this can land at all; before that,
    'Doc-3524228095' was non-NULL and no backfill could ever replace it.

    Returns the number of solicitations named. Idempotent.
    """
    missing = {r["document_number"] for r in
               conn.execute("SELECT document_number FROM solicitation WHERE title IS NULL")}
    if not missing:
        return 0
    filled = {}
    for row in conn.execute("SELECT reference, title FROM council_item WHERE title IS NOT NULL"):
        for hit in _TEN_DIGIT.findall(row["title"]):
            doc = normalize_document_number(hit)
            # First council item wins: agendas are walked oldest-first, so the original
            # award beats any later amendment naming the same document.
            if doc in missing and doc not in filled:
                filled[doc] = row["title"]
    conn.executemany(
        "UPDATE solicitation SET title = ?, title_source = 'bid_award_panel' "
        "WHERE document_number = ? AND title IS NULL",
        [(t, d) for d, t in filled.items()])
    conn.commit()
    return len(filled)


def cached_agendas(agenda_dir) -> dict:
    """{reference: html} for every agenda already on disk. Offline."""
    root = pathlib.Path(agenda_dir)
    if not root.is_dir():
        return {}
    return {p.stem: p.read_text(errors="replace") for p in sorted(root.glob("*.html"))}


def scrape_agendas(agenda_dir, virtual_display: bool = False, log=lambda _m: None) -> dict:
    """Discover and cache every agenda, returning {reference: html}.

    Resumable and safe to re-run: an agenda already on disk is never refetched, so a second
    run costs only the probes past the last meeting. Only misses and new meetings hit the
    network.
    """
    root = pathlib.Path(agenda_dir)
    root.mkdir(parents=True, exist_ok=True)

    with agenda_fetcher(virtual_display=virtual_display) as fetch_live:
        def fetch(meeting: str) -> str:
            cached = root / f"{meeting}.html"
            if cached.exists():
                return cached.read_text(errors="replace")
            html = fetch_live(meeting)
            if not agenda_is_missing(html):
                # Store <main> only: the rest is nav, sharing widgets and a language picker.
                match = re.search(r"(<main.*</main>)", html, re.S)
                cached.write_text(match.group(1) if match else html)
            return html

        return discover_meetings(fetch, log=log)


def parse_agenda_pdfs(html: str, meeting: str) -> list[dict]:
    """The staff-report PDFs an agenda links, attributed to the item each sits under.

    Rewrite spec §2.3 lists background-file PDFs as having "**No index** — source
    (year, committee, id) tuples from TMMIS". The agendas *are* that index: every award item
    links its report, and 474 of the 475 cached agendas carry at least one. 3,142 distinct
    PDFs across the corpus.

    Attribution works because the City emits them in document order — item heading, then that
    item's Background Information links, then the next heading — so the most recent heading
    owns the links that follow it. Links appearing before any item heading (agenda-level
    attachments) are attributed to the meeting rather than dropped.
    """
    root = _html.fromstring(html)
    out, seen = [], set()
    reference = meeting
    for el in root.iter():
        if isinstance(el, HtmlComment) or not isinstance(el.tag, str):
            continue
        if el.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            m = _ITEM_HEADING.match(_clean(el.text_content()))
            if m:
                reference = f"{meeting}.{m.group('ref').split('.', 1)[1]}"
            continue
        if el.tag != "a":
            continue
        url = el.get("href") or ""
        if "/legdocs/mmis/" not in url or not url.lower().endswith(".pdf") or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "reference": reference, "kind": pdf_kind(url)})
    return out


def store_background_pdfs(conn, agendas: dict) -> int:
    """Index every staff-report PDF the agendas link. Idempotent. Downloads nothing.

    The URL index is the deliverable: it is what spec §2.3 says does not exist, and it turns
    "fetch the report for award X" from an unanswerable question into a lookup. Fetching the
    bytes is a separate, much heavier pass — these are plain HTTP (verified: 200,
    application/pdf), so it needs no browser, unlike the agendas themselves.
    """
    n = 0
    for meeting, html in agendas.items():
        for pdf in parse_agenda_pdfs(html, meeting):
            db.upsert_row(conn, BackgroundPdf(url=pdf["url"], reference=pdf["reference"],
                                              kind=pdf["kind"]), overwrite=False)
            n += 1
    conn.commit()
    return n


# Bid tables name their first column six ways across the corpus.
# The plural is not decoration: BA189.3 heads its table "Suppliers", and without the `s?` the
# whole table is declined and its five bids are silently dropped. Found by #94, on a BA
# agenda, because the Bid Committee parser accepted a header the Bid Award Panel one refused.
_BIDDER_HDR = re.compile(
    r"^\s*(supplier|bidder|proponent|firm|vendor|company|respondent)s?"
    r"[\s/]*(name|names|or proponent name)?\s*$", re.I)
_PRICE_HDR = re.compile(r"bid price|bid amount|price|quotation", re.I)
# "including H.S.T." vs "excluding H.S.T." is a real difference — 1,307 tables say one and
# 1,048 the other. A bare price column would silently mix the two bases.
# "incl\w*\.?" not "includ\w*": the Bid Committee overwhelmingly abbreviates, and
# "Bid Price (Incl. HST)" is its single most common price header (587 of them). Requiring the
# full word reads those as basis-unknown, which is the one thing a bid price must not be —
# 5,801 bids include HST and 4,097 exclude it, so an unmarked price is two incomparable
# things in one column (#84).
_HST_INCLUDING = re.compile(r"\bincl\w*\.?\s*(all applicable taxes|h\.?s\.?t)", re.I)
_HST_EXCLUDING = re.compile(r"\bexcl\w*\.?\s*(all applicable taxes|h\.?s\.?t)", re.I)
# Footnote markers ride on both names and prices: '$2,982,036.67*', 'Smith and Long Ltd.**'.
# They point at a note under the table ('*includes contingency', '**found non-compliant'), so
# the raw string keeps them and only the parse strips them.
_FOOTNOTE = re.compile(r"[\s*^+†‡§]+$")
# Some tables enumerate their rows, two different ways, and both corrupt the bidder name:
#   inline    ['1. Pave Tar Construction Ltd', '$937,419']        -> 639 rows
#   own column ['1', 'Joe Pace & Sons Inc.', '$1,219,281']        ->  19 rows, and the header
#              declares only 2 columns, so every cell shifts and the NAME lands in bid_price.
# Pure presentation — unlike a footnote marker it points at nothing — so it is stripped.
_ROW_NUMBER_CELL = re.compile(r"^\s*\d+\.?\s*$")
_ROW_NUMBER_PREFIX = re.compile(r"^\s*\d+[.)]\s+")
# Footnote markers ride on bidder names too, and on either side: '**AQUA TECH SOLUTIONS INC',
# 'Smith and Long Ltd.**'. Unlike bid_price — where the marker sits beside a value we parse,
# so keeping it preserves the pairing — a name is an identifier that has to match across
# sources. The marker is not part of it, and left on it wins display_name's alphabetical
# sort ('**AQUA TECH...' before 'AQUA TECH...') and uglifies the dimension.
_NAME_MARKERS = re.compile(r"^[\s*^+†‡§]+|[\s*^+†‡§]+$")


def _hst_basis(header: str) -> str | None:
    if _HST_INCLUDING.search(header):
        return "including"
    if _HST_EXCLUDING.search(header):
        return "excluding"
    return None


# --- Bid Committee (BD) bid tables (#94) -------------------------------------------------
#
# BA lays a bid table out one row per bidder. BD does not, and 417 BD agendas yielded 36 bids
# because of it. Its columns are single cells holding a <p> per value, so the whole table is:
#
#     cell[0]  "Number of Bids:"                                    (a rowspan)
#     cell[1]  "Firm Name"             / R.E. Cavanagh Electric / Ozz Electric Inc. / ...
#     cell[2]  "Bid Price (Incl. HST)" / $ 224,156.52 / $ 231,817.47 / ...
#
# Two things follow. The heading is a LINE inside a cell, not the table's header row, so
# _BIDDER_HDR.match(header[0]) never fires. And lxml's text_content() concatenates those <p>
# runs with no separator, so the cell reads as one blob: "$ 224,156.52$ 231,817.47$ ...".
# Reading the markup back as lines is what recovers the column.
#
# The same agendas also put the bidders in the FOLLOWING rows instead, offset by the rowspan
# cell, and mix the two freely. Both reduce to the same rule: zip the bidder column's lines
# against the price column's lines.
_CELL_BLOCK_END = re.compile(r"</(p|div|li|tr|td)>", re.I)
_CELL_BR = re.compile(r"<br\s*/?>", re.I)


def _cell_lines(cell) -> list[str]:
    """A table cell's values, one per line of block markup.

    text_content() would fuse them: a BD price column comes back as
    "$ 224,156.52$ 231,817.47$ 240,633.50" with nothing to split on.
    """
    markup = _CELL_BR.sub("\n", etree.tostring(cell, encoding="unicode"))
    text = _html.fromstring(_CELL_BLOCK_END.sub("\n", markup)).text_content()
    return [line.strip() for line in text.replace("\xa0", " ").split("\n") if line.strip()]


# A BD heading occupies its cell alone ("Firm Name"), unlike the appendix label
# "Recommended Bidder:" which carries its value beside it.
_BD_BIDDER_HDR = re.compile(
    r"^(bidder|firm|proponent|company|contractor|supplier|vendor)s?[\s/]*(name|names)?\s*:?$",
    re.I)
# What can stand in a price column: an amount, or the outcome the City writes instead of one.
_BD_PRICEY = re.compile(r"\$|\d[\d,]*\.\d{2}|non.?compliant|no bid|withdrawn|informal", re.I)


def _parse_bd_bid_table(table, reference: str, docs: list) -> list[dict]:
    """Bids from one Bid Committee table, whichever of its two layouts it uses.

    Returns [] for anything it cannot read cleanly. Unequal columns are REFUSED rather than
    paired: names and prices are positional, so one stray line (a footnote, a wrapped name)
    silently attributes a bid to the wrong firm, and a misattributed bid is worse than a
    missing one.
    """
    # Direct children only. These agendas nest a bid table inside the item's outer table, and
    # the element walk visits both: with .//tr the outer table descends into the inner one and
    # re-parses rows the row-major path already read, duplicating five of BA189.3's bids.
    # Scoped this way, every row belongs to exactly one table.
    rows = table.xpath("./tr|./tbody/tr|./thead/tr")
    out = []
    for index, row in enumerate(rows):
        columns = [_cell_lines(c) for c in row.xpath("./td|./th")]
        bidder_col = next((i for i, c in enumerate(columns)
                           if c and _BD_BIDDER_HDR.match(c[0])), None)
        if bidder_col is None:
            continue
        price_col = next((i for i, c in enumerate(columns)
                          if c and i != bidder_col and _PRICE_HDR.search(c[0])), None)
        if price_col is None:
            continue
        price_header = columns[price_col][0]

        pairs = []
        if len(columns[bidder_col]) > 1:          # the column's values share its header cell
            pairs.append((columns[bidder_col][1:], columns[price_col][1:]))
        for later in rows[index + 1:]:            # ...or sit in the rows below it
            cells = [_cell_lines(c) for c in later.xpath("./td|./th")]
            if not cells:
                break
            # The header's rowspan cell ("Number of Bids:") is absent from the rows beneath,
            # shifting every one of their cells left by exactly that much.
            offset = len(columns) - len(cells)
            if offset < 0 or bidder_col - offset < 0 or price_col - offset < 0:
                break
            if price_col - offset >= len(cells):
                break
            names, prices = cells[bidder_col - offset], cells[price_col - offset]
            if not names or not prices or not any(_BD_PRICEY.search(p) for p in prices):
                break                              # left the bid table
            pairs.append((names, prices))

        for names, prices in pairs:
            if len(names) != len(prices):
                continue                           # cannot pair positionally; refuse
            for name, price in zip(names, prices):
                name = _NAME_MARKERS.sub("", _ROW_NUMBER_PREFIX.sub(
                    "", _NAME_MARKERS.sub("", name)))
                if not name or not _BD_PRICEY.search(price):
                    continue
                out.append({
                    "reference": reference,
                    "document_number": docs[0] if docs else None,
                    "bidder_name_raw": name,
                    "bid_price": price or None,
                    "hst_basis": _hst_basis(price_header),
                    "price_header": price_header,
                })
    return out


def parse_bid_tables(html: str, meeting: str) -> list[dict]:
    """Every bid on an agenda: who bid, what they bid, and on which basis.

    Rewrite spec §2.5.2 calls this data "never published anywhere. **Unrecoverable.**" It is
    published on every Bid Award Panel agenda, in real <table> markup (#84).

    Returns dicts of reference / document_number / bidder_name_raw / bid_price /
    hst_basis / price_header. Tables are selected on their first column matching a bidder
    heading, because an item also carries Financial Impact and WBS cost-centre tables that
    look nothing like a bid.
    """
    root = _html.fromstring(html)
    out = []
    reference, docs = meeting, []
    for el in root.iter():
        if isinstance(el, HtmlComment) or not isinstance(el.tag, str):
            continue
        if el.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            m = _ITEM_HEADING.match(_clean(el.text_content()))
            if m:
                reference = f"{meeting}.{m.group('ref').split('.', 1)[1]}"
                docs = [d for hit in _TEN_DIGIT.findall(m.group("title"))
                        if (d := normalize_document_number(hit))]
            continue
        if el.tag != "table":
            continue
        rows = el.xpath(".//tr")
        if not rows:
            continue
        header = [_clean(c.text_content()) for c in rows[0].xpath(".//td|.//th")]
        if not (len(rows) >= 2 and header and _BIDDER_HDR.match(header[0])):
            # Not BA's shape. Try the Bid Committee's, which puts its heading in a cell rather
            # than a header row (#94). Additive by construction: only reached once the
            # row-major path has declined, so BA's 12,733 bids are untouched.
            #
            # The `len(rows) >= 2` belongs to the row-major path alone: it wants a header row
            # plus data rows, whereas a BD table is routinely a SINGLE row whose cells each
            # hold a whole column. Testing it before the fallback skipped those outright.
            out.extend(_parse_bd_bid_table(el, reference, docs))
            continue
        price_col = next((i for i, h in enumerate(header) if i and _PRICE_HDR.search(h)), None)
        price_header = header[price_col] if price_col is not None else None
        for row in rows[1:]:
            cells = [_clean(c.text_content()) for c in row.xpath(".//td|.//th")]
            # An undeclared leading row-number column shifts every cell left, dropping the
            # bidder name into the price. Realign against the header before reading either.
            while (len(cells) > len(header) and cells
                   and _ROW_NUMBER_CELL.match(cells[0] or "")):
                cells = cells[1:]
            if not cells or not cells[0]:
                continue
            name = _NAME_MARKERS.sub("", _ROW_NUMBER_PREFIX.sub(
                "", _NAME_MARKERS.sub("", cells[0])))
            if not name:
                continue
            price = cells[price_col] if (price_col is not None
                                         and len(cells) > price_col) else None
            out.append({
                "reference": reference,
                # Pre-2019 items name no document number (Toronto adopted Ariba ~2019), so a
                # bid can be real and unattributable. Kept anyway — #77 wants exactly these.
                "document_number": docs[0] if docs else None,
                "bidder_name_raw": name,
                "bid_price": price or None,
                "hst_basis": _hst_basis(price_header) if price_header else None,
                "price_header": price_header,
            })
    return out


def store_bids(conn, agendas: dict) -> int:
    """Extract and store every bid the agendas tabulate. Idempotent. Returns rows stored.

    This is the data rewrite spec §2.5.2 calls unrecoverable (#84).
    """
    n = 0
    for meeting, html in agendas.items():
        for bid in parse_bid_tables(html, meeting):
            db.upsert_row(conn, Bid(source="bid_award_panel", **bid), overwrite=True)
            n += 1
    conn.commit()
    return n


# "Award of Tender Call No. 14-2017 to Ontario Excavac Inc. for Replacement of ..."
_WINNER = re.compile(r"\bto\s+(.+?)\s+for\s+", re.I)
# Council publishes THREE figures per award. Calibrated against 980 Ariba-era items where the
# document number gives ground truth: award_amount is the "net of all applicable taxes" one
# (820/980 = 84%). "including HST" matched 0; "net of HST recoveries" matched 4.
_NET_OF_TAXES = re.compile(r"\$([\d,]+(?:\.\d+)?)\s*net of all applicable taxes", re.I)
_ITEM_SPLIT = re.compile(r"B[AD]\d+\.\d+ - ")


def parse_pre_ariba_awards(html: str) -> list[dict]:
    """Items that name no document number, with the winner and value needed to match them.

    Toronto adopted Ariba around 2019, so a 2017-2018 agenda identifies its award by Call
    Number ("Award of Call Number 6032-16-3114 to MeteoGroup...") and our spine is keyed on a
    10-digit Ariba number backfilled later. There is no identifier in common (#77), and
    `Contract_Number_Purchase_Order` is empty on all 7,592 feed records.

    But the item names its winner and its value, and `award` holds both. That is the join.
    """
    text = _WS_LINES.sub(" ", _html.fromstring(html).text_content())
    out = []
    for chunk in _ITEM_SPLIT.split(text)[1:]:
        head = chunk[:400]
        if _TEN_DIGIT.search(head):
            continue                      # names a doc number — joins directly, not our case
        winner, value = _WINNER.search(head), _NET_OF_TAXES.search(chunk)
        if not (winner and value):
            continue
        amount = parse_amount(value.group(1))
        if amount is None:
            continue
        out.append({"title": _clean(head.split("\n")[0]),
                    "winner_raw": _clean(winner.group(1)),
                    "award_value": amount})
    return out


# Legal-form noise that varies freely between how council writes a supplier and how the feed
# does: 'Sanscon Construction Limited' vs 'Sanscon Construction Ltd.', 'Liftsafe Engineering &
# Service Group' vs '... and Service Group', 'The Municipal Infrastructure Group,'.
_LEGAL_NOISE = re.compile(r"\b(limited|ltd|incorporated|inc|corporation|corp|company|co|"
                          r"lp|llp|ulc|holdings|group|canada|ontario)\b", re.I)
_LEADING_THE = re.compile(r"^the\s+", re.I)


def supplier_tokens(name: str | None) -> set:
    """Significant words in a supplier name, with legal form and '&'/'and' variance removed.

    Deliberately looser than linking/supplier.py's supplier_key, and that is safe *here* for a
    reason that does not apply there: supplier_key must not MERGE two firms into one dimension
    row, so it keeps legal suffixes on purpose. This only has to CONFIRM a match the exact
    award value already pinned — and the value is nearly a unique key (4,725 of 4,861 title-less
    amounts occur exactly once).
    """
    text = _LEADING_THE.sub("", (name or "").lower().replace("&", " and "))
    return {t for t in supplier_key(_LEGAL_NOISE.sub(" ", text)).split() if len(t) > 2}


def match_pre_ariba_titles(conn, agendas: dict) -> int:
    """Name title-less pre-Ariba solicitations by matching (supplier, award value). Idempotent.

    The award value carries the match; the supplier only confirms it. Measured against 777
    Ariba-era items, where the document number gives ground truth — matching them on
    (supplier, value) while ignoring that number, then checking the answer:

        exact supplier_key   488 matched, 0 wrong, recall 62.8%
        one shared token     759 matched, 0 wrong, recall 97.7%   <- this
        no supplier check    768 matched, 0 wrong, recall 98.8%

    Zero false positives at every level, so the supplier check buys no precision on that
    sample — but the sample is biased: every item in it IS an award we hold. A pre-Ariba item
    whose award we do NOT hold could coincidentally match an unrelated value, and this is the
    only guard against it. 1.1% recall is a cheap premium.

    Only a UNIQUE match is taken. A wrong title is worse than none.
    """
    items = []
    for meeting, html in agendas.items():
        if meeting.split(".")[0] >= "2019":
            continue                      # 2019+ names a document number; no need to guess
        items.extend(parse_pre_ariba_awards(html))
    return match_on_supplier_and_value(conn, items, "council_pre_ariba")


# --- composite reports (#93) ------------------------------------------------------------
#
# 2009-2012 agendas do not describe their awards. One item carries many:
#
#     BD100.1 - Contract Awards - November 21 - Composite Report
#
# and the agenda body only says the details are "set out in the appendices of this report".
# It names no amount, so #77's (supplier, value) join has nothing to stand on, and the feed
# offers no identifier to fall back to: probing Posting_Title and
# Solicitation_Document_Description for all 3,628 title-less rows returned an identifier on
# zero of them. Matching on (supplier, award date) instead was measured against 880
# ground-truth items and rejected — at a 30-day window it is 21% wrong.
#
# The appendices of the linked staff-report PDF do carry all of it, in the same shape #77
# already reads:
#
#     Call No:            Request for Quotation 3917-12-7226
#     Description:        For the non exclusive provision of ... Concrete Cutting Services
#     Recommended Bidder: Accrue Contracting Ltd.
#     Contract Award Value:
#         $420,000.00 net of all applicable taxes and charges     <- the figure #77 calibrated
#         $474,600.00 including all applicable taxes and charges
#         $427,392.00 net of HST recoveries
#
# So this parses the appendices and hands them to the same join. Those PDFs are plain HTTP —
# only TMMIS itself is Akamai-gated — and they are already indexed in background_pdf by
# store_background_pdfs, so the download is bounded and needs no browser.
# Lookahead so the block keeps its own "Call No:" label — a plain split would eat the
# delimiter and the call number with it.
_APPENDIX_BLOCK = re.compile(r"(?=Call No:)", re.I)


# An appendix is a run of "Label:" fields, and the value may sit on the label's line or the
# lines below it. A regex lookahead for the next label cannot read this safely: it has to
# enumerate what a label looks like, and the real ones defeat any tidy guess —
# "Contract Award Value*:" (84), "Contract Award Values*:" (19) and
# "Recommended Bidder/Proponent:" (18) all carry punctuation, so a [A-Za-z ]+ lookahead runs
# straight past them and swallows the whole value block into the supplier name. That misread
# 201 of 1,076 bidder fields. Walking lines instead makes the terminator explicit.
# The length bound is load-bearing and was too tight at 34: "Total Potential Contract Award
# Value:" is 36 characters, so it was not recognized as a label at all and the supplier name
# ran straight through it into the amount ("Pitney Bowes of Canada Ltd. Total Potential
# Contract Award Value: $3,676,730.40").
_LABEL_LINE = re.compile(r"^[ \t]*([A-Z][A-Za-z ./&'-]{2,44})\*?:[ \t]*(.*)$")
# An appendix can span a page break, which drops the running header, the page number and the
# next "APPENDIX #n" banner into the middle of a field. One such header was captured verbatim
# as a supplier name ("Contract Awards – Bid Committee Composite Report – January 26, 2011").
_APX_FURNITURE = re.compile(
    r"^\s*(?:\d{1,3}|Contract Awards.*Composite Report.*|APPENDIX\s*#?\s*\d*)\s*$", re.I)
# Council footnotes the corrected tender prices ("*Tender price corrected for mathematical
# errors..."), which belongs to no field.
_APX_FOOTNOTE = re.compile(r"^\s*[*^+†‡]")
# The same section headings sometimes arrive with no colon at all, which is invisible to
# _LABEL_LINE and so does not end the field before it. Left unhandled the supplier swallows
# the value block behind it — 24 names ran past their firm into "... Contract Award Value
# Date of award to December 31, 2011", one to 735 characters.
_APX_SECTION_NO_COLON = re.compile(
    r"^\s*(?:Contract Award Values?|Financial Impact|Number of (?:Bids|Proposals)|"
    r"Range of Scores|Division Contacts?|Call Dates?)\b", re.I)

_BIDDER_LABELS = ("recommended bidder", "recommended bidders", "recommended proponent",
                  "recommended proponents", "recommended bidder/proponent")


def _appendix_fields(block: str) -> dict:
    """The block's "Label: value" fields, lowercased, with page furniture and footnotes dropped.

    A blank line does not end a field — council wraps long descriptions across them — but a
    new label does, and so does any furniture line, which is what a page break inserts.
    """
    out, current = {}, None
    for line in block.split("\n"):
        if _APX_FURNITURE.match(line) or _APX_FOOTNOTE.match(line):
            current = None
            continue
        label = _LABEL_LINE.match(line)
        if label:
            current = label.group(1).strip().lower()
            out.setdefault(current, [])
            if label.group(2).strip():
                out[current].append(label.group(2).strip())
        elif _APX_SECTION_NO_COLON.match(line):
            current = None                # a heading that lost its colon still ends the field
        elif current is not None and line.strip():
            out[current].append(line.strip())
    return {k: _clean(" ".join(v)) for k, v in out.items() if v}


def _appendix_bidder(fields: dict) -> str | None:
    """The recommended supplier, whatever this appendix calls the field.

    RFPs say "Proponent" where tenders say "Bidder", both pluralize when an award is split,
    and one form hedges with "Bidder/Proponent". Accepting only the singular drops 41.
    """
    for label in _BIDDER_LABELS:
        if fields.get(label):
            return fields[label]
    return None


# "1. CDR Youngs Aggregates Inc. 2. Lafarge Aggregates 3. Vicdom Sand & Gravel (Ontario)
# Limited" — one call, several winners, each with its own value section further down. Split
# awards are real and must not be silently recorded as one firm with a fused name.
_ENUMERATED_BIDDER = re.compile(r"(?:^|\s)\d{1,2}[.)]\s+")
# A split award is also written by naming the segments each firm won:
#     Area "A" – A&F DiCarlo Construction Inc. Area "B" – Pave-Tar Construction Ltd.
#     Part "A" and Part "C" – SCI Interiors Part "B" – POI Business Interiors
#     Project 1 - GENIVAR Inc. Project 2 – Stantec Consulting Ltd.
_SEGMENTED_BIDDER = re.compile(r"\b(?:Area|Part|Project|Group|Schedule)\s*[\"'“]?[A-D0-9]\b", re.I)
# Rosters name their winners without any marker at all ("Ability Learning Network Inc. Abrigo
# Centre ACCESS Employment Adanac Truck Driver Training Ltd. ..."), leaving length as the only
# signal. Counting legal-form tokens looks like a better idea and is not: it flags 81 rows,
# nearly all single firms that simply carry two ("Canadian Tire Corporation, Limited",
# "A.J. Stone Co. Ltd.", "Furfari Paving Co. Ltd.").
#
# The threshold stays loose on purpose. Real single firms run to 71 characters via aliases
# ("St. Marys Cement Inc. (Canada) d.b.a. Canada Building Materials Company", "Corporate
# Express, Canada Inc. operating as Staples Advantage Canada"), so tightening it to catch the
# last few unmarked pairs would drop an equal number of genuine awards — a worse trade. Three
# unmarked pairs survive as single rows; #98 is where they get separated properly.
_MAX_SUPPLIER_NAME = 80

# How council words the net figure drifts with tax history, and the drift is not cosmetic:
# Ontario had no HST until July 2010, so 2009 publishes only "$1,020,600.00 (Net of GST)"
# and _NET_OF_TAXES matches none of it. Measured yield per year, narrow -> this:
#
#     2009    0 -> 243      (all of it; "net of GST")
#     2010  230 -> 253      (the GST/HST transition year, mixed wording)
#     2011  256 -> 303      ("net of all taxes and charges")
#     2012  257 -> 277      ("net of all applicable taxes")
#
# Still refuses "net of HST recoveries" — a third figure #77 measured as the wrong one
# (4/980 against ground truth). Deliberately separate from _NET_OF_TAXES, which #77
# calibrated on Ariba-era agendas: widening that would reopen a settled measurement.
_APX_AWARD_VALUE = re.compile(
    r"\$([\d,]+(?:\.\d+)?)\s*\(?\s*net of (?:all )?(?:applicable )?(?:taxes|gst)\b", re.I)


# --- split awards: one call, several winners (#98) ---------------------------------------
#
# The value section is the authority on HOW MUCH and the bidder field on WHO won, and the two
# are keyed to each other. Council uses several schemes for that key, all in one corpus:
#
#     Recommended Bidders:  1. CDR Youngs Aggregates Inc.  2. Lafarge Aggregates
#     Contract Award Value: 1. CDR Youngs Aggregates Inc. ... $2,589,782.50 net of ...
#
#     Recommended Bidder:   Firm A) WM Weller Tree Service Ltd. Firm B) Ontario Line Clearing
#     Contract Award Value: Firm A) $4,872,017.44 net of GST
#                           Firm B) $2,787,919.68 net of GST
#
# so the winner's value is the FIRST net-of-taxes figure inside that winner's own run of the
# section — which also steps past the option-year and "total potential" figures that follow
# it, exactly as the single-winner case does.
_VALUE_SECTION = re.compile(
    r"Contract Award Values?\*?:?\s*\n(.*?)(?=\n\s*(?:Number of (?:Bids|Proposals)|"
    r"Financial Impact|Division Contacts?|Range of Scores|Call No|Ward No|Call Dates)\b|\Z)",
    re.S | re.I)
_VALUE_ENTRY = re.compile(
    r"^[ \t]*(?:Firm\s+\(?(?P<firm>[A-H])\)|(?P<num>\d{1,2})\s*[.)]|(?P<let>[a-h])\s*\)|"
    r"(?P<named>[A-Z][^:$\n]{3,44}):(?=\s*\$))[ \t]*(?P<rest>.*)$", re.M)
# The value section labels its periods the same way it labels its firms, so "Option January 1,
# 2010 to December 31, 2010: $79,800.00" reads as a winner unless this refuses it. Inventing a
# supplier named after a date range is the exact failure this whole pass exists to avoid.
_NOT_A_FIRM = re.compile(r"^(option|date of|from\b|to\b|total|for the period|item|part|year|"
                         r"january|february|march|april|may|june|july|august|september|"
                         r"october|november|december)\b|\d{4}", re.I)
# "Firm A)" and "Firm (A)" are the same scheme; both appear.
_BIDDER_KEYED = re.compile(
    r"(?:Firm\s+\(?([A-H])\)|(?:^|\s)(\d{1,2})\s*[.)]\s|(?:^|\s)([a-h])\s*\)\s)")
# What a winner won, appended to its name: "WM Weller Tree Service Ltd. – Type I and II
# Service", "Budget Car & Truck - Award Price Schedule A, B and C". Left on, the same firm
# keys differently here than everywhere else and forks the supplier dimension. Only a SPACED
# dash starts one, so hyphenated firms (Levitt-Safety, Trade-Mark Industrial) are untouched.
_WINNER_QUALIFIER = re.compile(
    r"\s+[-–—]\s*(?:Award|Type|Price|Part|Item|for\b|Schedule|Group|Area).*$", re.I)
# The same thing in parentheses: "Lima's Gardens & Construction Inc. (Northwest, Northeast and
# Southwest Quadrant)", "Flow-Kleen Technology Ltd (for services in Toronto East York ...)".
# Anchored to the end and gated on what a qualifier opens with, so a parenthetical that is
# part of the name survives — "Vicdom Sand & Gravel (Ontario) Limited" is one firm.
_WINNER_PAREN_QUALIFIER = re.compile(
    # north\w* etc: council writes "Northwest"/"Northeast", where a bare `north\b` never matches.
    r"\s*\((?:for|price\s+schedule|award|north\w*|south\w*|east\w*|west\w*|district|region|"
    r"quadrant|part|area|item|schedule|group)\b[^)]*\)\.?\s*$", re.I)


def _bidder_map(bidder: str) -> dict:
    """{key: name} from the bidder field, for the schemes that key their winners."""
    parts, out = list(_BIDDER_KEYED.finditer(bidder)), {}
    for i, match in enumerate(parts):
        key = (match.group(1) or match.group(2) or match.group(3) or "").lower()
        end = parts[i + 1].start() if i + 1 < len(parts) else len(bidder)
        name = _clean(bidder[match.end():end])
        if key and name:
            out[key] = name
    return out


def split_award_winners(block: str, bidder: str) -> list[dict] | None:
    """Each winner of a split award with its own value, or None if the block does not say.

    Returns None rather than guessing: 21 of the 51 split appendices key their value section
    in a way this does not read (per-Item tables, per-district prose), and a wrong supplier is
    worse than a skipped one.
    """
    section = _VALUE_SECTION.search(block)
    if not section:
        return None
    text = section.group(1)
    entries = [m for m in _VALUE_ENTRY.finditer(text)
               if not (m.group("named") and _NOT_A_FIRM.match(m.group("named").strip()))]
    if len(entries) < 2:
        return None
    keyed, want = _bidder_map(bidder), supplier_tokens(bidder)
    out = []
    for i, match in enumerate(entries):
        end = entries[i + 1].start() if i + 1 < len(entries) else len(text)
        value = _APX_AWARD_VALUE.search(text[match.start():end])
        if not value:
            continue
        amount = parse_amount(value.group(1))
        if amount is None:
            continue
        rest = (match.group("rest") or "").lstrip()
        name = match.group("named") or (rest if rest and not rest.startswith("$") else None)
        if not name:
            # The value section only labels this winner; the bidder field is where it is named.
            name = keyed.get((match.group("firm") or match.group("num")
                              or match.group("let") or "").lower())
        if not name:
            continue
        name = _clean(_WINNER_QUALIFIER.sub("", _clean(name)))
        # repeat: a name can carry both forms ("X (Northwest Quadrant) (Southwest Quadrant)")
        while True:
            stripped = _clean(_WINNER_PAREN_QUALIFIER.sub("", name))
            if stripped == name or not stripped:
                break
            name = stripped
        # The bidder field is the authority on who won. A "winner" it never names is a parsing
        # artefact, not a firm — this is what stops a date range becoming a supplier.
        if not name or not (supplier_tokens(name) & want):
            continue
        out.append({"winner_raw": name, "award_value": amount})
    return out or None


def parse_composite_appendices(text: str) -> list[dict]:
    """Awards from a composite staff report's appendices.

    Pure: `text` is the pdftotext output already stored in background_pdf.text. Identical
    yield with and without pdftotext's -layout (244/280 blocks either way on the 2012 sample),
    so this reads whatever council.py:download_pdf produced without changing that path.

    `award_value` is the FIRST net-of-taxes figure in the block — the initial term, excluding
    option years. That is measured, not assumed: on the 139 appendices whose award the City's
    feed also published, the first figure equals the feed's award_amount 137 times (98.6%).
    The option-year and "total potential" figures below it can be twice as large.

    One item per award LINE. A call awarded to several winners yields one item each, taken
    from the value section's own per-winner runs (#98); a call this cannot split confidently
    yields none and is reported by `split_award`, so the caller can count what it dropped.
    """
    out = []
    for block in _APPENDIX_BLOCK.split(text)[1:]:
        fields = _appendix_fields(block)
        description, bidder = fields.get("description"), _appendix_bidder(fields)
        value = _APX_AWARD_VALUE.search(block)
        if not (description and bidder and value):
            continue                      # blocks publishing no winner or no value at all
        call_raw = fields.get("call no") or fields.get("call no.")
        common = {
            "call_number_raw": call_raw,
            "call_number": normalize_call_number(call_raw),
            # The call number is the only identifier the appendix carries, and it is what a
            # human would search for, so it leads the title rather than being dropped.
            "title": f"{call_raw} - {description}" if call_raw else description,
            "description": description,
            "split_award": False,
        }

        winners = split_award_winners(block, bidder)
        if winners:
            for winner in winners:
                out.append({**common, **winner, "award_value_raw": f"${winner['award_value']:,.2f}"})
            continue

        amount = parse_amount(value.group(1))
        if amount is None:
            continue
        # Not splittable: council named several winners in a way the value section does not
        # key, so there is nothing to apportion the money by and the row is refused.
        #
        # A parenthetical is NOT evidence of a single firm, tempting as it looks. Some long
        # names are one firm plus what it won ("Lafarge Paving & Construction (for winter
        # season – South, West and North Districts)"), but just as many are two firms EACH
        # carrying one ("Coco Paving Limited (for the North, South and West Districts) D.
        # Crupi & Sons Limited (for the East District)"). Admitting the qualified ones
        # recovered ~5 real awards and let in as many invented suppliers, including a 735-
        # character prose bleed and a roster of a dozen firms. So length alone still decides,
        # exactly as #96 had it, and the ~15 that reach here stay out of the archive.
        out.append({
            **common,
            "winner_raw": bidder,
            "award_value": amount,
            "split_award": bool(_ENUMERATED_BIDDER.search(bidder)
                                or _SEGMENTED_BIDDER.search(bidder)
                                or len(bidder) > _MAX_SUPPLIER_NAME),
            # The amount as council wrote it, WITHOUT the trailing "net of all applicable
            # taxes" qualifier: amount.py:parse_amount is strict and refuses any string that
            # is not a single CAD amount, so storing the whole phrase would leave
            # award_value_numeric NULL on every row and silently zero every SUM. Which figure
            # this is, is a property of the column (see schema.sql), not of the string.
            "award_value_raw": f"${value.group(1)}",
        })
    return out


def store_composite_awards(conn) -> int:
    """Ingest the composite-report appendices as awards in their own keyspace (#96). Idempotent.

    This is the archive expanding backwards, not a linking pass: the City's feed publishes 0
    awards for 2009, 1 for 2010 and 12 for 2011, against the 799 sitting in these reports. For
    those years this table IS the record, which is why an appendix with no `document_number`
    is ingested rather than discarded — see the composite_award comment in schema.sql.

    Offline: reads background_pdf.text, so it only sees reports download_composite_reports
    already fetched.
    """
    stored = skipped = 0
    for row in conn.execute(
            "SELECT reference, text FROM background_pdf WHERE text IS NOT NULL AND kind='bgrd' "
            "AND substr(reference,1,4) BETWEEN '2009' AND '2012' ORDER BY reference"):
        for item in parse_composite_appendices(row["text"]):
            if not item["call_number"]:
                continue                  # nothing to key it on; 0 of 1,229 in the corpus
            if item["split_award"]:
                # Several winners on one call that the value section does not key in any
                # scheme split_award_winners reads (per-Item tables, per-district prose), so
                # there is nothing to apportion the money by. Recording the fused name as one
                # firm would invent a supplier and hand it the first winner's money, and the
                # dimension would then carry it as real.
                skipped += 1
                continue
            db.upsert_row(conn, CompositeAward(
                call_number=item["call_number"],
                call_number_raw=item["call_number_raw"],
                reference=row["reference"],
                title=item["description"],
                supplier_name_raw=item["winner_raw"],
                award_value=item["award_value_raw"],
                source="bid_committee_composite",
            ), overwrite=True)
            stored += 1
    conn.commit()
    if skipped:
        # Never silent: a bounded skip that nobody prints reads as full coverage later.
        print(f"    split awards skipped (several winners on one call, #98): {skipped}")
    return stored


def match_composite_titles(conn) -> int:
    """Name title-less solicitations from composite-report appendices already downloaded.

    Offline: reads background_pdf.text, so it only sees reports fetched by
    download_composite_reports. Idempotent.
    """
    items = []
    for row in conn.execute(
            "SELECT text FROM background_pdf WHERE text IS NOT NULL AND kind='bgrd' "
            "AND substr(reference,1,4) BETWEEN '2009' AND '2012'"):
        items.extend(parse_composite_appendices(row["text"]))
    return match_on_supplier_and_value(conn, items, "council_composite")


def download_composite_reports(conn, http, dest_dir=None, log=lambda _m: None) -> int:
    """Download the 2009-2012 staff-report PDFs and store their text. Plain HTTP, no browser.

    Bounded by what store_background_pdfs already indexed off the cached agendas (221 PDFs,
    ~80MB). Resumable and idempotent: rows that already hold text are skipped, so an
    interrupted run costs only what it had not yet fetched.
    """
    from toronto_bids.sources.council import download_pdf

    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext (poppler) is required to read composite reports but was not found on "
            "PATH. Install poppler (e.g. `brew install poppler` / `apt-get install -y "
            "poppler-utils`).")
    dest_dir = dest_dir if dest_dir is not None else config.COUNCIL_DOCS_DIR
    rows = conn.execute(
        "SELECT url, reference FROM background_pdf WHERE text IS NULL AND kind='bgrd' "
        "AND substr(reference,1,4) BETWEEN '2009' AND '2012' ORDER BY reference").fetchall()
    log(f"  composite reports to fetch: {len(rows)}")
    stored = 0
    for i, row in enumerate(rows, 1):
        try:
            info = download_pdf(http, row["url"], dest_dir)
            db.upsert_row(conn, BackgroundPdf(
                url=row["url"], reference=row["reference"], kind="bgrd",
                local_path=info["local_path"], sha256=info["sha256"], text=info["text"],
            ), overwrite=True)
            conn.commit()
            stored += 1
        except Exception as exc:
            conn.rollback()
            log(f"    skipped {row['reference']}: {exc}")   # one bad PDF must not end the run
        if i % 25 == 0:
            log(f"    {i}/{len(rows)}")
    return stored


def _title_less_awards_by_value(conn) -> dict:
    """Title-less awards indexed by rounded value — the left side of the (value, supplier) join."""
    by_value = {}
    for row in conn.execute(
            "SELECT a.document_number d, a.supplier_name_raw s, a.award_amount_numeric v "
            "FROM award a JOIN solicitation sol ON sol.document_number = a.document_number "
            "WHERE a.source='odata' AND a.award_amount_numeric IS NOT NULL "
            "AND a.supplier_name_raw IS NOT NULL AND sol.title IS NULL"):
        by_value.setdefault(round(row["v"]), []).append((supplier_tokens(row["s"]), row["d"]))
    return by_value


def match_on_supplier_and_value(conn, items, title_source: str) -> int:
    """Name title-less solicitations from items carrying (title, winner_raw, award_value).

    The one join shared by the two sources that have no identifier to offer: agenda items
    (#77) and composite-report appendices (#93). The value carries the match and the supplier
    only confirms it; a non-unique match is dropped rather than guessed. Idempotent — the
    UPDATE is guarded on `title IS NULL`.
    """
    by_value = _title_less_awards_by_value(conn)
    filled = {}
    for item in items:
        want = supplier_tokens(item["winner_raw"])
        docs = {doc for toks, doc in by_value.get(round(item["award_value"]), [])
                if want & toks}
        if len(docs) == 1:
            filled.setdefault(docs.pop(), item["title"])
    conn.executemany(
        "UPDATE solicitation SET title = ?, title_source = ? "
        "WHERE document_number = ? AND title IS NULL",
        [(t, title_source, d) for d, t in filled.items()])
    conn.commit()
    return len(filled)
