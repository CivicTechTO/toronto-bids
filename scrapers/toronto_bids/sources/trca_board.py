"""Download, index, and store TRCA eSCRIBE board/executive PDF reports."""

import hashlib
import json
from contextlib import ExitStack
import pathlib
import re
import subprocess
from urllib.parse import parse_qs, urlsplit
from html import unescape

import httpx

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.store import db

# Match <a href=...> only; eSCRIBE also serves stylesheets/logos via FileStream.ashx (<link>/<img>).
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
        # Decode HTML entities (some hrefs encode ':' as '&#58;' as well as '&amp;').
        u = _absolute(unescape(m))
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def escribe_document_urls(html: str) -> list[str]:
    """Return unique absolute FileStream and Meeting URLs linked from <a> tags."""
    return _absolute_unique(_ANCHOR_FILESTREAM.findall(html) + _MEETING.findall(html))


def escribe_asset_urls(html: str) -> list[str]:
    """Return FileStream URLs loaded as non-anchor assets (stylesheets/logos) so they can be unqueued."""
    linked = set(_absolute_unique(_ANCHOR_FILESTREAM.findall(html)))
    return [
        u for u in _absolute_unique(_ANY_FILESTREAM.findall(html)) if u not in linked
    ]


def meeting_detail_urls(calendar_json: dict) -> list[str]:
    """Return agenda'd meeting detail URLs from a GetCalendarMeetings JSON response."""
    urls = []
    for meeting in calendar_json.get("d", []):
        mid = meeting.get("ID")
        if mid and meeting.get("HasAgenda"):
            urls.append(
                f"{config.TRCA_ESCRIBE_BASE}Meeting.aspx?Id={mid}"
                f"&Agenda=Agenda&lang=English"
            )
    return urls


def _download_failure(prefix, message, log, failures):
    log(f"  {prefix} {message}")
    if failures is not None:
        failures.append((f"download:{prefix}", message))


def download_reports(conn, http, log=lambda _m: None, *, failures=None) -> int:
    """Index eSCRIBE meeting PDFs by year and download queued (sha256 IS NULL) reports. Resumable.

    Calendar/meeting HTTP errors are logged to failures without aborting the run.
    """
    config.TRCA_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
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
            _download_failure("trca", f"{year}: calendar fetch failed: {exc}", log, failures)
            continue
        detail_urls = meeting_detail_urls(calendar)
        log(f"  trca {year}: {len(detail_urls)} meetings")
        meetings = {str(m.get("ID")): m for m in calendar.get("d", [])}
        for murl in detail_urls:
            meeting = meetings[parse_qs(urlsplit(murl).query)["Id"][0]]
            # Save meeting context when MeetingPassed is False so unheld agendas stay flagged.
            review_context = (json.dumps({"calendar_url": config.TRCA_CALENDAR_URL,
                "meeting_url": murl, "meeting": meeting}, sort_keys=True)
                if meeting.get("MeetingPassed") is False else None)
            try:
                mhtml = http.get_text(murl)
            except httpx.HTTPError as exc:
                _download_failure("trca", f"skip meeting {murl}: {exc}", log, failures)
                continue
            for durl in escribe_document_urls(mhtml):
                if "ashx" in durl.lower():
                    db.upsert_row(
                        conn,
                        BackgroundPdf(url=durl, kind="agency_board",
                                      award_review_context_json=review_context),
                        overwrite=False,
                    )
            assets.update(escribe_asset_urls(mhtml))
        conn.commit()
    _prune_page_assets(conn, assets, log)
    return _store_pending_pdfs(
        conn, http, config.TRCA_REPORTS_DIR, "%escribemeetings%", log, "trca",
        failures=failures,
    )


def _prune_page_assets(conn, assets: set[str], log) -> int:
    """Delete unheld (sha256 IS NULL) background_pdf rows confirmed as page assets on a loaded meeting page."""
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


class _DeferredDownloads(RuntimeError):
    """The source requested backoff; leave the rest queued for a later run."""


def _browser_fetch():
    from toronto_bids.sources.trca_document_recovery import browser_fetch
    return browser_fetch()


def _fetch_report(http, url, prefix, stack, session, reports_dir, log):
    """Fetch a report over HTTP, falling back to an anonymous browser only on eSCRIBE verification interstitials."""
    try:
        blob = http.get_bytes(url)
    except httpx.HTTPStatusError as exc:
        if prefix == "trca" and (exc.response.status_code == 429
                or exc.response.headers.get("retry-after")):
            raise _DeferredDownloads(f"source requested backoff; remaining downloads deferred: {exc}") from exc
        if prefix != "trca" or exc.response.status_code != 403:
            raise
        blob = exc.response.content
        if b"verifying your browser" not in blob.lower():
            raise
    if prefix != "trca" or b"verifying your browser" not in blob.lower():
        return blob
    from toronto_bids.sources.trca_document_recovery import document_id, _validated
    expected_id = document_id(url)  # Exact public TRCA FileStream URLs only.
    if session.get("unavailable"):
        raise RuntimeError("ordinary browser unavailable earlier in this pass; left queued")
    if "fetch" not in session:
        try:
            session["fetch"] = stack.enter_context(_browser_fetch())
        except Exception:
            session["unavailable"] = True
            raise
    log(f"  trca: eSCRIBE verification page; ordinary browser fallback for {url}")
    response = session["fetch"](url)
    if response["status"] == 429 or response.get("retry_after"):
        raise _DeferredDownloads("browser requested backoff; remaining downloads deferred")
    if response["status"] != 200:
        raise ValueError(f"browser HTTP {response['status']}; availability cause unverified")
    if document_id(response["url"]) != expected_id:
        raise ValueError("browser response changed the document identity")
    blob = response["body"]
    _, temp = _validated(blob, reports_dir)
    temp.unlink()
    return blob


def _store_pending_pdfs(
    conn, http, reports_dir, url_like: str, log, prefix: str, *, failures=None
) -> int:
    """Download queued (sha256 IS NULL) agency_board PDFs matching url_like (shared by TRCA and Zoo).

    Logs per-URL HTTP or non-PDF errors to failures and leaves them queued rather than aborting.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    session = {}
    with ExitStack() as stack:
        for row in conn.execute(
            "SELECT id, url FROM background_pdf WHERE kind='agency_board' "
            "AND url LIKE ? AND sha256 IS NULL ORDER BY id",
            (url_like,),
        ).fetchall():
            try:
                blob = _fetch_report(http, row["url"], prefix, stack, session, reports_dir, log)
            except _DeferredDownloads as exc:
                _download_failure(prefix, f"{row['url']}: {exc}; left queued", log, failures)
                break
            except Exception as exc:  # noqa: BLE001
                _download_failure(
                    prefix, f"skip {row['url']}: {exc}; left queued (cause unverified)",
                    log, failures,
                )
                continue
            if not blob.startswith(b"%PDF"):
                # Leave non-PDF responses queued (may be transient interstitials) and record the failure.
                _download_failure(
                    prefix, f"not a PDF ({blob[:4]!r}, {len(blob)} bytes), left queued: {row['url']}",
                    log, failures,
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


def store_trca_reports(conn, log=lambda _m: None, failures=None) -> dict:
    """Extract and backfill agency_* rows from cached LLM extractions."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "trca", log=log, failures=failures)
    return {
        "solicitations": result["solicitations_written"],
        "awards": result["awards_written"],
        "bids": result["bids_written"],
    }
