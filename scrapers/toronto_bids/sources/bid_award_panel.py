"""Scrape and parse Bid Award Panel (BA) and Bid Committee (BD) agendas from TMMIS.

Fetching requires headed Chromium (Akamai blocks plain HTTP and headless); parsing is offline.
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

# BA = Bid Award Panel (2017+), BD = Bid Committee (2009-2016); same agenda structure.
_ITEM_HEADING = re.compile(
    r"^\s*(?P<ref>B[AD]\d+\.\d+)\s*-\s*(?P<title>.+?)\s*$", re.DOTALL
)
_TEN_DIGIT = re.compile(r"\d{10}")
_WS = re.compile(r"\s+")
# Keep newlines; item headings are split by line.
_WS_LINES = re.compile(r"[ \t]+")

_NOT_AN_AWARD = re.compile(
    r"^(election of|confirmation of minutes|declarations? of)", re.IGNORECASE
)


def _clean(text):
    return _WS.sub(" ", text or "").strip()


def parse_agenda(html: str, meeting: str) -> list[dict]:
    """Parse award items from one agenda page into dicts with reference, meeting, title, and document_numbers."""
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


# TMMIS returns HTTP 200 with an error page for nonexistent meeting references.
_MISSING_MARKERS = (
    "this meeting is not available",
    "the published report was not found",
)


def agenda_is_missing(html: str) -> bool:
    """Return True when TMMIS served a missing-meeting error page."""
    text = (html or "").lower()
    return any(marker in text for marker in _MISSING_MARKERS)


@contextmanager
def agenda_fetcher(virtual_display: bool = False):
    """Yield fetch(meeting) -> html using one shared headed Chromium instance (Akamai blocks headless)."""
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
    """Extract the agenda's meeting date as YYYY-MM-DD, or None."""
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


def _read_agenda_date(page):
    from datetime import date

    try:
        when = agenda_date(page) if page.strip() else None
        return date.fromisoformat(when).isoformat() if when else None
    except (ValueError, etree.ParserError):
        return None


def _agenda_in_term(page, term):
    if agenda_is_missing(page):
        return None
    when = _read_agenda_date(page)
    if not when:
        raise RuntimeError("unrecognized agenda response; no valid meeting date")
    start, end = term.split("-")
    return when if f"{start}-11-01" <= when < f"{end}-11-01" else None


def _automatic_term_plan(fetch, term_starts, today, log):
    """Probe first agendas for each term and extend 4-year council terms through today."""
    terms = list(term_starts)
    first = {}

    def confirm(entry):
        series, year, term, first_n = entry
        for candidate in (year, year + 1):
            ref = f"{candidate}.{series}{first_n}"
            when = _agenda_in_term(fetch(ref), term)
            if when:
                return {"successor": ref, "date": when}
        return None

    for entry in terms:
        proof = confirm(entry)
        if proof:
            first[f"{entry[0]}{entry[2]}"] = proof
    if not terms:
        return terms, first
    series, year, term, _ = terms[-1]
    start, end = map(int, term.split("-"))
    if end - start != 4:
        raise ValueError(f"automatic council terms require a four-year seed, got {term!r}")
    while end <= int(today[:4]):
        start, end = end, end + 4
        entry = (series, start, f"{start}-{end}", 1)
        proof = confirm(entry)
        if proof:
            terms.append(entry)
            first[f"{series}{entry[2]}"] = proof
            log(f"  {series} {entry[2]}: confirmed first agenda {proof['successor']}")
        else:
            log(f"  {series} {entry[2]}: first agenda not confirmed; no closure inferred")
    return terms, first


