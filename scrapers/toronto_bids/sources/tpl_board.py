"""Download, index, and store Toronto Public Library (TPL) Board PDF reports (#333).

TPL publishes Board meeting archives over plain HTTP at tpl.ca/about-the-library/board/meetings/
(current year on the root page, prior years on board-meetings-YYYY/), linking to per-meeting
pages with individual staff report PDFs hosted on cdn-web-main.bibliocms.com (site 164).

Extraction and backfill use the shared LLM pipeline in extraction.py (#205).
"""

from datetime import UTC, date, datetime
import re
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx
from lxml import html as lxml_html

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.trca_board import _download_failure, _store_pending_pdfs
from toronto_bids.store import db

_TPL_PAGE_HOSTS = frozenset({"tpl.ca", "www.tpl.ca", "torontopubliclibrary.ca", "www.torontopubliclibrary.ca"})
_TPL_PDF_PATH_MARKER = "/wp-content/uploads/sites/164/"
_YEAR_ARCHIVE_RE = re.compile(r"^/about-the-library/board/meetings/board-meetings-(19\d{2}|20\d{2})$")
_SUBCOMMITTEE_SKIP_RE = re.compile(
    r"performance[- ]review|selection[- ]committee|naming[- ]committee|strategic[- ]planning",
    re.I,
)

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))
_YMD_SLUG_RE = re.compile(rf"\b(20\d{{2}})[-_ ]({_MONTH_PATTERN})\.?[-_ ](\d{{1,2}})\b", re.I)
_MDY_TEXT_RE = re.compile(
    rf"\b({_MONTH_PATTERN})\.?[-_ ]+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*[-_ ]*(20\d{{2}})\b", re.I
)
_MD_ONLY_RE = re.compile(rf"\b({_MONTH_PATTERN})\.?[-_ ]+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)

_EXCLUDE_PDF_RE = re.compile(
    r"\b(?:full[- ]agenda|agenda|consolidated[- ]board[- ]document[- ]package|"
    r"minutes|deputations?|chair'?s[- ]report|communications|"
    r"charters?|purchasing[- ]policy|procurement[- ]policy|"
    r"delegation[- ]of[- ]authority|internal[- ]audit|audit[- ]report)\b",
    re.I,
)

_INCLUDE_PDF_RE = re.compile(
    r"\bawards?[- ]of[- ](?:construction[- ])?(?:contracts?|tenders?)\b|"
    r"\b(?:contract|tender|vendor|direct)[- ]awards?\b|"
    r"\b(?:increase|extension|amendment)[- ]to[- ](?:the[- ]value[- ]of[- ](?:the[- ])?)?"
    r"(?:(?:architect|construction|general[- ]contractor)[- ])?(?:contracts?|tenders?)\b|"
    r"\b(?:contracts?|tenders?)[- ](?:extension|increase|amendment|renewal)\b|"
    r"\b(?:extension|increase|renewal)[- ]of[- ](?:contracts?|tenders?)\b|"
    r"\b(?:sole|single)[- ]source\b|"
    r"\b\d{2,4}-\d{2}-tpl-rf(?:p|t|q|sq|vq|pq)\b|"
    r"\btpl[- ]\d{2,4}-20\d{2}\b",
    re.I,
)


def _canonical_page_url(raw_href: str, base_url: str) -> str | None:
    resolved = urljoin(base_url, raw_href.strip())
    parts = urlsplit(resolved)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in _TPL_PAGE_HOSTS:
        return None
    path = parts.path.rstrip("/")
    if not path:
        return None
    return urlunsplit(("https", "tpl.ca", path + "/", "", ""))


def _canonical_pdf_url(raw_href: str, base_url: str) -> str | None:
    resolved = urljoin(base_url, raw_href.strip())
    parts = urlsplit(resolved)
    host = (parts.hostname or "").lower()
    path = parts.path
    if parts.scheme not in ("http", "https") or not path.lower().endswith(".pdf"):
        return None
    is_bibliocms_tpl = host.endswith(".bibliocms.com") and _TPL_PDF_PATH_MARKER in path
    if not (is_bibliocms_tpl or host in _TPL_PAGE_HOSTS):
        return None
    return urlunsplit(("https", host, path, "", ""))


def _main_root(html_text: str):
    doc = lxml_html.fromstring(html_text)
    mains = doc.xpath("//main")
    return mains[0] if mains else doc


def parse_meeting_date(
    slug: str, label: str, *, fallback_year: int | None = None
) -> date | None:
    """Parse a TPL meeting date from URL slug or link label, returning None if ambiguous."""
    for text in (slug, label):
        m = _YMD_SLUG_RE.search(text)
        if m:
            try:
                return date(int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3)))
            except ValueError:
                pass
        m = _MDY_TEXT_RE.search(text)
        if m:
            try:
                return date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
            except ValueError:
                pass
    if fallback_year is not None:
        for text in (slug, label):
            m = _MD_ONLY_RE.search(text)
            if m:
                try:
                    return date(fallback_year, _MONTHS[m.group(1).lower()], int(m.group(2)))
                except ValueError:
                    pass
    return None


