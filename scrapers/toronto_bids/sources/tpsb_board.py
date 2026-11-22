"""Download, slice, index, and store Toronto Police Service Board (TPSB) public minutes reports (#334).

TPSB publishes public meeting archives over plain HTTP at tpsb.ca/home/current-and-past-meetings/
and yearly archive pages back to 2015. Public Board Minutes PDFs (100-600+ pages) embed both
the confirmed Board motions and the full staff PUBLIC REPORT documents (monthly >$1M Contract
Awards / Extensions / Pre-Qualifications, Annual Non-Competitive Purchases >$25K, and Annual
Co-operative, Joint and Consolidated Procurements).

To avoid sending hundreds of non-procurement pages to the LLM and to give standalone monthly
awards/extensions their own #page=N provenance, each downloaded Minutes PDF is archived intact
under TPSB_REPORTS_DIR and deterministically sliced by `Subject:` report headers into one
background_pdf row per procurement report (plus a text-free base-URL marker row so already-
inspected Minutes PDFs are never re-downloaded).
"""

from datetime import UTC, datetime
import hashlib
import re
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx
from lxml import html as lxml_html

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.trca_board import _download_failure, _pdftotext
from toronto_bids.store import db

_TPSB_HOSTS = frozenset({"tpsb.ca", "www.tpsb.ca"})
_ARCHIVE_YEAR_RE = re.compile(r"(?:meeting-archive-|past-meetings-)(20\d{2})/?$", re.I)

# TPSB has published typos like "public_mibutes_-_june_12_2025.pdf".
_MINUTES_RE = re.compile(r"\bmi[nb]utes?\b", re.I)
_EXCLUDE_LINK_RE = re.compile(
    r"\b(?:audit[- ]risk|budget[- ]committee|in[- ]camera|confidential|"
    r"agenda|public[- ]index|notice|special[- ]confidential)\b",
    re.I,
)

# Report opening page marker inside TPSB Minutes PDFs (2015-2026).
_SUBJECT_RE = re.compile(
    r"^[ \t]*Subject:[ \t]*(.+?)(?=\n[ \t]*(?:Purpose:|Recommendation|Financial|Background|It is recommended|\s*\n))",
    re.M | re.S,
)
_MEETING_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+20\d{2}\b",
    re.I,
)

_EXCLUDE_SUBJECT_RE = re.compile(
    r"\b(?:grant\s+applications?|special\s+constable|disciplinary|service\s+complaint|"
    r"use\s+of\s+image|hate\s+crime|mental\s+health|race-?based|special\s+funds?|"
    r"donation|grievances?|freedom\s+of\s+information|write-?off|paid\s+duty|"
    r"publication\s+of\s+expenses|intimate\s+partner|training\s+&\s+development|"
    r"people\s+&\s+culture|consulting\s+services\s+expenditures?)\b",
    re.I,
)

_INCLUDE_SUBJECT_RE = re.compile(
    r"\bcontract\s+(?:award|extension|increase|amendment|renewal)\b|"
    r"\baward\s+of\s+contract\b|\bvendor\s+award\b|"
    r"\bpre-?qualification\s+of\s+(?:vendors?|general\s+contractors?|suppliers?|consultants?)\b|"
    r"\bnon-?competitive\s+purchases?\b|\b(?:sole|single)[- ]source\b|"
    r"\bco-?operative,?\s+joint\s+and\s+consolidated\s+procurements?\b|"
    r"\bcollision\s+reporting\s+centre\s+contract\b|\bpolice\s+towing\s+contract\b|"
    r"\brf[pqts]\b",
    re.I,
)


def _canonical_url(raw_href: str, base_url: str) -> str | None:
    resolved = urljoin(base_url, raw_href.strip())
    parts = urlsplit(resolved)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in _TPSB_HOSTS or not parts.path:
        return None
    return urlunsplit(("https", "tpsb.ca", parts.path, "", ""))


def parse_archive_urls(
    html_text: str,
    *,
    base_url: str = config.TPSB_MEETINGS_URL,
    min_year: int = config.TPSB_MIN_YEAR,
) -> list[dict]:
    """Extract unique yearly TPSB meeting archive URLs with year >= min_year."""
    doc = lxml_html.fromstring(html_text)
    seen: dict[int, dict] = {}
    for anchor in doc.xpath("//a[@href]"):
        url = _canonical_url(anchor.get("href", ""), base_url)
        if not url:
            continue
        m = _ARCHIVE_YEAR_RE.search(urlsplit(url).path)
        if not m:
            continue
        year = int(m.group(1))
        if year < min_year:
            continue
        seen.setdefault(year, {"year": year, "url": url.rstrip("/") + "/"})
    return [seen[y] for y in sorted(seen)]


