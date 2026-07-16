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
from contextlib import contextmanager

from lxml import html as _html

from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import CouncilItem
from toronto_bids.store import db

AGENDA_URL = "https://secure.toronto.ca/council/report.do"

# "BA189.1 - Award of Ariba Document Number 3234668279 to GHD Limited for the ..."
_ITEM_HEADING = re.compile(r"^\s*(?P<ref>BA\d+\.\d+)\s*-\s*(?P<title>.+?)\s*$", re.S)
_TEN_DIGIT = re.compile(r"\d{10}")
_WS = re.compile(r"\s+")

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


def agenda_is_missing(html: str) -> bool:
    """TMMIS answers 200 with 'This meeting is not available.' for a reference that isn't real.

    Meeting numbering restarts each council term and the year prefix is a session year, so
    enumerating references means probing. A miss is normal, not an error.
    """
    return "this meeting is not available" in (html or "").lower()


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
TERM_STARTS = [(2017, "2014-2018"), (2019, "2018-2022"), (2023, "2022-2026")]


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
    for start_year, term in TERM_STARTS:
        session = start_year
        misses = 0
        for n in range(1, max_per_term + 1):
            html = ref = None
            # The prefix only ever advances, and only at a November boundary.
            for candidate in (session, session + 1):
                probe = f"{candidate}.BA{n}"
                page = fetch(probe)
                if not agenda_is_missing(page):
                    html, ref, session = page, probe, candidate
                    break
            if html is None:
                misses += 1
                log(f"  {term}: no meeting {n} (miss {misses}/{stop_after_misses})")
                if misses >= stop_after_misses:
                    log(f"  {term}: stopping after {n - misses} meetings")
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
        "UPDATE solicitation SET title = ?, source = 'bid_award_panel' "
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