def parse_year_archive_urls(
    html_text: str,
    *,
    base_url: str = config.TPL_MEETINGS_URL,
    min_year: int = config.TPL_MIN_YEAR,
) -> list[dict]:
    """Return unique board-meetings-YYYY archive pages with year >= min_year."""
    root = _main_root(html_text)
    seen: dict[int, dict] = {}
    for anchor in root.xpath(".//a[@href]"):
        url = _canonical_page_url(anchor.get("href", ""), base_url)
        if not url:
            continue
        path = urlsplit(url).path.rstrip("/")
        m = _YEAR_ARCHIVE_RE.match(path)
        if not m:
            continue
        year = int(m.group(1))
        if year < min_year:
            continue
        seen.setdefault(year, {"year": year, "url": url})
    return [seen[y] for y in sorted(seen)]


def parse_meeting_urls(
    html_text: str,
    *,
    base_url: str = config.TPL_MEETINGS_URL,
    year: int | None = None,
    today: date | None = None,
) -> list[dict]:
    """Extract unique held Library Board meeting subpages from a TPL year or current index page."""
    root = _main_root(html_text)
    seen: dict[str, dict] = {}
    for anchor in root.xpath(".//a[@href]"):
        url = _canonical_page_url(anchor.get("href", ""), base_url)
        if not url:
            continue
        path = urlsplit(url).path.rstrip("/")
        if not path.startswith("/about-the-library/board/meetings/"):
            continue
        if path == "/about-the-library/board/meetings" or _YEAR_ARCHIVE_RE.match(path):
            continue
        slug = path.rsplit("/", 1)[-1]
        label = " ".join(anchor.text_content().split())
        if _SUBCOMMITTEE_SKIP_RE.search(f"{slug} {label}"):
            continue
        mtg_date = parse_meeting_date(slug, label, fallback_year=year)
        if today is not None and mtg_date is not None and mtg_date > today:
            continue
        resolved_year = mtg_date.year if mtg_date else year
        existing = seen.get(url)
        if existing is None or (not existing["label"] and label):
            seen[url] = {
                "url": url,
                "slug": slug,
                "year": resolved_year,
                "date": mtg_date.isoformat() if mtg_date else None,
                "label": label,
            }
    return sorted(seen.values(), key=lambda m: (m["date"] or "", m["url"]))


def is_procurement_pdf(label: str, url: str) -> bool:
    """Return True if a TPL Board meeting PDF link is a contract award, extension, or increase report."""
    filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    stem = re.sub(r"\.pdf$", "", filename, flags=re.I)
    normalized = re.sub(r"[_/]+", " ", f"{label} {stem}")
    normalized = " ".join(normalized.split())
    if _EXCLUDE_PDF_RE.search(normalized):
        return False
    return bool(_INCLUDE_PDF_RE.search(normalized))


