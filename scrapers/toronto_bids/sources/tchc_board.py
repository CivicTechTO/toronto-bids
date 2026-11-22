"""Download, index, and store Toronto Community Housing (TCHC) BIFAC and Board PDF reports (#332).

TCHC publishes public past meeting pages over plain HTTP at torontohousing.ca, with
individual named PDFs starting in 2021 (pre-2021 Drupal pages carry no attachments).
Procurement disclosures live in:
  - Quarterly PAC (Procurement Award Committee) Awards and Revenue Contracts reports to BIFAC
    ($500K-$5M awards, change orders, and direct awards, with RFX #, bid counts, and vendors)
  - Individual BIFAC (>$5M) and Board of Directors (>$10M) Vendor/Contractor/Direct Award and
    Change Order reports.

Extraction and backfill use the shared LLM pipeline in extraction.py (#205).
"""

from datetime import UTC, datetime
import re
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx
from lxml import html as lxml_html

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.trca_board import _download_failure, _store_pending_pdfs
from toronto_bids.store import db

_TCHC_HOSTS = frozenset({"torontohousing.ca", "www.torontohousing.ca"})
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

_BIFAC_RE = re.compile(r"\bbifac\b|building[- ]investment", re.I)
_BOARD_RE = re.compile(r"board[- ](?:of[- ])?directors", re.I)
_IN_CAMERA_RE = re.compile(r"\bin[- ]camera\b|(?:-|/)camera(?:-|/|$)", re.I)

_EXCLUDE_PDF_RE = re.compile(
    r"\b(?:agenda|minutes|deputations?|action[- ]items?|charters?|"
    r"work[- ]plans?|financial[- ]results?|capital[- ]budget|"
    r"internal[- ]audit|audit[- ](?:report|update|action[- ]plan)|"
    r"procurement[- ]transformation|strategic[- ]procurement[- ]update|"
    r"public[- ](?:bifac|board)[- ]materials?|meeting[- ]materials?|"
    r"reconciliation[- ]of[- ](?:q\d[- ]20\d{2}[- ])?(?:preventive[- ]maintenance|pm\b|demand[- ]repair))\b",
    re.I,
)

_INCLUDE_PDF_RE = re.compile(
    r"\b(?:vendor|contractor|contract|direct)[- ]awards?\b|"
    r"\bawards?[- ]of[- ]contracts?\b|"
    r"\b(?:sole|single)[- ]source\b|"
    r"\bprocurement[- ]award[- ]committee\b|"
    r"\bpac\b.*\bawards?\b|\bawards?\b.*\bpac\b|"
    r"\bchange[- ]orders?\b|"
    r"\bcontract[- ](?:extension|amendment|increase)\b|"
    r"\b(?:extension|increase)[- ]to[- ]contract\b|"
    r"\brf(?:p|q|vq|pq|t|r|s|ql|eoi)[- ]?\d{4,}\b",
    re.I,
)


def _canonical_url(raw_href: str, base_url: str) -> str | None:
    """Resolve href against base_url, normalize host to torontohousing.ca, drop query/fragment."""
    resolved = urljoin(base_url, raw_href.strip())
    parts = urlsplit(resolved)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in _TCHC_HOSTS:
        return None
    path = parts.path
    if not path:
        return None
    return urlunsplit(("https", "torontohousing.ca", path, "", ""))


