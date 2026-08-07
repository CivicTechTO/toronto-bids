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

import json
import pathlib
import re
import shutil
from contextlib import contextmanager

from lxml import etree
from lxml import html as _html
from lxml.html import HtmlComment

from toronto_bids import config
from toronto_bids.amount import parse_amount
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.linking.supplier import supplier_key
from toronto_bids.models import BackgroundPdf, Bid, CouncilItem
from toronto_bids.sources.council import pdf_kind
from toronto_bids.store import db

AGENDA_URL = "https://secure.toronto.ca/council/report.do"

# "BA189.1 - Award of Ariba Document Number 3234668279 to GHD Limited for the ..."
# BA = Bid Award Panel (2017-01-04 onward). BD = Bid Committee, its predecessor, which ran
# 2009-02-04 to 2016-12-21 — three weeks before BA's first meeting (#90). Same agenda
# structure throughout: same "Award of <id> to <supplier> for <subject>" heading, same
# "Contract Award Value ... net of all applicable taxes" block, same bid tables, same
# background-file PDFs. One series succeeded the other; nothing else changed.
_ITEM_HEADING = re.compile(
    r"^\s*(?P<ref>B[AD]\d+\.\d+)\s*-\s*(?P<title>.+?)\s*$", re.DOTALL
)
_TEN_DIGIT = re.compile(r"\d{10}")
_WS = re.compile(r"\s+")
# Collapse spaces/tabs but keep newlines: item headings are found by line.
_WS_LINES = re.compile(r"[ \t]+")

# Item titles that are panel housekeeping, never an award.
_NOT_AN_AWARD = re.compile(
    r"^(election of|confirmation of minutes|declarations? of)", re.IGNORECASE
)


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
        items.append(
            {
                "reference": f"{meeting}.{m.group('ref').split('.', 1)[1]}",
                "meeting": meeting,
                "title": title,
                "document_numbers": docs,
            }
        )
    return items