def is_public_minutes_pdf(label: str, url: str) -> bool:
    """Return True if a TPSB PDF link is a public Board Minutes document (excluding subcommittees/agendas)."""
    filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    stem = re.sub(r"\.pdf$", "", filename, flags=re.I)
    normalized = " ".join(re.sub(r"[_/]+", " ", f"{label} {stem}").split())
    if _EXCLUDE_LINK_RE.search(normalized):
        return False
    return bool(_MINUTES_RE.search(normalized))


def parse_minutes_pdfs(
    html_text: str,
    *,
    base_url: str = config.TPSB_MEETINGS_URL,
) -> list[dict]:
    """Extract unique public Board Minutes PDF links from a TPSB current or yearly archive page."""
    doc = lxml_html.fromstring(html_text)
    seen: dict[str, dict] = {}
    for anchor in doc.xpath("//a[@href]"):
        url = _canonical_url(anchor.get("href", ""), base_url)
        if not url or not urlsplit(url).path.lower().endswith(".pdf"):
            continue
        label = " ".join(anchor.text_content().split())
        if not is_public_minutes_pdf(label, url):
            continue
        existing = seen.get(url)
        if existing is None or (not existing["label"] and label):
            seen[url] = {"url": url, "label": label}
    return list(seen.values())


def is_procurement_subject(subject: str) -> bool:
    """Return True when a TPSB PUBLIC REPORT Subject describes a procurement disclosure."""
    clean = " ".join(subject.split())
    if not clean or _EXCLUDE_SUBJECT_RE.search(clean):
        return False
    return bool(_INCLUDE_SUBJECT_RE.search(clean))


def slice_procurement_reports(full_text: str) -> list[dict]:
    """Slice procurement PUBLIC REPORT sections from a combined TPSB Minutes/Agenda pdftotext dump.

    Returns a list of dicts: {"start_page": int (1-based), "end_page": int, "subject": str, "text": str}.
    Prepends the meeting header date (when found on early pages) so extraction always has the
    Board approval date alongside each individual report.
    """
    if not full_text or not full_text.strip():
        return []
    pages = full_text.split("\x0c")
    # Remove trailing empty element after final form-feed.
    if pages and not pages[-1].strip():
        pages = pages[:-1]

    meeting_date = None
    for pg in pages[:5]:
        m = _MEETING_DATE_RE.search(pg)
        if m:
            meeting_date = " ".join(m.group(0).split())
            break
    banner = (
        f"Toronto Police Service Board — Public Minutes ({meeting_date})\n\n"
        if meeting_date
        else "Toronto Police Service Board — Public Minutes\n\n"
    )

    # Identify opening pages of embedded reports via their Subject: header.
    starts: list[tuple[int, str]] = []
    for idx, page in enumerate(pages):
        m = _SUBJECT_RE.search(page)
        if not m:
            continue
        subject = " ".join(m.group(1).split())
        if subject:
            starts.append((idx, subject))

    reports: list[dict] = []
    for pos, (start_idx, subject) in enumerate(starts):
        if not is_procurement_subject(subject):
            continue
        end_idx = starts[pos + 1][0] if pos + 1 < len(starts) else len(pages)
        # Trim trailing divider page belonging to the next item if present (very short page
        # immediately before the next report).
        while end_idx - 1 > start_idx:
            tail = pages[end_idx - 1].strip()
            if not tail or (len(tail) < 220 and re.match(r"^\d+(?:\.\d+)?\.\s+\S", tail)):
                end_idx -= 1
            else:
                break
        slice_text = banner + "\n\x0c\n".join(pages[start_idx:end_idx]).strip()
        reports.append(
            {
                "start_page": start_idx + 1,
                "end_page": end_idx,
                "subject": subject,
                "text": slice_text,
            }
        )
    return reports