def discover_meetings(
    fetch,
    log=lambda _m: None,
    max_per_term=260,
    stop_after_misses=4,
    *,
    term_starts,
    closed_terms: dict | None = None,
    on_term_closed=lambda key, last_n: None,
    auto_terms=False,
    today=None,
):
    """Probe contiguous meeting numbers per term and return {reference: html} for existing agendas.

    Shared by BA/BD and active TMMIS committees (e.g. Zoo/EP). With auto_terms=True, extends
    4-year seeds through today and only closes an older term once a successor agenda has occurred.
    """
    if auto_terms:
        from datetime import date
        today = today or date.today().isoformat()
        date.fromisoformat(today)
        live_fetch = fetch
        probes = {}

        def fetch(ref):
            if ref not in probes:
                probes[ref] = live_fetch(ref)
            return probes[ref]

        term_starts, first_agendas = _automatic_term_plan(fetch, term_starts, today, log)
    found = {}
    closed_terms = closed_terms or {}
    last_index = len(term_starts) - 1
    for i, (series, start_year, term, first_n) in enumerate(term_starts):
        key = f"{series}{term}"
        cap = first_n + max_per_term - 1
        proof = None
        if auto_terms:
            proof = next((first_agendas[f"{later[0]}{later[2]}"]
                          for later in term_starts[i + 1:]
                          if later[0] == series and f"{later[0]}{later[2]}" in first_agendas
                          and first_agendas[f"{later[0]}{later[2]}"]["date"] <= today), None)
        can_close = bool(proof) if auto_terms else i != last_index
        saved = closed_terms.get(key)
        if auto_terms:
            valid_boundary = (isinstance(saved, dict) and proof is not None
                              and saved.get("successor") == proof["successor"]
                              and saved.get("date") == proof["date"]
                              and type(saved.get("last_n")) is int
                              and saved["last_n"] >= first_n - 1)
        else:
            valid_boundary = can_close and saved is not None
        if can_close and valid_boundary:
            cap = min(cap, saved["last_n"] if isinstance(saved, dict) else saved)
        session = start_year
        misses = 0
        for n in range(first_n, cap + 1):
            html = ref = None
            # Session year prefix only advances at a November boundary.
            for candidate in (session, session + 1):
                probe = f"{candidate}.{series}{n}"
                page = fetch(probe)
                if (_agenda_in_term(page, term) if auto_terms else not agenda_is_missing(page)):
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
                    if can_close and not valid_boundary:
                        on_term_closed(key, {"last_n": last_n, **proof} if auto_terms else last_n)
                    break
                continue
            misses = 0
            found[ref] = html
            log(f"  {ref:<12} {agenda_date(html)}  ({len(found)} so far)")
    return found


def store_items(conn, agendas: dict) -> int:
    """Upsert all parsed award items from {reference: html} into council_item. Idempotent."""
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
    """Populate NULL solicitation.title values from council_item titles naming a 10-digit document number. Idempotent."""
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
            # First council item wins (oldest-first), so the original award beats later amendments.
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
    """Load cached {reference: html} agenda files from agenda_dir."""
    root = pathlib.Path(agenda_dir)
    if not root.is_dir():
        return {}
    return {p.stem: p.read_text(errors="replace") for p in sorted(root.glob("*.html"))}


def _closed_terms_path(agenda_dir) -> pathlib.Path:
    return pathlib.Path(agenda_dir) / ".closed_terms.json"


def _load_closed_terms(agenda_dir) -> dict:
    """Load persisted closed-term boundaries from .closed_terms.json, returning {} if missing or corrupt."""
    path = _closed_terms_path(agenda_dir)
    if not path.exists():
        return {}
    try:
        records = json.loads(path.read_text())
        if not isinstance(records, dict):
            return {}
        return {key: value for key, value in records.items()
                if isinstance(key, str) and (
                    (type(value) is int and value >= 0) or
                    (isinstance(value, dict) and type(value.get("last_n")) is int
                     and value["last_n"] >= 0))}
    except (json.JSONDecodeError, OSError):
        return {}


