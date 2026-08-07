"""TRCA board/executive report infrastructure (#135).

Download, index, and cache reports from TRCA's eSCRIBE system. Parsing is
handled by LLM extraction (#205); this module provides the infrastructure
(download, index, pdftotext, store via extract_and_backfill).
"""

import hashlib
import pathlib
import re
import subprocess
from html import unescape

import httpx

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.store import db

# A document is something the page LINKS TO, never something it LOADS (#175). eSCRIBE
# serves the meeting page's own print stylesheet (<link rel=stylesheet href>) and header
# logo (<img src>) through the same FileStream.ashx?DocumentId= handler as its PDFs, so
# matching href|src on any element indexed two page assets per meeting page — 460 across
# the 230-page corpus, against 3,422 real documents. They can never be PDFs, so the fetch
# loop dropped them at the %PDF check and left them queued, re-fetching all 460 every
# night forever while logging nothing at all. Anchor-scoping is measured against the live
# corpus: it drops exactly those 460 and adds none. `[^>]*?` because href is not always
# the first attribute (`<a class='Link' tabindex='15' href=...>`).
_ANCHOR_FILESTREAM = re.compile(
    r"""<a\b[^>]*?href=["']([^"']*[Ff]ile[Ss]tream\.ashx\?DocumentId=\d+[^"']*)""",
    re.IGNORECASE,
)
_ANY_FILESTREAM = re.compile(
    r"""(?:href|src)=["']([^"']*[Ff]ile[Ss]tream\.ashx\?DocumentId=\d+[^"']*)"""
)
_MEETING = re.compile(
    r"""<a\b[^>]*?href=["']([^"']*Meeting\.aspx\?[^"']+)""", re.IGNORECASE
)


def _absolute(url: str) -> str:
    if url.startswith("http"):
        return url
    return config.TRCA_ESCRIBE_BASE.rstrip("/") + "/" + url.lstrip("/")


def _absolute_unique(matches) -> list[str]:
    seen, out = set(), []
    for m in matches:
        # HTML-decode each href before use: some detail pages encode the colon as `&#58;`
        # (and `&` as `&amp;`), so a plain `.replace('&amp;', '&')` leaves `https&#58;//…` —
        # a malformed scheme that crashes the fetch. `unescape` handles every entity (#137).
        u = _absolute(unescape(m))
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def escribe_document_urls(html: str) -> list[str]:
    """Every linked FileStream + Meeting URL on a page, absolute, order-preserving, deduped.

    Anchors only — a stylesheet or logo served through FileStream.ashx is page furniture,
    not a document (#175). See `escribe_asset_urls` for the other side of that split.
    """
    return _absolute_unique(_ANCHOR_FILESTREAM.findall(html) + _MEETING.findall(html))


def escribe_asset_urls(html: str) -> list[str]:
    """The FileStream URLs this page LOADS rather than links to — its stylesheet and logo.

    The complement of `escribe_document_urls` over the same handler, and the reason it is
    computed rather than inferred: these are the rows #175 mis-indexed as documents, and
    naming them from the page itself is what lets `download_reports` unqueue them. A row is
    pruned only because the page it came from still shows it as an asset — never because
    it merely stopped appearing, which would drop a real document the moment a meeting fell
    out of the calendar.
    """
    linked = set(_absolute_unique(_ANCHOR_FILESTREAM.findall(html)))
    return [
        u for u in _absolute_unique(_ANY_FILESTREAM.findall(html)) if u not in linked
    ]


def meeting_detail_urls(calendar_json: dict) -> list[str]:
    """Meeting detail-page URLs from a GetCalendarMeetings JSON response (#137).

    The eSCRIBE calendar is rendered client-side from this page-method, so the meeting
    IDs live here, not in the year landing page's markup (which is why the old static
    -anchor walk found zero). Only agenda'd meetings are followed — a meeting with no
    agenda has no report PDFs to index.
    """
    urls = []
    for meeting in calendar_json.get("d", []):
        mid = meeting.get("ID")
        if mid and meeting.get("HasAgenda"):
            urls.append(
                f"{config.TRCA_ESCRIBE_BASE}Meeting.aspx?Id={mid}"
                f"&Agenda=Agenda&lang=English"
            )
    return urls