def parse_meeting_pdfs(
    html_text: str,
    *,
    base_url: str = config.TPL_MEETINGS_URL,
) -> list[dict]:
    """Extract unique procurement/award report PDF links from a single TPL Board meeting page."""
    root = _main_root(html_text)
    seen: dict[str, dict] = {}
    for anchor in root.xpath(".//a[@href]"):
        url = _canonical_pdf_url(anchor.get("href", ""), base_url)
        if not url:
            continue
        label = " ".join(anchor.text_content().split())
        if not is_procurement_pdf(label, url):
            continue
        existing = seen.get(url)
        if existing is None or (not existing["label"] and label):
            seen[url] = {"url": url, "label": label}
    return list(seen.values())


def download_tpl_reports(
    conn,
    http,
    *,
    log=lambda _m: None,
    failures=None,
    min_year: int | None = None,
    today: date | None = None,
) -> int:
    """Index TPL Board procurement PDFs and download queued (sha256 IS NULL) reports.

    Historical year-index and meeting pages older than today.year - 1 are cached on disk under
    TPL_MEETINGS_DIR; the current year and previous year are always refreshed.
    """
    min_year = config.TPL_MIN_YEAR if min_year is None else min_year
    today = datetime.now(UTC).date() if today is None else today
    config.TPL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    config.TPL_MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        root_html = http.get_text(config.TPL_MEETINGS_URL)
    except httpx.HTTPError as exc:
        _download_failure("tpl", f"meetings index fetch failed: {exc}", log, failures)
        root_html = None

    if root_html is not None:
        meetings: dict[str, dict] = {}
        # Current year meetings sit directly on the root meetings page.
        for m in parse_meeting_urls(root_html, base_url=config.TPL_MEETINGS_URL, year=today.year, today=today):
            meetings[m["url"]] = m

        for archive in parse_year_archive_urls(root_html, min_year=min_year):
            year = archive["year"]
            year_cache = config.TPL_MEETINGS_DIR / f"year-{year}.html"
            if year < today.year - 1 and year_cache.is_file():
                yhtml = year_cache.read_text(encoding="utf-8", errors="replace")
            else:
                try:
                    yhtml = http.get_text(archive["url"])
                except httpx.HTTPError as exc:
                    _download_failure("tpl", f"skip year {year} ({archive['url']}): {exc}", log, failures)
                    continue
                year_cache.write_text(yhtml, encoding="utf-8")
            for m in parse_meeting_urls(yhtml, base_url=archive["url"], year=year, today=today):
                meetings.setdefault(m["url"], m)

        ordered = sorted(meetings.values(), key=lambda m: (m["date"] or "", m["url"]))
        log(f"  tpl: {len(ordered)} held Board meetings ({min_year}+)")
        indexed = 0
        for meeting in ordered:
            cache_path = config.TPL_MEETINGS_DIR / f"{meeting['slug']}.html"
            use_cache = (
                meeting["year"] is not None
                and meeting["year"] < today.year - 1
                and cache_path.is_file()
            )
            if use_cache:
                mhtml = cache_path.read_text(encoding="utf-8", errors="replace")
            else:
                try:
                    mhtml = http.get_text(meeting["url"])
                except httpx.HTTPError as exc:
                    _download_failure("tpl", f"skip meeting {meeting['url']}: {exc}", log, failures)
                    continue
                cache_path.write_text(mhtml, encoding="utf-8")
            for pdf in parse_meeting_pdfs(mhtml, base_url=meeting["url"]):
                db.upsert_row(
                    conn,
                    BackgroundPdf(url=pdf["url"], kind="agency_board"),
                    overwrite=False,
                )
                indexed += 1
        conn.commit()
        log(f"  tpl: {indexed} procurement PDF link(s) observed")

    return _store_pending_pdfs(
        conn,
        http,
        config.TPL_REPORTS_DIR,
        "%bibliocms.com/wp-content/uploads/sites/164/%",
        log,
        "tpl",
        failures=failures,
    )


def store_tpl_reports(conn, log=lambda _m: None, failures=None) -> dict:
    """Extract and backfill agency_* rows from cached LLM extractions (#205, #333)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "tpl", log=log, failures=failures)
    return {
        "solicitations": result["solicitations_written"],
        "awards": result["awards_written"],
        "bids": result["bids_written"],
    }