def scrape_agendas(
    agenda_dir, *, virtual_display: bool = False, log=lambda _m: None, term_starts,
    auto_terms=False, today=None
) -> dict:
    """Discover, cache, and return {reference: html} agendas, persisting closed-term boundaries.

    With auto_terms=True, cached pages must carry a valid date inside their council term.
    """
    root = pathlib.Path(agenda_dir)
    root.mkdir(parents=True, exist_ok=True)
    closed_terms = _load_closed_terms(agenda_dir)

    def cache_is_agenda(meeting, page):
        if not auto_terms:
            return True
        year, code = meeting.split(".", 1)
        year = int(year)
        if not any(code.startswith(entry[0]) for entry in term_starts):
            return False
        when = _read_agenda_date(page)
        # Session-year prefixes cover Nov/Dec of (year - 1) through Dec of year.
        return bool(when and f"{year - 1}-11-01" <= when < f"{year + 1}-01-01")

    def _persist_closed(key: str, last_n) -> None:
        closed_terms[key] = last_n
        _closed_terms_path(root).write_text(json.dumps(closed_terms))

    with agenda_fetcher(virtual_display=virtual_display) as fetch_live:

        def fetch(meeting: str) -> str:
            cached = root / f"{meeting}.html"
            if cached.exists():
                held = cached.read_text(errors="replace")
                if cache_is_agenda(meeting, held):
                    return held
                log(f"  {meeting}: cached page has no valid term date; checking the source")
            html = fetch_live(meeting)
            if not agenda_is_missing(html) and cache_is_agenda(meeting, html):
                # Store <main> only to omit navigation chrome.
                match = re.search(r"(<main.*</main>)", html, re.DOTALL)
                cached.write_text(match.group(1) if match else html)
            return html

        return discover_meetings(
            fetch,
            log=log,
            term_starts=term_starts,
            closed_terms=closed_terms,
            on_term_closed=_persist_closed,
            auto_terms=auto_terms,
            today=today,
        )