def download_tpsb_reports(
    conn,
    http,
    *,
    log=lambda _m: None,
    failures=None,
    min_year: int | None = None,
    current_year: int | None = None,
) -> int:
    """Index TPSB Public Minutes PDFs, download unseen ones, and store sliced procurement reports.

    Archive HTML older than current_year - 1 is cached under TPSB_MEETINGS_DIR. Each Minutes PDF
    is archived intact under TPSB_REPORTS_DIR, recorded with a text-free base-URL marker row so
    already-inspected PDFs are skipped on subsequent runs, and sliced into one background_pdf row
    per procurement report at `<pdf_url>#page=<N>`.
    """
    min_year = config.TPSB_MIN_YEAR if min_year is None else min_year
    current_year = datetime.now(UTC).year if current_year is None else current_year
    config.TPSB_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    config.TPSB_MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        root_html = http.get_text(config.TPSB_MEETINGS_URL)
    except httpx.HTTPError as exc:
        _download_failure("tpsb", f"meetings index fetch failed: {exc}", log, failures)
        return 0

    pdf_entries: dict[str, dict] = {}
    for p in parse_minutes_pdfs(root_html, base_url=config.TPSB_MEETINGS_URL):
        pdf_entries[p["url"]] = p

    for archive in parse_archive_urls(root_html, min_year=min_year):
        year = archive["year"]
        cache_path = config.TPSB_MEETINGS_DIR / f"archive-{year}.html"
        if year < current_year - 1 and cache_path.is_file():
            ahtml = cache_path.read_text(encoding="utf-8", errors="replace")
        else:
            try:
                ahtml = http.get_text(archive["url"])
            except httpx.HTTPError as exc:
                _download_failure("tpsb", f"skip archive {year} ({archive['url']}): {exc}", log, failures)
                continue
            cache_path.write_text(ahtml, encoding="utf-8")
        for p in parse_minutes_pdfs(ahtml, base_url=archive["url"]):
            pdf_entries.setdefault(p["url"], p)

    held = {
        r["url"]
        for r in conn.execute(
            "SELECT url FROM background_pdf WHERE kind='agency_board' "
            "AND url LIKE '%tpsb.ca%' AND sha256 IS NOT NULL"
        ).fetchall()
    }
    log(f"  tpsb: {len(pdf_entries)} public minutes PDF(s) discovered ({min_year}+)")

    fetched_slices = 0
    for url in sorted(pdf_entries):
        if url in held:
            continue
        try:
            blob = http.get_bytes(url)
        except Exception as exc:  # noqa: BLE001
            _download_failure("tpsb", f"skip {url}: {exc}; left queued", log, failures)
            continue
        if not blob.startswith(b"%PDF"):
            _download_failure(
                "tpsb", f"not a PDF ({blob[:4]!r}, {len(blob)} bytes), skipped: {url}", log, failures
            )
            continue
        pdf_sha = hashlib.sha256(blob).hexdigest()
        pdf_path = config.TPSB_REPORTS_DIR / f"{pdf_sha}.pdf"
        if not pdf_path.exists():
            pdf_path.write_bytes(blob)
        full_text = _pdftotext(pdf_path) or ""
        slices = slice_procurement_reports(full_text)

        # Marker row on base URL (text=None) prevents re-downloading this Minutes PDF on reruns.
        db.upsert_row(
            conn,
            BackgroundPdf(
                url=url,
                kind="agency_board",
                local_path=str(pdf_path),
                sha256=pdf_sha,
                text=None,
            ),
            overwrite=True,
        )
        for rep in slices:
            slice_url = f"{url}#page={rep['start_page']}"
            slice_sha = hashlib.sha256(rep["text"].encode("utf-8")).hexdigest()
            db.upsert_row(
                conn,
                BackgroundPdf(
                    url=slice_url,
                    kind="agency_board",
                    local_path=str(pdf_path),
                    sha256=slice_sha,
                    text=rep["text"],
                ),
                overwrite=True,
            )
            fetched_slices += 1
        conn.commit()
        log(f"  tpsb minutes {url.rsplit('/', 1)[-1]}: {len(slices)} procurement report(s)")

    return fetched_slices


def store_tpsb_reports(conn, log=lambda _m: None, failures=None) -> dict:
    """Extract and backfill agency_* rows from cached LLM extractions (#205, #334)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "tpsb", log=log, failures=failures)
    return {
        "solicitations": result["solicitations_written"],
        "awards": result["awards_written"],
        "bids": result["bids_written"],
    }