def download_reports(conn, http, log=lambda _m: None) -> int:
    """POST the calendar page-method per year -> meeting pages -> FileStream PDFs. Resumable.

    Queue keys on sha256 IS NULL (#96): the hash records that we hold the bytes; text
    records whether pdftotext could read them. Never re-download for unreadable text. A
    year whose calendar call fails, or a single meeting page that 404s, is logged and
    skipped rather than aborting the run.
    """
    config.TRCA_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    # 1. Index: discover FileStream URLs and upsert a background_pdf row per document.
    assets: set[str] = set()
    for year in config.TRCA_ESCRIBE_YEARS:
        try:
            calendar = http.post_json(
                config.TRCA_CALENDAR_URL,
                json={
                    "calendarStartDate": f"{year}-01-01",
                    "calendarEndDate": f"{year}-12-31",
                },
            )
        except httpx.HTTPError as exc:
            log(f"  trca {year}: calendar fetch failed: {exc}")
            continue
        detail_urls = meeting_detail_urls(calendar)
        log(f"  trca {year}: {len(detail_urls)} meetings")
        for murl in detail_urls:
            try:
                mhtml = http.get_text(murl)
            except httpx.HTTPError as exc:
                log(f"  trca skip meeting {murl}: {exc}")
                continue
            for durl in escribe_document_urls(mhtml):
                if "ashx" in durl.lower():
                    db.upsert_row(
                        conn,
                        BackgroundPdf(url=durl, kind="agency_board"),
                        overwrite=False,
                    )
            assets.update(escribe_asset_urls(mhtml))
        conn.commit()
    _prune_page_assets(conn, assets, log)
    # 2. Fetch: everything indexed but not yet held.
    return _store_pending_pdfs(
        conn, http, config.TRCA_REPORTS_DIR, "%escribemeetings%", log, "trca"
    )


def _prune_page_assets(conn, assets: set[str], log) -> int:
    """Unqueue rows a meeting page shows as an ASSET rather than a document (#175).

    Deleting from `background_pdf` is a deliberate exception to this archive's "rows are
    never deleted" rule, and a narrow one: these rows index a stylesheet and a logo, not a
    record, and nothing was ever held for them. The alternative — leaving them — is not
    inert, because `_store_pending_pdfs` queues on `sha256 IS NULL`, so all 460 were
    re-fetched every night and would go on being re-fetched forever.

    Scoped so it can only ever hit that mistake: a row must be unheld AND still named as an
    asset by a page we just loaded. A document that merely stopped appearing — a meeting
    dropped from the calendar, a page that 404'd mid-walk — is untouched, so no completeness
    guard on the walk is needed. Nothing here can delete bytes we hold.
    """
    if not assets:
        return 0
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id, url FROM background_pdf WHERE kind='agency_board' "
            "AND url LIKE '%escribemeetings%' AND sha256 IS NULL"
        ).fetchall()
        if r["url"] in assets
    ]
    if not ids:
        return 0
    conn.executemany("DELETE FROM background_pdf WHERE id=?", [(i,) for i in ids])
    conn.commit()
    log(f"  trca: unqueued {len(ids)} page asset(s) mis-indexed as documents (#175)")
    return len(ids)


def _store_pending_pdfs(
    conn, http, reports_dir, url_like: str, log, prefix: str
) -> int:
    """Fetch every queued (sha256 IS NULL) agency_board PDF matching url_like. Resumable.

    Shared by the TRCA and Zoo download passes — the indexing differs (eSCRIBE walk vs.
    agenda parse), the fetch loop is identical. A single dead/404 URL is logged and
    SKIPPED (the row stays queued), never aborting the run: across hundreds of legdocs /
    eSCRIBE URLs a stray 404 is routine, and get_bytes re-raises 4xx — found live when one
    dead legdocs URL killed the whole Zoo body after storing 1 of 859 reports (#135).
    Queue keys on sha256 IS NULL (#96): the hash records we hold the bytes.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for row in conn.execute(
        "SELECT id, url FROM background_pdf WHERE kind='agency_board' "
        "AND url LIKE ? AND sha256 IS NULL ORDER BY id",
        (url_like,),
    ).fetchall():
        try:
            blob = http.get_bytes(row["url"])
        except Exception as exc:  # noqa: BLE001 — 404/5xx/transport OR a malformed
            # URL (an entity that slipped decoding): one bad URL among thousands must never
            # abort the batch. Scoped to the single fetch call, so real bugs still surface.
            log(f"  {prefix} skip {row['url']}: {exc}")
            continue
        if not blob.startswith(b"%PDF"):
            # Left queued — an interstitial or a transient error page may serve the real
            # PDF next run. But SAY SO: this branch was silent, and #175 hid 460 wasted
            # fetches a night behind it, making an 11-line 404 list look like the whole
            # problem. A skip nobody can see is a skip nobody fixes.
            log(
                f"  {prefix} not a PDF ({blob[:4]!r}, {len(blob)} bytes), left queued: "
                f"{row['url']}"
            )
            continue
        sha = hashlib.sha256(blob).hexdigest()
        path = reports_dir / f"{sha}.pdf"
        path.write_bytes(blob)
        conn.execute(
            "UPDATE background_pdf SET sha256=?, local_path=?, text=? WHERE id=?",
            (sha, str(path), _pdftotext(path), row["id"]),
        )
        conn.commit()
        n += 1
        log(f"  {prefix} report {n}: {row['url']}")
    return n


def _pdftotext(path: pathlib.Path) -> str | None:
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=120
        )
        return out.stdout.decode("utf-8", errors="replace") or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def store_trca_reports(conn, buyer_id: int) -> dict:
    """Extract and backfill agency_* rows from cached LLM extractions (#205)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "trca")
    return {
        "solicitations": result["solicitations_written"],
        "awards": result["awards_written"],
        "bids": result["bids_written"],
    }