def parse_agenda_pdfs(html: str, meeting: str) -> list[dict]:
    """Extract legdocs PDF links from an agenda page, attributed to the preceding item heading.

    Links before the first item heading are attributed to the meeting reference.
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
    """Index staff-report PDF URLs from parsed agendas into background_pdf without downloading. Idempotent."""
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


# Match singular/plural bidder headers (e.g. "Suppliers" in BA189.3).
_BIDDER_HDR = re.compile(
    r"^\s*(supplier|bidder|proponent|firm|vendor|company|respondent)s?"
    r"[\s/]*(name|names|or proponent name)?\s*$",
    re.IGNORECASE,
)
_PRICE_HDR = re.compile(r"bid price|bid amount|price|quotation", re.IGNORECASE)
# Match abbreviated "Incl."/"Excl." forms common in BD headers (e.g. "Bid Price (Incl. HST)").
_HST_INCLUDING = re.compile(
    r"\bincl\w*\.?\s*(all applicable taxes|h\.?s\.?t)", re.IGNORECASE
)
_HST_EXCLUDING = re.compile(
    r"\bexcl\w*\.?\s*(all applicable taxes|h\.?s\.?t)", re.IGNORECASE
)
# Trailing footnote markers kept on raw prices but stripped when parsing.
_FOOTNOTE = re.compile(r"[\s*^+†‡§]+$")
# Leading row numbers appear either inline ("1. Name") or as an extra leading cell.
_ROW_NUMBER_CELL = re.compile(r"^\s*\d+\.?\s*$")
_ROW_NUMBER_PREFIX = re.compile(r"^\s*\d+[.)]\s+")
# Strip footnote markers from bidder names so they match across sources.
_NAME_MARKERS = re.compile(r"^[\s*^+†‡§]+|[\s*^+†‡§]+$")


def _hst_basis(header: str) -> str | None:
    if _HST_INCLUDING.search(header):
        return "including"
    if _HST_EXCLUDING.search(header):
        return "excluding"
    return None


# BD tables pack multi-bidder columns as <p>/<br> lines inside single cells (or offset rows under a rowspan).
_CELL_BLOCK_END = re.compile(r"</(p|div|li|tr|td)>", re.IGNORECASE)
_CELL_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _cell_lines(cell) -> list[str]:
    """Split a table cell into lines at block/<br> boundaries.

    Avoids lxml text_content() fusing BD <p> entries into one string.
    """
    markup = _CELL_BR.sub("\n", etree.tostring(cell, encoding="unicode"))
    text = _html.fromstring(_CELL_BLOCK_END.sub("\n", markup)).text_content()
    return [
        line.strip() for line in text.replace("\xa0", " ").split("\n") if line.strip()
    ]


# Standalone BD bidder column header (excludes inline labels like "Recommended Bidder:").
_BD_BIDDER_HDR = re.compile(
    r"^(bidder|firm|proponent|company|contractor|supplier|vendor)s?[\s/]*(name|names)?\s*:?$",
    re.IGNORECASE,
)
# Price cell values: currency amounts or stated bid outcomes.
_BD_PRICEY = re.compile(
    r"\$|\d[\d,]*\.\d{2}|non.?compliant|no bid|withdrawn|informal", re.IGNORECASE
)
_BD_HST_LINE = re.compile(
    r"^\(?\s*(?:incl(?:uding)?\.?|excl(?:uding)?\.?)\s+H\.?S\.?T\.?\s*\)?$",
    re.IGNORECASE,
)
_BD_BIDDER_NOTE = re.compile(
    r"^\s*([*^+†‡§]+)?\s*Bidders?\s+(?:was|were|is|are)\b", re.IGNORECASE
)


def _bd_column_pairs(names, prices):
    """Strip marker-referenced trailing notes and pair names with prices only if counts and price shapes match."""
    names = list(names)
    while names and (note := _BD_BIDDER_NOTE.match(names[-1])):
        marker = note.group(1)
        if not marker or not any(marker in price for price in prices):
            return []
        names.pop()
    if (len(names) != len(prices) or any(_BD_BIDDER_NOTE.match(name) for name in names)
            or not all(_BD_PRICEY.search(price) for price in prices)):
        return []
    return list(zip(names, prices))


def _parse_bd_bid_table(table, reference: str, docs: list) -> list[dict]:
    """Parse a Bid Committee table (in-cell or multi-row layout), returning [] if columns misalign."""
    # Direct child rows only; .//tr descends into nested tables and duplicates inner bids.
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
        price_lines = list(columns[price_col])
        price_header = price_lines.pop(0)
        while price_lines and _BD_HST_LINE.fullmatch(price_lines[0]):
            price_header += " " + price_lines.pop(0)

        pairs = []
        if len(columns[bidder_col]) > 1:
            pairs.append((columns[bidder_col][1:], price_lines))
        for later in rows[index + 1 :]:
            cells = [_cell_lines(c) for c in later.xpath("./td|./th")]
            if not cells:
                break
            # Offset adjusts for the header's rowspan cell ("Number of Bids:") absent from later rows.
            offset = len(columns) - len(cells)
            if offset < 0 or bidder_col - offset < 0 or price_col - offset < 0:
                break
            if price_col - offset >= len(cells):
                break
            names, prices = cells[bidder_col - offset], cells[price_col - offset]
            if not names or not prices or not any(_BD_PRICEY.search(p) for p in prices):
                break
            pairs.append((names, prices))

        for names, prices in pairs:
            for name, price in _bd_column_pairs(names, prices):
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
    """Extract bid dicts (reference, document_number, bidder_name_raw, bid_price, hst_basis, price_header) from an agenda."""
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
            # Fall back to BD layout when row-major header check fails (BD tables can be a single row).
            out.extend(_parse_bd_bid_table(el, reference, docs))
            continue
        price_col = next(
            (i for i, h in enumerate(header) if i and _PRICE_HDR.search(h)), None
        )
        price_header = header[price_col] if price_col is not None else None
        for row in rows[1:]:
            cells = [_clean(c.text_content()) for c in row.xpath(".//td|.//th")]
            # Drop undeclared leading row-number cells so columns align with header.
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
                    "document_number": docs[0] if docs else None,
                    "bidder_name_raw": name,
                    "bid_price": price or None,
                    "hst_basis": _hst_basis(price_header) if price_header else None,
                    "price_header": price_header,
                }
            )
    return out


def store_bids(conn, agendas: dict, *, failures=None, log=lambda _m: None) -> int:
    """Rebuild bid_award_panel bid rows per reference; retain prior rows and report failures if a table degrades."""
    by_ref = {}
    seen = set()
    for meeting, html in agendas.items():
        seen.update(item['reference'] for item in parse_agenda(html, meeting))
        for bid in parse_bid_tables(html, meeting):
            by_ref.setdefault(bid['reference'], []).append(Bid(source='bid_award_panel', **bid))
    prior_counts = {r['reference']: r['n'] for r in conn.execute(
        "SELECT reference,COUNT(*) n FROM bid WHERE source='bid_award_panel' GROUP BY reference")}
    missing = set(prior_counts) & (seen-set(by_ref))
    for ref, rows in list(by_ref.items()):
        keys = {(r.reference, r.document_number, r.bidder_name_raw, r.bid_price, r.source) for r in rows}
        if len(keys) < prior_counts.get(ref, 0):
            missing.add(ref)
            del by_ref[ref]
    for ref in sorted(missing):
        message = 'held agenda no longer yields a complete unambiguous table; prior observations retained for review'
        log(f'{ref}: {message}')
        if failures is not None:
            failures.append(('bid_table:'+ref, message))
    with conn:
        for ref, rows in by_ref.items():
            db.rebuild_rows(conn, rows, scope="source='bid_award_panel' AND reference=?", params=(ref,))
    return sum(len(rows) for rows in by_ref.values())


_WINNER = re.compile(r"\bto\s+(.+?)\s+for\s+", re.IGNORECASE)
# Award amount matches "net of all applicable taxes" (not including-HST or net-of-HST-recoveries).
_NET_OF_TAXES = re.compile(
    r"\$([\d,]+(?:\.\d+)?)\s*net of all applicable taxes", re.IGNORECASE
)
_ITEM_SPLIT_CAP = re.compile(
    r"(B[AD]\d+\.\d+) - "
)  # Capturing group keeps the item token (e.g. 'BD106.3').


def parse_pre_ariba_awards(html: str, meeting: str | None = None) -> list[dict]:
    """Parse agenda items without a 10-digit doc number into winner/net-award dicts for legacy fallback matching."""
    text = _WS_LINES.sub(" ", _html.fromstring(html).text_content())
    parts = _ITEM_SPLIT_CAP.split(
        text
    )
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
        out.append(
            {
                "reference": f"{year}.{reftoken}" if year else None,
                "title": _clean(head.split("\n")[0]),
                "winner_raw": _clean(winner.group(1)),
                "award_value": amount,
            }
        )
    return out


_LEGAL_NOISE = re.compile(
    r"\b(limited|ltd|incorporated|inc|corporation|corp|company|co|"
    r"lp|llp|ulc|holdings|group|canada|ontario)\b",
    re.IGNORECASE,
)
_LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)


def supplier_tokens(name: str | None) -> set:
    """Return >2-char normalized tokens for legacy supplier/value fallback matching."""
    text = _LEADING_THE.sub("", (name or "").lower().replace("&", " and "))
    return {t for t in supplier_key(_LEGAL_NOISE.sub(" ", text)).split() if len(t) > 2}


def match_pre_ariba_titles(conn, agendas: dict) -> int:
    """Fill missing pre-2019 solicitation titles using the guarded supplier/value fallback matcher."""
    items = []
    for meeting, html in agendas.items():
        if meeting.split(".")[0] >= "2019":
            continue
        items.extend(parse_pre_ariba_awards(html, meeting))
    return match_on_supplier_and_value(conn, items, "council_pre_ariba")


# Industry/legal words that cannot uniquely identify a supplier.
_GENERIC_SUPPLIER = {'and', 'construction', 'services', 'service', 'contracting',
                     'general', 'group', 'canada', 'international', 'company', 'engineering'}


def _supplier_core(name):
    text = _LEADING_THE.sub('', (name or '').lower().replace('&', ' and '))
    text = re.sub(r'\bcontractors\b', 'contractor', text)
    return re.sub(r'[^a-z0-9]', '', _LEGAL_NOISE.sub(' ', text))


def _legacy_supplier_matches(left, right):
    # Match exact stripped core (handles short names like Co-X-Co) or a shared non-generic token.
    core = _supplier_core(left)
    return bool(core and core == _supplier_core(right)) or bool(
        (supplier_tokens(left) & supplier_tokens(right)) - _GENERIC_SUPPLIER)


def _awards_by_value(conn, *, titleless=False):
    by_value = {}
    sql = ("SELECT a.document_number d,a.supplier_name_raw s,a.award_amount_numeric v,sol.issue_date "
           "FROM award a JOIN solicitation sol ON sol.document_number=a.document_number "
           "WHERE a.source='odata' AND a.award_amount_numeric IS NOT NULL "
           "AND a.supplier_name_raw IS NOT NULL")
    if titleless:
        sql += " AND sol.title IS NULL"
    for row in conn.execute(sql):
        by_value.setdefault(round(row['v']), []).append((row['s'], row['d'], row['issue_date']))
    return by_value


def _legacy_documents(item, by_value):
    reference = item.get('reference') or ''
    year = int(reference[:4]) if reference[:4].isdigit() else None
    candidates = [(name, doc, issued) for name, doc, issued in by_value.get(round(item['award_value']), [])
                  if supplier_tokens(item['winner_raw']) & supplier_tokens(name)]
    # Require uniqueness before applying extra guards so ambiguous matches are never promoted.
    if len({doc for _, doc, _ in candidates}) != 1:
        return set()
    return {doc for name, doc, issued in candidates
            if _legacy_supplier_matches(item['winner_raw'], name)
            and not (year and issued and issued[:4].isdigit() and int(issued[:4]) > year)}


def match_pre_ariba_solicitations(conn, agendas: dict) -> int:
    """Rebuild council_pre_ariba links in solicitation_link by unique (winner, net award) match.

    Leaves reviewed crosswalk links untouched.
    """
    by_value = _awards_by_value(conn)
    links = {}
    for meeting, html in agendas.items():
        if meeting.split(".")[0] >= "2019":
            continue  # Legacy fallback cutoff; reviewed crosswalks handle later native-call items.
        for item in parse_pre_ariba_awards(html, meeting):
            if not item["reference"]:
                continue
            docs = _legacy_documents(item, by_value)
            if len(docs) == 1:
                links[item["reference"]] = docs.pop()
    conn.execute("DELETE FROM solicitation_link WHERE method='council_pre_ariba'")
    conn.executemany(
        "INSERT INTO solicitation_link (reference, document_number, method) VALUES (?, ?, 'council_pre_ariba') ON CONFLICT(reference) DO NOTHING",
        list(links.items()),
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM solicitation_link").fetchone()[0]


def store_composite_awards(conn, log=lambda _m: None, failures=None) -> int:
    """Extract and backfill composite awards from cached LLM extractions."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "composite", log=log, failures=failures)
    return result["awards_written"]


