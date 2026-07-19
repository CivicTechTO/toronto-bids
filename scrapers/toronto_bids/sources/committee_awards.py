import csv
import hashlib
import io
import pathlib
import re
import subprocess

from lxml import html as _html

from toronto_bids import config
from toronto_bids.amount import parse_bid_price
from toronto_bids.models import BackgroundPdf, Bid
from toronto_bids.store import db

COMMITTEE_REPORTS_DIR = config.DATA_DIR / "documents" / "committee_award"

_DOC = re.compile(r"(?:Ariba\s+)?Doc(?:ument)?(?:\s*Number)?\s*[:#]?\s*(\d{10})", re.IGNORECASE)

# Both report formats lay a bid row out as "<name>   <$price>" on one pdftotext -layout
# line, columns separated by the layout's wide gap (2+ spaces) -- never a single space,
# which would also fire on a name that merely contains a numeral. The price is kept
# verbatim (footnote marker and all); only the name's trailing '*' is stripped.
_BID_ROW = re.compile(r"^\s*(?P<name>\S.*?)\s{2,}(?P<price>\$[\d,]+(?:\.\d+)?\*?)\s*$")

# Either table anchor a committee award report may carry. Refuse (return []) when neither
# is present -- an emergency/sole-source/confidential-attachment report has no bid table
# to invent one from (#130 discipline).
_BID_TABLE_ANCHORS = ("Summary of Bids Received", "opened the following bids")


def award_doc_number(title):
    """The 10-digit document number an award item names, else None. Match the number, not the
    vocabulary -- titles say 'Doc<n>', 'Document Number <n>', 'Ariba Document Number <n>'."""
    if not title:
        return None
    m = _DOC.search(title)
    return m.group(1) if m else None


def award_items_from_voting_record(csv_text):
    """Distinct award items carrying a document number, from one voting-record CSV.
    Returns [{document_number, reference, committee, title}]. Dedups the per-member vote rows."""
    out = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        title = row.get("Agenda Item Title") or ""
        doc = award_doc_number(title)
        if not doc:
            continue
        out.setdefault(doc, {"document_number": doc, "reference": row.get("Agenda Item #"),
                             "committee": row.get("Committee"), "title": title})
    return list(out.values())


def parse_committee_bids(text):
    """The bidder/price table from a committee award staff report, as [(bidder, bid_price)].

    Handles both report shapes: the RFQ/RFP 'Summary of Bids Received' table and the RFT
    'opened the following bids' table. Both lay each bid out as one 'name ... $price' line
    once pdftotext -layout has run; the header/prose lines around the table carry no '$' and
    are skipped, never mistaken for a row. Returns [] when neither anchor is present --
    refuse rather than invent a table (#130).
    """
    anchor = None
    for candidate in _BID_TABLE_ANCHORS:
        idx = text.find(candidate)
        if idx != -1 and (anchor is None or idx < anchor):
            anchor = idx
    if anchor is None:
        return []

    bids = []
    started = False
    for line in text[anchor:].splitlines():
        if not line.strip():
            continue
        m = _BID_ROW.match(line)
        price = m.group("price") if m else None
        if m is None or parse_bid_price(price) is None:
            if started:
                break  # first non-matching line after the table started: table is over
            continue  # still walking through the header/caption lines before row 1
        name = m.group("name").rstrip("*").strip()
        bids.append((name, price))
        started = True
    return bids


def fetch_voting_records(http):
    """The per-term voting-record CSV bodies from CKAN (UUIDs resolved at runtime, never hardcoded)."""
    data = http.get_json(config.CKAN_BASE + "package_show",
                          params={"id": "members-of-toronto-city-council-voting-record"})
    csvs = []
    for r in data["result"]["resources"]:
        if r.get("format", "").upper() == "CSV" and r.get("name", "").startswith("member-voting-record-2"):
            csvs.append(http.get_text(r["url"]))
    return csvs


def report_url_from_item_html(html: str, document_number: str) -> str | None:
    """The award report PDF URL from a TMMIS agenda-item page (Step 1 of #164 discovery, PURE).

    The report link sits in the same block as the report's own caption text, which names
    the award via "... on Award of Doc<n> to <supplier> ...". Preferred: the backgroundfile
    link whose nearest block ancestor's text contains the document number. Falls back to the
    page's sole backgroundfile link when there's exactly one candidate and none matched by
    caption (mirrors bid_award_panel's tolerance for pages that lay the caption out
    differently) -- but refuses to guess among several unmatched candidates.
    """
    root = _html.fromstring(html)
    seen = set()
    candidates = []
    for a in root.xpath("//a[contains(@href, 'backgroundfile')]"):
        url = a.get("href")
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append((a, url))
    if not candidates:
        return None
    for a, url in candidates:
        ancestor = a.getparent()
        text = ancestor.text_content() if ancestor is not None else ""
        if document_number in text:
            return url
    if len(candidates) == 1:
        return candidates[0][1]
    return None


