"""Committee/Council award discovery and download (#164).

The awards too large for the CPO go to a Standing Committee or Council. The route is
voting-record CSV -> agenda-item page (headed browser) -> staff-report PDF -> bid table.
Parsing is handled by LLM extraction (#205).
"""

import csv
import hashlib
import io
import pathlib
import re
import subprocess

from lxml import html as _html

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.store import db

COMMITTEE_REPORTS_DIR = config.DATA_DIR / "documents" / "committee_award"

_DOC = re.compile(
    r"(?:Ariba\s+)?Doc(?:ument)?(?:\s*Number)?\s*[:#]?\s*(\d{10})", re.IGNORECASE
)


def award_doc_number(title):
    """The 10-digit document number an award item names, else None."""
    if not title:
        return None
    m = _DOC.search(title)
    return m.group(1) if m else None


def award_items_from_voting_record(csv_text):
    """Distinct award items carrying a document number, from one voting-record CSV."""
    out = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        title = row.get("Agenda Item Title") or ""
        doc = award_doc_number(title)
        if not doc:
            continue
        out.setdefault(
            doc,
            {
                "document_number": doc,
                "reference": row.get("Agenda Item #"),
                "committee": row.get("Committee"),
                "title": title,
            },
        )
    return list(out.values())


def fetch_voting_records(http):
    """The per-term voting-record CSV bodies from CKAN."""
    data = http.get_json(
        config.CKAN_BASE + "package_show",
        params={"id": "members-of-toronto-city-council-voting-record"},
    )
    csvs = []
    for r in data["result"]["resources"]:
        if r.get("format", "").upper() == "CSV" and r.get("name", "").startswith(
            "member-voting-record-2"
        ):
            csvs.append(http.get_text(r["url"]))
    return csvs


def report_url_from_item_html(html: str, document_number: str) -> str | None:
    """The award report PDF URL from a TMMIS agenda-item page."""
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


def discover_report_urls(
    items, cache_dir, *, virtual_display: bool = False, log=lambda _m: None
) -> dict:
    """Browser-discover each item's award report URL."""
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
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = browser.new_context().new_page()
                for item in items:
                    reference = item["reference"]
                    path = cache_dir / f"{reference}.html"
                    try:
                        if path.exists():
                            page_html = path.read_text()
                        else:
                            page.goto(
                                f"{config.COUNCIL_ITEM_URL}?item={reference}",
                                wait_until="domcontentloaded",
                                timeout=45000,
                            )
                            page.wait_for_timeout(700)
                            page_html = page.content()
                            path.write_text(page_html)
                        url = report_url_from_item_html(
                            page_html, item["document_number"]
                        )
                    except Exception as exc:  # noqa: BLE001
                        log(f"  committee item {reference}: skipped ({exc})")
                        continue
                    if url:
                        out[reference] = url
                    log(
                        f"  committee item {reference}: "
                        f"{'report found' if url else 'no report link'}"
                    )
            finally:
                browser.close()
    finally:
        if display is not None:
            display.stop()
    return out


def _pdftotext(path) -> str | None:
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            timeout=120,
            check=False,
        )
        return out.stdout.decode("utf-8", errors="replace") or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def download_committee_reports(conn, http, url_map: dict, log=lambda _m: None) -> int:
    """Fetch each committee award staff report PDF (plain HTTP) and store it."""
    COMMITTEE_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    have = {
        r["url"]
        for r in conn.execute(
            "SELECT url FROM background_pdf WHERE kind='committee_award' AND sha256 IS NOT NULL"
        )
    }
    n = 0
    for url, document_number in url_map.items():
        if url in have:
            continue
        try:
            blob = http.get_bytes(url)
        except Exception as exc:  # noqa: BLE001
            log(f"  committee skip {url}: {exc}")
            continue
        if not blob.startswith(b"%PDF"):
            continue
        sha = hashlib.sha256(blob).hexdigest()
        path = COMMITTEE_REPORTS_DIR / f"{sha}.pdf"
        path.write_bytes(blob)
        db.upsert_row(
            conn,
            BackgroundPdf(
                url=url,
                document_number=document_number,
                kind="committee_award",
                local_path=str(path),
                sha256=sha,
                text=_pdftotext(path),
            ),
            overwrite=True,
        )
        conn.commit()
        n += 1
        log(f"  committee report {n}: {url}")
    return n


def store_committee_bids(conn, log=lambda _m: None) -> int:
    """Extract and backfill bid rows from cached LLM extractions (#205)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "committee", log=log)
    return result["bids_written"]