def store_ba_report_bids(conn, log=lambda _m: None, failures=None) -> int:
    """Extract and backfill bid rows from Bid Award Panel staff reports (ba_report)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "ba_report", log=log, failures=failures)
    return result["bids_written"]


def match_composite_titles(conn) -> int:
    """Populate NULL solicitation titles from stored composite_award rows via supplier/value matching. Idempotent."""
    items = [
        {
            "title": r["title"],
            "reference": r["reference"],
            "winner_raw": r["supplier_name_raw"],
            "award_value": r["award_value_numeric"],
        }
        for r in conn.execute(
            "SELECT title, reference, supplier_name_raw, award_value_numeric FROM composite_award "
            "WHERE award_value_numeric IS NOT NULL AND supplier_name_raw IS NOT NULL"
        )
    ]
    return match_on_supplier_and_value(conn, items, "council_composite")



# BA reports for items whose agendas have no bids; queue keys on sha256 IS NULL.
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
    """Download uncached (sha256 IS NULL) staff-report PDFs over plain HTTP and extract text with pdftotext. Resumable."""
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
            )
        if i % 25 == 0:
            log(f"    {i}/{len(rows)}")
    return stored


def _title_less_awards_by_value(conn):
    return _awards_by_value(conn, titleless=True)


def match_on_supplier_and_value(conn, items, title_source: str) -> int:
    """Populate NULL solicitation titles from unique (winner_raw, award_value) matches. Idempotent.

    Skips items with blank titles before matching so they cannot claim a document ahead of a titled item.
    """
    by_value = _title_less_awards_by_value(conn)
    filled = {}
    for item in items:
        if not (item["title"] or "").strip():
            continue
        docs = _legacy_documents(item, by_value)
        if len(docs) == 1:
            filled.setdefault(docs.pop(), item["title"])
    conn.executemany(
        "UPDATE solicitation SET title = ?, title_source = ? "
        "WHERE document_number = ? AND title IS NULL",
        [(t, title_source, d) for d, t in filled.items()],
    )
    conn.commit()
    return len(filled)
