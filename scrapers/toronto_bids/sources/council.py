import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from lxml import html as _html
from lxml.html import HtmlComment

from toronto_bids import config
from toronto_bids.models import BackgroundPdf, CouncilItem
from toronto_bids.store import db


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed or None


def parse_agenda_item(html: str, reference: str):
    """Parse a TMMIS agenda-item page into (CouncilItem, [{"url", "kind"}, ...])."""
    root = _html.fromstring(html)

    title = root.xpath("//title/text()")
    title = _clean(title[0]) if title else None

    # Decision text runs from the "City Council Decision" heading until the next heading.
    decision = None
    heads = root.xpath("//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6][contains(translate(text(),"
                       "'CITY COUNCIL DECISION','city council decision'),'city council decision')]")
    if heads:
        parts = []
        for sib in heads[0].itersiblings():
            if sib.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                break
            if not isinstance(sib, HtmlComment):
                parts.append(sib.text_content())
        decision = _clean(" ".join(parts))

    seen = set()
    pdfs = []
    for a in root.xpath("//a[contains(@href, '/legdocs/mmis/')]"):
        url = a.get("href")
        if not url or not url.lower().endswith(".pdf") or url in seen:
            continue
        seen.add(url)
        pdfs.append({"url": url, "kind": pdf_kind(url)})

    return CouncilItem(reference=reference, title=title, decision_text=decision), pdfs


def pdf_kind(url: str) -> str:
    """Classify a legdocs PDF URL as 'bgrd', 'comm', or 'other'."""
    return "bgrd" if "/bgrd/" in url else ("comm" if "/comm/" in url else "other")


def download_pdf(http, url: str, dest_dir, layout: bool = False) -> dict:
    """Download a PDF, save and hash it, and extract text with pdftotext.

    Pass layout=True (-layout) for columnar forms such as Award Summary Forms.
    """
    data = http.get_bytes(url)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1]
    path = dest_dir / name
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    proc = subprocess.run(["pdftotext", "-q", *(["-layout"] if layout else []), str(path), "-"],
                          capture_output=True, text=True)
    text = proc.stdout.strip() or None
    return {"local_path": str(path), "sha256": digest, "text": text}


def fetch_agenda_item(reference: str, virtual_display: bool = False) -> str:
    """Fetch TMMIS agenda-item HTML with headed Chromium (Akamai blocks headless); uses Xvfb if virtual_display=True."""
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
    """Fetch and archive council decisions and PDFs for each suspended_firm.council_authority. Idempotent."""
    dest_dir = dest_dir if dest_dir is not None else config.COUNCIL_DOCS_DIR
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext (poppler) is required for `tb enrich-council` but was not found on PATH. "
            "Install poppler (e.g. `brew install poppler` / `apt-get install -y poppler-utils`)."
        )
    refs = [r["council_authority"] for r in conn.execute(
        "SELECT DISTINCT council_authority FROM suspended_firm "
        "WHERE council_authority IS NOT NULL AND council_authority != ''"
    )]
    processed = 0
    for ref in refs:
        try:
            html = fetch(ref)
            item, pdfs = parse_agenda_item(html, ref)
            db.upsert_row(conn, item, overwrite=True)
            for pdf in pdfs:
                try:
                    info = download_pdf(http, pdf["url"], dest_dir)
                    db.upsert_row(conn, BackgroundPdf(
                        url=pdf["url"], reference=ref, kind=pdf["kind"],
                        local_path=info["local_path"], sha256=info["sha256"], text=info["text"],
                    ), overwrite=True)
                except Exception:
                    continue
            conn.commit()
            processed += 1
        except Exception:
            conn.rollback()
            continue
    return processed
