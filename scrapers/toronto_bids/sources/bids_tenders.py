"""Capture public bids&tenders portal listings via the JSON grid endpoint (requires enabled permission)."""
import hashlib
import json
import re
import time
from datetime import datetime, timezone

import httpx

from toronto_bids import config
from toronto_bids.buyers import seed_buyers
from toronto_bids.models import PortalListing
from toronto_bids.store import db

_LANDING = "Module/Tenders/en"
_SEARCH = "Module/Tenders/en/Tender/Search/"
_NODE_RE = re.compile(r'id="NodeId"[^>]*value="([^"]+)"')
_TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
# Empty status queries "All"; numeric status codes silently return total=0.
_STATUS_FILTERS = ("",)
_PAGE = 50
_DELAY = 1.5


def _search_params(status: str, start: int, limit: int = _PAGE) -> dict:
    # Sort by DateClosing DESC,Id so pages do not overlap on ties.
    return {"status": status, "limit": limit, "start": start, "dir": "DESC",
            "from": "", "to": "", "sort": "DateClosing DESC,Id"}


def _search_payload(resp, slug: str, status: str, start: int) -> dict:
    """Validate a search response and return its JSON payload, raising RuntimeError on HTTP, JSON, or pagination errors."""
    where = f"bids&tenders {slug}: search status={status or 'All'} start={start} got HTTP {resp.status_code}"
    if not resp.is_success:
        raise RuntimeError(where)
    try:
        payload = resp.json()
    except ValueError:
        raise RuntimeError(f"{where} with a non-JSON body") from None
    if not isinstance(payload, dict):
        raise RuntimeError(f"{where} with a non-object JSON body")
    if payload.get("success", True) is False:
        raise RuntimeError(f"{where} with success=false")
    rows, total = payload.get("data"), payload.get("total")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError(f"{where} with invalid/missing data rows")
    if type(total) is not int or total < 0:
        raise RuntimeError(f"{where} with invalid/missing total")
    if (not rows and start < total) or start + len(rows) > total:
        raise RuntimeError(f"{where} with incomplete/inconsistent page ({len(rows)} rows, total={total})")
    return payload


def fetch_listings(portal: dict, *, delay: float = _DELAY, log=lambda _m: None):
    """Yield all public listings from an enabled portal using the All filter, paged and rate-limited.

    Uses a dedicated httpx.Client so the landing-page antiforgery cookie persists to search POSTs.
    """
    if not portal.get("enabled"):
        raise PermissionError(
            f"bids&tenders portal '{portal['slug']}' is not enabled: fetching requires the "
            f"body's written permission recorded in docs/permissions/ (see #135 / #103). "
            f"Current permission record: {portal.get('permission')!r}")
    base = portal["portal_url"].rstrip("/") + "/"
    client = httpx.Client(
        headers={"User-Agent": config.USER_AGENT, "X-Requested-With": "XMLHttpRequest"},
        timeout=config.HTTP_TIMEOUT, follow_redirects=True)
    try:
        response = client.get(base + _LANDING)
        response.raise_for_status()
        land = response.text
        node_m, tok_m = _NODE_RE.search(land), _TOKEN_RE.search(land)
        if not (node_m and tok_m):
            raise RuntimeError(f"bids&tenders {portal['slug']}: no NodeId/token on landing page")
        node, token = node_m.group(1), tok_m.group(1)
        for status in _STATUS_FILTERS:
            start = 0
            seen = set()
            expected_total = None
            while True:
                time.sleep(delay)
                resp = client.post(base + _SEARCH + node,
                                   params=_search_params(status, start),
                                   data={"keywords": "", "__RequestVerificationToken": token})
                payload = _search_payload(resp, portal["slug"], status, start)
                rows, total = payload["data"], payload["total"]
                if expected_total is not None and total != expected_total:
                    raise RuntimeError(f"bids&tenders {portal['slug']}: total changed during pagination")
                expected_total = total
                for rec in rows:
                    rid = rec.get("Id")
                    if not isinstance(rid, str) or not rid or rid in seen:
                        raise RuntimeError(f"bids&tenders {portal['slug']}: missing/duplicate listing Id")
                    seen.add(rid)
                    yield {**rec, "buyer_slug": portal["slug"], "status_filter": status or "All"}
                start += len(rows)
                if start >= total:
                    log(f"  {portal['slug']}: fetched {start}/{total} listings (All)")
                    break
    finally:
        client.close()


_PORTAL_BASE = {p["slug"]: p["portal_url"].rstrip("/") for p in config.BIDS_TENDERS_PORTALS}