# TMMIS answers 200 with an error page for a reference that is not real, and it has more than
# one way of saying so. Missing either one records an error page as an agenda.
_MISSING_MARKERS = (
    "this meeting is not available",  # e.g. 2018.BA10
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
    from playwright.sync_api import sync_playwright

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
                    page.goto(
                        f"{AGENDA_URL}?meeting={meeting}&type=agenda",
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
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
    m = re.search(
        r"Meeting Date:\s*</?[^>]*>?\s*\w+day,\s*(\w+)\s+(\d{1,2}),\s*(\d{4})",
        _clean(_html.fromstring(html).text_content()) if "<" in html else html,
    )
    if not m:
        m = re.search(
            r"\w+day,\s*(\w+)\s+(\d{1,2}),\s*(\d{4})",
            _clean(_html.fromstring(html).text_content()),
        )
    if not m:
        return None
    month = [
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ].index(m.group(1).lower()) + 1
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


def discover_meetings(
    fetch,
    log=lambda _m: None,
    max_per_term=260,
    stop_after_misses=4,
    *,
    term_starts,
    closed_terms: dict | None = None,
    on_term_closed=lambda key, last_n: None,
):
    """Walk each term's meetings, returning {reference: html} for every agenda that exists.

    References cannot be derived from the City's published schedule: it lists dates but omits
    MTG # for every term before 2022-2026, and inferring numbers from date order is wrong in
    both directions (measured against four references confirmed by fetching: it landed 188 vs
    189, 51 vs 50, 99 vs 100, 2 vs 1). The schedule both duplicates and omits meetings, and
    the drift is not constant, so there is no offset to correct for.

    So: probe, and let each page's own date confirm what it is. Walking n upward within a
    term is cheap because numbering is contiguous; only the session-year prefix has to be
    guessed, and it only ever advances.

    `term_starts` is the committee's term list (e.g. `zoo_board.ZB_TERM_STARTS`); the Bid
    Award Panel is abolished, so no in-repo default remains. Other TMMIS committees (e.g.
    the Zoo Board's ZB series, #135) reuse this prober by passing their own list — same
    probe-and-confirm design, different (series, start_year, term, first_n) tuples.

    Every term but the LAST in `term_starts` is, by construction, a closed council term: it is
    followed by a newer one, which only happens once the term it precedes has already ended and
    can never produce another meeting (#177). Once such a term's miss boundary is found, that
    boundary is permanent — but nothing remembered it, so every run re-walked into the same
    dead miss range on live network probes, forever, for a term that provably cannot change. Two
    hooks, not a hardcoded rule: `closed_terms` (`{f"{series}{term}": last_real_n}`) lets a
    closed term skip straight to its known end instead of probing past it, and
    `on_term_closed(key, last_n)` fires the first time a non-final term's boundary is confirmed,
    so the caller can persist it. Passing neither reproduces the old always-probe behaviour
    exactly — this is additive, not a change to what a fresh run discovers.
    """
    found = {}
    closed_terms = closed_terms or {}
    last_index = len(term_starts) - 1
    for i, (series, start_year, term, first_n) in enumerate(term_starts):
        key = f"{series}{term}"
        cap = first_n + max_per_term - 1
        # The cap only ever narrows a HISTORICAL term. The last entry is the one still
        # accepting new meetings, so it must always be probed for real — never frozen by a
        # stale or mistaken closed_terms entry.
        if i != last_index and key in closed_terms:
            cap = min(cap, closed_terms[key])
        session = start_year
        misses = 0
        for n in range(first_n, cap + 1):
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
                log(
                    f"  {series} {term}: no meeting {n} (miss {misses}/{stop_after_misses})"
                )
                if misses >= stop_after_misses:
                    last_n = n - misses
                    log(f"  {series} {term}: stopping after {last_n} meetings")
                    if i != last_index and key not in closed_terms:
                        on_term_closed(key, last_n)
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
            db.upsert_row(
                conn,
                CouncilItem(reference=item["reference"], title=item["title"]),
                overwrite=True,
            )
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
    missing = {
        r["document_number"]
        for r in conn.execute(
            "SELECT document_number FROM solicitation WHERE title IS NULL"
        )
    }
    if not missing:
        return 0
    filled = {}
    for row in conn.execute(
        "SELECT reference, title FROM council_item WHERE title IS NOT NULL"
    ):
        for hit in _TEN_DIGIT.findall(row["title"]):
            doc = normalize_document_number(hit)
            # First council item wins: agendas are walked oldest-first, so the original
            # award beats any later amendment naming the same document.
            if doc in missing and doc not in filled:
                filled[doc] = row["title"]
    conn.executemany(
        "UPDATE solicitation SET title = ?, title_source = 'bid_award_panel' "
        "WHERE document_number = ? AND title IS NULL",
        [(t, d) for d, t in filled.items()],
    )
    conn.commit()
    return len(filled)


def cached_agendas(agenda_dir) -> dict:
    """{reference: html} for every agenda already on disk. Offline."""
    root = pathlib.Path(agenda_dir)
    if not root.is_dir():
        return {}
    return {p.stem: p.read_text(errors="replace") for p in sorted(root.glob("*.html"))}


def _closed_terms_path(agenda_dir) -> pathlib.Path:
    return pathlib.Path(agenda_dir) / ".closed_terms.json"


def _load_closed_terms(agenda_dir) -> dict:
    """A historical term's confirmed miss boundary, `{"EP2018-2022": 27}`-shaped (#177).

    Missing or corrupt reads as empty rather than raising: this is a resumability cache, not
    a record — losing it costs one term's worth of miss-probing on the next run, exactly the
    pre-#177 behaviour, never a wrong discovery.
    """
    path = _closed_terms_path(agenda_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def scrape_agendas(
    agenda_dir, *, virtual_display: bool = False, log=lambda _m: None, term_starts
) -> dict:
    """Discover and cache every agenda, returning {reference: html}.

    Resumable and safe to re-run: an agenda already on disk is never refetched, so a second
    run costs only the probes past the last meeting — but "the last meeting" is only ever
    moving for the CURRENT term. Every earlier term_starts entry is a closed council term (a
    newer one already follows it in the list, which only happens once the term it precedes has
    ended), so its miss boundary is permanent. Before #177 nothing remembered that, and every
    run re-probed every closed term's dead miss range on live network requests, forever —
    for the EP body alone, most of a nightly nine-figure minute count. `.closed_terms.json`
    in `agenda_dir` records each closed term's confirmed last meeting the first time
    `discover_meetings` finds it, so later runs skip straight to it. The one term that can
    never be cached this way is whichever is LAST in `term_starts` — see `discover_meetings`.

    `term_starts` is the committee's term list (e.g. `zoo_board.ZB_TERM_STARTS`); the Bid
    Award Panel is abolished, so no in-repo default remains. Forwarded to
    `discover_meetings` unchanged — see that function for why other TMMIS committees pass
    their own list instead.
    """
    root = pathlib.Path(agenda_dir)
    root.mkdir(parents=True, exist_ok=True)
    closed_terms = _load_closed_terms(agenda_dir)

    def _persist_closed(key: str, last_n: int) -> None:
        closed_terms[key] = last_n
        _closed_terms_path(root).write_text(json.dumps(closed_terms))

    with agenda_fetcher(virtual_display=virtual_display) as fetch_live:

        def fetch(meeting: str) -> str:
            cached = root / f"{meeting}.html"
            if cached.exists():
                return cached.read_text(errors="replace")
            html = fetch_live(meeting)
            if not agenda_is_missing(html):
                # Store <main> only: the rest is nav, sharing widgets and a language picker.
                match = re.search(r"(<main.*</main>)", html, re.DOTALL)
                cached.write_text(match.group(1) if match else html)
            return html

        return discover_meetings(
            fetch,
            log=log,
            term_starts=term_starts,
            closed_terms=closed_terms,
            on_term_closed=_persist_closed,
        )


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
        if (
            "/legdocs/mmis/" not in url
            or not url.lower().endswith(".pdf")
            or url in seen
        ):
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
            db.upsert_row(
                conn,
                BackgroundPdf(
                    url=pdf["url"], reference=pdf["reference"], kind=pdf["kind"]
                ),
                overwrite=False,
            )
            n += 1
    conn.commit()
    return n


# Bid tables name their first column six ways across the corpus.
# The plural is not decoration: BA189.3 heads its table "Suppliers", and without the `s?` the
# whole table is declined and its five bids are silently dropped. Found by #94, on a BA
# agenda, because the Bid Committee parser accepted a header the Bid Award Panel one refused.
_BIDDER_HDR = re.compile(
    r"^\s*(supplier|bidder|proponent|firm|vendor|company|respondent)s?"
    r"[\s/]*(name|names|or proponent name)?\s*$",
    re.IGNORECASE,
)
_PRICE_HDR = re.compile(r"bid price|bid amount|price|quotation", re.IGNORECASE)
# "including H.S.T." vs "excluding H.S.T." is a real difference — 1,307 tables say one and
# 1,048 the other. A bare price column would silently mix the two bases.
# "incl\w*\.?" not "includ\w*": the Bid Committee overwhelmingly abbreviates, and
# "Bid Price (Incl. HST)" is its single most common price header (587 of them). Requiring the
# full word reads those as basis-unknown, which is the one thing a bid price must not be —
# 5,801 bids include HST and 4,097 exclude it, so an unmarked price is two incomparable
# things in one column (#84).
_HST_INCLUDING = re.compile(
    r"\bincl\w*\.?\s*(all applicable taxes|h\.?s\.?t)", re.IGNORECASE
)
_HST_EXCLUDING = re.compile(
    r"\bexcl\w*\.?\s*(all applicable taxes|h\.?s\.?t)", re.IGNORECASE
)
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
_CELL_BLOCK_END = re.compile(r"</(p|div|li|tr|td)>", re.IGNORECASE)
_CELL_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _cell_lines(cell) -> list[str]:
    """A table cell's values, one per line of block markup.

    text_content() would fuse them: a BD price column comes back as
    "$ 224,156.52$ 231,817.47$ 240,633.50" with nothing to split on.
    """
    markup = _CELL_BR.sub("\n", etree.tostring(cell, encoding="unicode"))
    text = _html.fromstring(_CELL_BLOCK_END.sub("\n", markup)).text_content()
    return [
        line.strip() for line in text.replace("\xa0", " ").split("\n") if line.strip()
    ]


# A BD heading occupies its cell alone ("Firm Name"), unlike the appendix label
# "Recommended Bidder:" which carries its value beside it.
_BD_BIDDER_HDR = re.compile(
    r"^(bidder|firm|proponent|company|contractor|supplier|vendor)s?[\s/]*(name|names)?\s*:?$",
    re.IGNORECASE,
)
# What can stand in a price column: an amount, or the outcome the City writes instead of one.
_BD_PRICEY = re.compile(
    r"\$|\d[\d,]*\.\d{2}|non.?compliant|no bid|withdrawn|informal", re.IGNORECASE
)


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
        bidder_col = next(
            (i for i, c in enumerate(columns) if c and _BD_BIDDER_HDR.match(c[0])), None
        )
        if bidder_col is None:
            continue
        price_col = next(
            (
                i
                for i, c in enumerate(columns)
                if c and i != bidder_col and _PRICE_HDR.search(c[0])
            ),
            None,
        )
        if price_col is None:
            continue
        price_header = columns[price_col][0]

        pairs = []
        if len(columns[bidder_col]) > 1:  # the column's values share its header cell
            pairs.append((columns[bidder_col][1:], columns[price_col][1:]))
        for later in rows[index + 1 :]:  # ...or sit in the rows below it
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
                break  # left the bid table
            pairs.append((names, prices))

        for names, prices in pairs:
            if len(names) != len(prices):
                continue  # cannot pair positionally; refuse
            for name, price in zip(names, prices):
                name = _NAME_MARKERS.sub(
                    "", _ROW_NUMBER_PREFIX.sub("", _NAME_MARKERS.sub("", name))
                )
                if not name or not _BD_PRICEY.search(price):
                    continue
                out.append(
                    {
                        "reference": reference,
                        "document_number": docs[0] if docs else None,
                        "bidder_name_raw": name,
                        "bid_price": price or None,
                        "hst_basis": _hst_basis(price_header),
                        "price_header": price_header,
                    }
                )
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
                docs = [
                    d
                    for hit in _TEN_DIGIT.findall(m.group("title"))
                    if (d := normalize_document_number(hit))
                ]
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
        price_col = next(
            (i for i, h in enumerate(header) if i and _PRICE_HDR.search(h)), None
        )
        price_header = header[price_col] if price_col is not None else None
        for row in rows[1:]:
            cells = [_clean(c.text_content()) for c in row.xpath(".//td|.//th")]
            # An undeclared leading row-number column shifts every cell left, dropping the
            # bidder name into the price. Realign against the header before reading either.
            while (
                len(cells) > len(header)
                and cells
                and _ROW_NUMBER_CELL.match(cells[0] or "")
            ):
                cells = cells[1:]
            if not cells or not cells[0]:
                continue
            name = _NAME_MARKERS.sub(
                "", _ROW_NUMBER_PREFIX.sub("", _NAME_MARKERS.sub("", cells[0]))
            )
            if not name:
                continue
            price = (
                cells[price_col]
                if (price_col is not None and len(cells) > price_col)
                else None
            )
            out.append(
                {
                    "reference": reference,
                    # Pre-2019 items name no document number (Toronto adopted Ariba ~2019), so a
                    # bid can be real and unattributable. Kept anyway — #77 wants exactly these.
                    "document_number": docs[0] if docs else None,
                    "bidder_name_raw": name,
                    "bid_price": price or None,
                    "hst_basis": _hst_basis(price_header) if price_header else None,
                    "price_header": price_header,
                }
            )
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
_WINNER = re.compile(r"\bto\s+(.+?)\s+for\s+", re.IGNORECASE)
# Council publishes THREE figures per award. Calibrated against 980 Ariba-era items where the
# document number gives ground truth: award_amount is the "net of all applicable taxes" one
# (820/980 = 84%). "including HST" matched 0; "net of HST recoveries" matched 4.
_NET_OF_TAXES = re.compile(
    r"\$([\d,]+(?:\.\d+)?)\s*net of all applicable taxes", re.IGNORECASE
)
_ITEM_SPLIT_CAP = re.compile(
    r"(B[AD]\d+\.\d+) - "
)  # capturing: keeps the 'BD106.3' item token


def parse_pre_ariba_awards(html: str, meeting: str | None = None) -> list[dict]:
    """Items that name no document number, with the winner and value needed to match them.

    Toronto adopted Ariba around 2019, so a 2017-2018 agenda identifies its award by Call
    Number ("Award of Call Number 6032-16-3114 to MeteoGroup...") and our spine is keyed on a
    10-digit Ariba number backfilled later. There is no identifier in common (#77), and
    `Contract_Number_Purchase_Order` is empty on all 7,592 feed records.

    But the item names its winner and its value, and `award` holds both. That is the join.

    When `meeting` is given, each item carries a full council `reference` (e.g. '2016.BD106.3')
    built from the meeting's year prefix and the item's 'BD106.3' token — so a match can be
    recorded against the reference (#124), not only used to fill a title.
    """
    text = _WS_LINES.sub(" ", _html.fromstring(html).text_content())
    parts = _ITEM_SPLIT_CAP.split(
        text
    )  # [pre, reftoken1, chunk1, reftoken2, chunk2, ...]
    year = (meeting or "").split(".")[0] if meeting else None
    out = []
    for i in range(1, len(parts) - 1, 2):
        reftoken, chunk = parts[i], parts[i + 1]
        head = chunk[:400]
        if _TEN_DIGIT.search(head):
            continue  # names a doc number — joins directly, not our case
        winner, value = _WINNER.search(head), _NET_OF_TAXES.search(chunk)
        if not (winner and value):
            continue
        amount = parse_amount(value.group(1))
        if amount is None:
            continue
        out.append(
            {
                "reference": f"{year}.{reftoken}" if year else None,
                "title": _clean(head.split("\n")[0]),
                "winner_raw": _clean(winner.group(1)),
                "award_value": amount,
            }
        )
    return out


# Legal-form noise that varies freely between how council writes a supplier and how the feed
# does: 'Sanscon Construction Limited' vs 'Sanscon Construction Ltd.', 'Liftsafe Engineering &
# Service Group' vs '... and Service Group', 'The Municipal Infrastructure Group,'.
_LEGAL_NOISE = re.compile(
    r"\b(limited|ltd|incorporated|inc|corporation|corp|company|co|"
    r"lp|llp|ulc|holdings|group|canada|ontario)\b",
    re.IGNORECASE,
)
_LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)


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
            continue  # 2019+ names a document number; no need to guess
        items.extend(parse_pre_ariba_awards(html, meeting))
    return match_on_supplier_and_value(conn, items, "council_pre_ariba")


def _awards_by_value(conn):
    """ALL odata awards indexed by rounded value -> [(supplier_tokens, document_number)].
    Unlike _title_less_awards_by_value, this includes titled solicitations: a solicitation with
    a title still needs its bids linked."""
    by_value = {}
    for row in conn.execute(
        "SELECT document_number d, supplier_name_raw s, award_amount_numeric v FROM award "
        "WHERE source='odata' AND award_amount_numeric IS NOT NULL AND supplier_name_raw IS NOT NULL"
    ):
        by_value.setdefault(round(row["v"]), []).append(
            (supplier_tokens(row["s"]), row["d"])
        )
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
            continue  # 2019+ names a document number directly
        for item in parse_pre_ariba_awards(html, meeting):
            if not item["reference"]:
                continue
            want = supplier_tokens(item["winner_raw"])
            docs = {
                doc
                for toks, doc in by_value.get(round(item["award_value"]), [])
                if want & toks
            }
            if len(docs) == 1:
                links[item["reference"]] = docs.pop()
    conn.execute("DELETE FROM solicitation_link")
    conn.executemany(
        "INSERT INTO solicitation_link (reference, document_number, method) VALUES (?, ?, 'council_pre_ariba')",
        list(links.items()),
    )
    conn.commit()
    return len(links)


def store_composite_awards(conn) -> int:
    """Extract and backfill composite awards from cached LLM extractions (#205)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "composite")
    return result["awards_written"]


def match_composite_titles(conn) -> int:
    """Name title-less solicitations from composite awards already stored.

    Reads from composite_award (populated by store_composite_awards via LLM extraction)
    rather than re-parsing PDFs. Idempotent.
    """
    items = [
        {
            "title": r["title"],
            "winner_raw": r["supplier_name_raw"],
            "award_value": r["award_value_numeric"],
        }
        for r in conn.execute(
            "SELECT title, supplier_name_raw, award_value_numeric FROM composite_award "
            "WHERE award_value_numeric IS NOT NULL AND supplier_name_raw IS NOT NULL"
        )
    ]
    return match_on_supplier_and_value(conn, items, "council_composite")



# The staff reports of BA items whose agenda tabulates no bids (#83). Everything else the
# panel handled already has its bids from the agenda itself (#84/#94), and the reports of
# title-less solicitations do not exist at all — the panel only sees awards above the
# delegation threshold, so a solicitation is never both title-less and panel-handled (0 of
# them, measured). These ~229 are the whole of what the PDFs can still add.
# `sha256 IS NULL`, not `text IS NULL`, is what "not yet fetched" means. 16 of the composite
# reports are image-only scans with no embedded fonts, so pdftotext yields nothing and their
# text stays NULL forever — keyed on text, every run re-downloaded those 16 in perpetuity.
# The hash records that we have the bytes, whether or not anything could read them.
_BA_REPORTS_WITHOUT_BIDS = """
    SELECT p.url, p.reference FROM background_pdf p
    JOIN council_item ci ON ci.reference = p.reference
    WHERE p.sha256 IS NULL AND p.kind='bgrd' AND p.reference NOT LIKE '%.BD%'
      AND NOT EXISTS (SELECT 1 FROM bid b WHERE b.reference = p.reference)
    ORDER BY p.reference
"""
_COMPOSITE_REPORTS = """
    SELECT url, reference FROM background_pdf WHERE sha256 IS NULL AND kind='bgrd'
      AND substr(reference,1,4) BETWEEN '2009' AND '2012' ORDER BY reference
"""


def download_reports(
    conn, http, query: str, label: str = "reports", dest_dir=None, log=lambda _m: None
) -> int:
    """Download staff-report PDFs and store their text. Plain HTTP, no browser.

    Only TMMIS itself is Akamai-gated; the legdocs PDFs are ordinary HTTP. Bounded by what
    store_background_pdfs already indexed off the cached agendas, and resumable: rows that
    already hold text are skipped, so an interrupted run costs only what it had not fetched.
    """
    from toronto_bids.sources.council import download_pdf

    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext (poppler) is required to read staff reports but was not found on "
            "PATH. Install poppler (e.g. `brew install poppler` / `apt-get install -y "
            "poppler-utils`)."
        )
    dest_dir = dest_dir if dest_dir is not None else config.COUNCIL_DOCS_DIR
    rows = conn.execute(query).fetchall()
    log(f"  {label} to fetch: {len(rows)}")
    stored = 0
    for i, row in enumerate(rows, 1):
        try:
            info = download_pdf(http, row["url"], dest_dir)
            db.upsert_row(
                conn,
                BackgroundPdf(
                    url=row["url"],
                    reference=row["reference"],
                    kind="bgrd",
                    local_path=info["local_path"],
                    sha256=info["sha256"],
                    text=info["text"],
                ),
                overwrite=True,
            )
            conn.commit()
            stored += 1
        except Exception as exc:
            conn.rollback()
            log(
                f"    skipped {row['reference']}: {exc}"
            )  # one bad PDF must not end the run
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
        "AND a.supplier_name_raw IS NOT NULL AND sol.title IS NULL"
    ):
        by_value.setdefault(round(row["v"]), []).append(
            (supplier_tokens(row["s"]), row["d"])
        )
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
        docs = {
            doc
            for toks, doc in by_value.get(round(item["award_value"]), [])
            if want & toks
        }
        if len(docs) == 1:
            filled.setdefault(docs.pop(), item["title"])
    conn.executemany(
        "UPDATE solicitation SET title = ?, title_source = ? "
        "WHERE document_number = ? AND title IS NULL",
        [(t, title_source, d) for d, t in filled.items()],
    )
    conn.commit()
    return len(filled)