def parse_meeting_urls(
    html_text: str,
    *,
    base_url: str = config.TCHC_PAST_MEETINGS_URL,
    min_year: int = config.TCHC_MIN_YEAR,
) -> list[dict]:
    """Extract unique public BIFAC and Board of Directors past meeting URLs."""
    doc = lxml_html.fromstring(html_text)
    seen: dict[str, dict] = {}
    for anchor in doc.xpath("//a[@href]"):
        url = _canonical_url(anchor.get("href", ""), base_url)
        if not url:
            continue
        path = urlsplit(url).path.rstrip("/")
        if not path.startswith("/events/") or path == "/events":
            continue
        url = urlunsplit(("https", "torontohousing.ca", path, "", ""))
        label = " ".join(anchor.text_content().split())
        slug = path.rsplit("/", 1)[-1]
        combined = f"{slug} {label}"
        if _IN_CAMERA_RE.search(combined):
            continue
        if _BIFAC_RE.search(combined):
            body = "bifac"
        elif _BOARD_RE.search(combined):
            body = "board"
        else:
            continue
        year_match = _YEAR_RE.search(slug) or _YEAR_RE.search(label)
        year = int(year_match.group(1)) if year_match else None
        if year is not None and year < min_year:
            continue
        existing = seen.get(url)
        if existing is None or (not existing["label"] and label):
            seen[url] = {
                "url": url,
                "slug": slug,
                "body": body,
                "year": year,
                "label": label,
            }
    return sorted(seen.values(), key=lambda m: (m["year"] or 9999, m["url"]))


def is_procurement_pdf(label: str, url: str) -> bool:
    """Return True if a TCHC meeting PDF link is a procurement/award/PAC/change-order report."""
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
    base_url: str = config.TCHC_PAST_MEETINGS_URL,
) -> list[dict]:
    """Extract unique procurement/award report PDF links from a single TCHC meeting page."""
    doc = lxml_html.fromstring(html_text)
    seen: dict[str, dict] = {}
    for anchor in doc.xpath("//a[@href]"):
        url = _canonical_url(anchor.get("href", ""), base_url)
        if not url or not urlsplit(url).path.lower().endswith(".pdf"):
            continue
        label = " ".join(anchor.text_content().split())
        if not is_procurement_pdf(label, url):
            continue
        existing = seen.get(url)
        if existing is None or (not existing["label"] and label):
            seen[url] = {"url": url, "label": label}
    return list(seen.values())


def download_tchc_reports(
    conn,
    http,
    *,
    log=lambda _m: None,
    failures=None,
    min_year: int | None = None,
    current_year: int | None = None,
) -> int:
    """Index TCHC BIFAC & Board procurement PDFs and download queued (sha256 IS NULL) reports.

    Historical meeting pages older than current_year - 1 are cached on disk under
    TCHC_MEETINGS_DIR; current and previous-year meetings are always refreshed.
    """
    min_year = config.TCHC_MIN_YEAR if min_year is None else min_year
    current_year = datetime.now(UTC).year if current_year is None else current_year
    config.TCHC_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    config.TCHC_MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        index_html = http.get_text(config.TCHC_PAST_MEETINGS_URL)
    except httpx.HTTPError as exc:
        _download_failure("tchc", f"past-meetings index fetch failed: {exc}", log, failures)
        index_html = None

    if index_html is not None:
        meetings = parse_meeting_urls(index_html, min_year=min_year)
        log(f"  tchc: {len(meetings)} public BIFAC/Board meetings ({min_year}+)")
        indexed = 0
        for meeting in meetings:
            cache_path = config.TCHC_MEETINGS_DIR / f"{meeting['slug']}.html"
            use_cache = (
                meeting["year"] is not None
                and meeting["year"] < current_year - 1
                and cache_path.is_file()
            )
            if use_cache:
                mhtml = cache_path.read_text(encoding="utf-8", errors="replace")
            else:
                try:
                    mhtml = http.get_text(meeting["url"])
                except httpx.HTTPError as exc:
                    _download_failure(
                        "tchc", f"skip meeting {meeting['url']}: {exc}", log, failures
                    )
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
        log(f"  tchc: {indexed} procurement PDF link(s) observed")

    return _store_pending_pdfs(
        conn,
        http,
        config.TCHC_REPORTS_DIR,
        "%torontohousing.ca%",
        log,
        "tchc",
        failures=failures,
    )


def store_tchc_reports(conn, log=lambda _m: None, failures=None) -> dict:
    """Extract and backfill agency_* rows from cached LLM extractions (#205, #332)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "tchc", log=log, failures=failures)
    return {
        "solicitations": result["solicitations_written"],
        "awards": result["awards_written"],
        "bids": result["bids_written"],
    }