def _native_ref(record: dict) -> str | None:
    """Extract a normalized native reference from explicit fields or known TRCA/Zoo title prefixes, or None."""
    raw = record.get("ReferenceNumber") or record.get("BidNumber")
    if not raw:
        title = record.get("Title") or ""
        if record.get("buyer_slug") == "trca":
            pattern = r"^\s*(?:(?:RF[PQT]|RFSQ|RFI|REPOST)\s+)?(\d{8}(?:[_-]\d+)?)\s+-\s+"
            match = re.match(pattern, title, re.I)
            raw = match[1] if match else None
        elif record.get("buyer_slug") == "toronto-zoo":
            # Zoo titles use "<ref> - <description>"; keep the prefix verbatim.
            prefix, separator, description = title.partition(" - ")
            prefix = prefix.strip()
            if (separator and description.strip()
                    and re.fullmatch(r"[A-Z0-9][A-Z0-9 ._-]*", prefix, re.I)
                    and re.search(r"\d", prefix)):
                import uuid
                try:
                    uuid.UUID(prefix)
                except ValueError:
                    raw = prefix
    from toronto_bids.linking.agency_reference import normalize_agency_reference

    ref = normalize_agency_reference(record.get("buyer_slug"), raw)
    return ref


def _listing_date(value):
    if value is None:
        return None
    match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", str(value))
    if match:
        return datetime.fromtimestamp(int(match[1]) / 1000, timezone.utc).isoformat()
    try:
        datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"bids&tenders: unrecognized date {value!r}") from None
    return value


def _listing_status(record):
    """Return the named status string from StatusText or Status, rejecting numeric codes."""
    for field in ('StatusText', 'Status'):
        value = record.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or re.fullmatch(r'[+-]?\d+(?:\.\d+)?', value.strip()):
            raise ValueError(f'bids&tenders: {field} must be a named status, got {value!r}')
        if value.strip():
            return value
    return None


def parse_listing(record: dict, buyer_id: int) -> PortalListing:
    """Convert a raw bids&tenders JSON record into a PortalListing keyed by detail URL."""
    base = _PORTAL_BASE.get(record.get("buyer_slug"), "")
    rid = record.get("Id")
    portal_url = f"{base}/Module/Tenders/en/Tender/Detail/{rid}" if (base and rid) else None
    if not portal_url:
        raise ValueError('bids&tenders: missing buyer/page identity')
    return PortalListing(
        buyer_id=buyer_id,
        native_ref=_native_ref(record),
        title=record.get("Title"),
        status=_listing_status(record),
        posted_date=_listing_date(record.get("DateAvailable") or record.get("DatePosted") or record.get("PostDate")),
        closing_date=_listing_date(record.get("DateClosing") or record.get("ClosingDate") or record.get("BidClosingDate")),
        portal_url=portal_url,
        source="bids_tenders",
    )


def store_listings(conn, records, buyer_ids: dict) -> int:
    """Validate and atomically upsert a batch of bids&tenders listings, then resolve procurements."""
    from toronto_bids.linking.procurement import _transaction, resolve_procurements
    rows = [parse_listing(record, buyer_ids[record["buyer_slug"]]) for record in records
            if record.get("buyer_slug") in buyer_ids]
    with _transaction(conn):
        for row in rows:
            db.upsert_row(conn, row, overwrite=True)
        resolve_procurements(conn)
    conn.commit()
    return len(rows)


def record_listings(records, out_dir) -> int:
    """Write content-addressed raw record snapshots without overwriting existing files."""
    import pathlib
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for record in records:
        slug = record.get("buyer_slug", "unknown")
        status = record.get("status_filter", record.get("status_code", "x"))
        encoded = json.dumps(record, indent=1, default=str, sort_keys=True)
        digest = hashlib.sha256(encoded.encode()).hexdigest()[:16]
        path = out / f"{slug}-{status}-{digest}.json"
        if not path.exists():
            path.write_text(encoded)
        n += 1
    return n


def run_portal_capture(conn, *, record: bool = False, only=None, log=lambda _m: None) -> dict:
    """Fetch, record, and store all enabled bids&tenders portals with per-body error isolation."""
    buyer_ids = seed_buyers(conn)
    result = {}
    for portal in config.BIDS_TENDERS_PORTALS:
        if not portal["enabled"]:
            continue
        if only and portal["slug"] not in only:
            continue
        try:
            records = []
            try:
                for item in fetch_listings(portal, log=log):
                    records.append(item)
            finally:
                if records or record:
                    written = record_listings(records, config.PORTAL_RECORDINGS_DIR)
                    log(f"  {portal['slug']}: recorded {written} raw record(s)")
            result[portal["slug"]] = store_listings(conn, records, buyer_ids)
            log(f"  {portal['slug']}: {result[portal['slug']]} listing(s) stored")
        except Exception as exc:
            result[portal["slug"]] = f"FAILED: {exc}"
            log(f"  FAILED {portal['slug']}: {exc}")
    return result