def discover_report_urls(items, cache_dir, *, virtual_display: bool = False,
                         log=lambda _m: None) -> dict:
    """Browser-discover each item's award report URL (Step 2 of #164, browser -- no unit test).

    One headed Chromium for the whole run, exactly bid_award_panel.agenda_fetcher's pattern:
    launching per item would be ruinous across hundreds of committee items. An item already
    cached under `cache_dir` is not refetched. Returns {reference: url} for the hits only --
    an item whose report link couldn't be resolved is simply absent, not an error.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Committee award discovery needs the optional 'council' extra. "
            "Install it with: uv sync --extra council && uv run playwright install chromium"
        ) from exc

    cache_dir = pathlib.Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    display = None
    if virtual_display:
        from pyvirtualdisplay import Display
        display = Display(visible=False, size=(1440, 900))
        display.start()

    out = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False, args=["--disable-blink-features=AutomationControlled"])
            try:
                page = browser.new_context().new_page()
                for item in items:
                    reference = item["reference"]
                    path = cache_dir / f"{reference}.html"
                    if path.exists():
                        page_html = path.read_text()
                    else:
                        page.goto(f"{config.COUNCIL_ITEM_URL}?item={reference}",
                                 wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(700)
                        page_html = page.content()
                        path.write_text(page_html)
                    url = report_url_from_item_html(page_html, item["document_number"])
                    if url:
                        out[reference] = url
                    log(f"  committee item {reference}: "
                        f"{'report found' if url else 'no report link'}")
            finally:
                browser.close()
    finally:
        if display is not None:
            display.stop()
    return out


def _pdftotext(path) -> str | None:
    try:
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                             capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", errors="replace") or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def download_committee_reports(conn, http, url_map: dict, log=lambda _m: None) -> int:
    """Fetch each committee award staff report PDF (plain HTTP) and store it.

    `url_map` is {url: document_number} — the award items already discovered from a voting
    record (award_items_from_voting_record) name both. Mirrors trca_board's resilient fetch
    loop (#135): a single dead URL is logged and skipped, never aborting the batch; a body
    that isn't actually a PDF (an HTML error page) is left unqueued rather than stored as
    garbage. Queues/re-fetches only what isn't already held (sha256 IS NULL), keyed on url.
    """
    COMMITTEE_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    have = {r["url"] for r in conn.execute(
        "SELECT url FROM background_pdf WHERE kind='committee_award' AND sha256 IS NOT NULL")}
    n = 0
    for url, document_number in url_map.items():
        if url in have:
            continue
        try:
            blob = http.get_bytes(url)
        except Exception as exc:            # noqa: BLE001 — one dead URL must never abort the batch
            log(f"  committee skip {url}: {exc}")
            continue
        if not blob.startswith(b"%PDF"):
            continue                        # HTML error page, not a report
        sha = hashlib.sha256(blob).hexdigest()
        path = COMMITTEE_REPORTS_DIR / f"{sha}.pdf"
        path.write_bytes(blob)
        db.upsert_row(conn, BackgroundPdf(
            url=url, document_number=document_number, kind="committee_award",
            local_path=str(path), sha256=sha, text=_pdftotext(path),
        ), overwrite=True)
        conn.commit()
        n += 1
        log(f"  committee report {n}: {url}")
    return n


def store_committee_bids(conn, log=lambda _m: None) -> int:
    """Parse every held committee award report's text and attach its bids by document_number.

    Mirrors #114's award-summary bid attachment: reference=None, document_number=<report's>.
    A report whose parse returns [] (no bid table -- refused, not invented) stores nothing:
    an honest zero, not a missing row. Idempotent via db.upsert_row on bid_key.
    """
    n = 0
    for row in conn.execute(
            "SELECT document_number, text FROM background_pdf "
            "WHERE kind='committee_award' AND text IS NOT NULL AND document_number IS NOT NULL"):
        bids = parse_committee_bids(row["text"])
        for bidder_name_raw, bid_price in bids:
            db.upsert_row(conn, Bid(
                bidder_name_raw=bidder_name_raw,
                reference=None,
                document_number=row["document_number"],
                bid_price=bid_price,
                source="committee_award",
            ), overwrite=True)
            n += 1
        conn.commit()
        log(f"  committee bids: {len(bids)} from doc {row['document_number']}")
    return n
