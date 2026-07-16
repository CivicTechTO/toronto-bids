import hashlib
import re
import subprocess
from pathlib import Path

from lxml import html as _html
from lxml.html import HtmlComment

from toronto_bids import config
from toronto_bids.models import BackgroundPdf, CouncilItem
from toronto_bids.store import db

_LEGDOCS = "/legdocs/mmis/"


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed or None


def parse_agenda_item(html: str, reference: str):
    """Parse a TMMIS agenda-item page into a CouncilItem + a deduped list of PDF links.

    Returns (CouncilItem, [{"url": str, "kind": "bgrd"|"comm"}, ...]).
    """
    root = _html.fromstring(html)

    title = root.xpath("//title/text()")
    title = _clean(title[0]) if title else None

    # Decision text: everything after the "City Council Decision" heading until the next heading.
    decision = None
    heads = root.xpath("//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6][contains(translate(text(),"
                       "'CITY COUNCIL DECISION','city council decision'),'city council decision')]")
    if heads:
        parts = []
        for sib in heads[0].itersiblings():
            if sib.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                break
            # Skip comment nodes
            if not isinstance(sib, HtmlComment):
                parts.append(sib.text_content())
        decision = _clean(" ".join(parts))

    seen = set()
    pdfs = []
    for a in root.xpath("//a[contains(@href, '%s')]" % _LEGDOCS):
        url = a.get("href")
        if not url or not url.lower().endswith(".pdf") or url in seen:
            continue
        seen.add(url)
        kind = "bgrd" if "/bgrd/" in url else ("comm" if "/comm/" in url else "other")
        pdfs.append({"url": url, "kind": kind})

    return CouncilItem(reference=reference, title=title, decision_text=decision), pdfs


def download_pdf(http, url: str, dest_dir) -> dict:
    """Download a PDF over plain HTTP, save it, hash it, and extract its text with pdftotext."""
    data = http.get_bytes(url)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1]
    path = dest_dir / name
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    proc = subprocess.run(["pdftotext", "-q", str(path), "-"], capture_output=True, text=True)
    text = proc.stdout.strip() or None
    return {"local_path": str(path), "sha256": digest, "text": text}


def fetch_agenda_item(reference: str, virtual_display: bool = False) -> str:
    """Fetch a TMMIS agenda-item page's HTML with a HEADED Chromium (Akamai blocks headless).

    On a headless server pass virtual_display=True to run Chromium under Xvfb (needs Xvfb
    installed). Not unit-tested — exercised by the live smoke.
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
                page.goto(f"{config.COUNCIL_ITEM_URL}?item={reference}",
                          wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1500)
                return page.content()
            finally:
                browser.close()
    finally:
        if display is not None:
            display.stop()


def enrich_council(conn, http, fetch=fetch_agenda_item, dest_dir=None) -> int:
    """Fetch + archive the council decision and PDFs for each suspended firm's Authority.

    `fetch(reference) -> html` is injectable so the loop is testable without a browser.
    Idempotent. Returns the number of council items processed.
    """
    dest_dir = dest_dir if dest_dir is not None else config.COUNCIL_DOCS_DIR
    refs = [r["council_authority"] for r in conn.execute(
        "SELECT DISTINCT council_authority FROM suspended_firm "
        "WHERE council_authority IS NOT NULL AND council_authority != ''"
    )]
    processed = 0
    for ref in refs:
        html = fetch(ref)
        item, pdfs = parse_agenda_item(html, ref)
        db.upsert_row(conn, item, overwrite=True)
        for pdf in pdfs:
            info = download_pdf(http, pdf["url"], dest_dir)
            db.upsert_row(conn, BackgroundPdf(
                url=pdf["url"], reference=ref, kind=pdf["kind"],
                local_path=info["local_path"], sha256=info["sha256"], text=info["text"],
            ), overwrite=True)
        conn.commit()
        processed += 1
    return processed
