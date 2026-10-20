"""The bids&tenders portal source (#135) — listing metadata, PROVISIONAL parser.

Reads public bid listings from a body's bids&tenders portal via its plain-HTTP JSON grid
endpoint (no browser). GATED: fetch_listings raises PermissionError unless the portal's
config entry is enabled, which happens only when the body's written grant is recorded in
docs/permissions/ (the PMMD/Ariba precedent). Listings only — bid documents sit behind the
Vendor clickwrap and are never fetched.

TRCA fields are verified against real grid records and public detail metadata (#226).
Zoo uses the same grid protocol; its real-record validation remains tracked in #140.
Nonempty replies are recorded automatically, including when later parsing fails.
"""
import hashlib
import json
import re
import time
from datetime import datetime, timezone

import httpx

from toronto_bids import config
from toronto_bids.buyers import seed_buyers
from toronto_bids.models import AgencySolicitation
from toronto_bids.store import db

_LANDING = "Module/Tenders/en"
_SEARCH = "Module/Tenders/en/Tender/Search/"
_NODE_RE = re.compile(r'id="NodeId"[^>]*value="([^"]+)"')
_TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
# The UI sends named filters, not integer enum values: numeric filters silently return
# success=true, total=0 (#226). Its empty "All" filter includes every published status.
_STATUS_FILTERS = ("",)
_PAGE = 50
_DELAY = 1.5


def _search_params(status: str, start: int, limit: int = _PAGE) -> dict:
    # Match the UI's deterministic DateClosing + Id sort so pages cannot overlap on ties.
    return {"status": status, "limit": limit, "start": start, "dir": "DESC",
            "from": "", "to": "", "sort": "DateClosing DESC,Id"}


def _search_payload(resp, slug: str, status: str, start: int) -> dict:
    """The search reply's JSON payload — or RAISE, naming the portal, HTTP status and status
    filter (#226). A non-2xx, a non-JSON body (e.g. the Error?aspxerrorpath page a bad request
    redirects to) or `success: false` means the portal is BROKEN, which must never read as
    "no open bids": run_portal_capture turns the raise into that slug's `FAILED: ...`. A
    payload with no `success` key at all is accepted — only an explicit false fails."""
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
    """Yield all public listings using the UI's All filter, paged and rate-limited.

    Manages its own httpx.Client (the antiforgery cookie set on the landing GET must persist
    to the search POSTs — a session concern specific to this source, not the shared HttpClient).
    Yields each raw JSON record with `buyer_slug` and `status_filter` attached. An explicit
    empty page with total=0 yields nothing. A broken or incomplete reply
    (non-2xx, non-JSON, `success: false`) RAISES instead (#226), so it can't pass for empty.
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
                time.sleep(delay)                        # rate-limit (permission condition)
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


# Portal base per slug, for building a record's detail URL. Derived from config so the two
# stay in sync.
_PORTAL_BASE = {p["slug"]: p["portal_url"].rstrip("/") for p in config.BIDS_TENDERS_PORTALS}


def _native_ref(record: dict) -> str:
    """Prefer explicit bid numbers; recognize only demonstrated title-prefix shapes.

    A GUID locates a portal page, but cannot join the agency's board-report keyspace.
    Unknown layouts are refused and their raw replies remain recorded for review.
    """
    raw = record.get("ReferenceNumber") or record.get("BidNumber")
    if not raw:
        title = record.get("Title") or ""
        if record.get("buyer_slug") == "trca":
            pattern = r"^\s*(?:(?:RF[PQT]|RFSQ|RFI|REPOST)\s+)?(\d{8}(?:[_-]\d+)?)\s+-\s+"
        else:
            pattern = r"^\s*((?:RF[PQT]|RFSQ|RFI)[ -]?\d+(?:[-_]\d+)*)\s+-\s+"
        match = re.match(pattern, title, re.I)
        raw = match[1] if match else None
    from toronto_bids.linking.agency_reference import normalize_agency_reference

    ref = normalize_agency_reference(record.get("buyer_slug"), raw)
    if not ref:
        raise ValueError(f"bids&tenders: missing/unrecognized bid reference for {record.get('Id')}")
    return ref


def _listing_date(value):
    if value is None:
        return None
    match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", str(value))
    if match:
        return datetime.fromtimestamp(int(match[1]) / 1000, timezone.utc).isoformat()
    # Existing ISO date fields stay verbatim; unfamiliar formats must not enter the store.
    try:
        datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"bids&tenders: unrecognized date {value!r}") from None
    return value


def parse_listing(record: dict, buyer_id: int) -> AgencySolicitation:
    """Map verified grid fields to an agency listing; refuse an unresolved reference."""
    base = _PORTAL_BASE.get(record.get("buyer_slug"), "")
    rid = record.get("Id")
    portal_url = f"{base}/Module/Tenders/en/Tender/Detail/{rid}" if (base and rid) else None
    return AgencySolicitation(
        buyer_id=buyer_id,
        native_ref=_native_ref(record),
        title=record.get("Title"),
        status=record.get("StatusText") or record.get("Status"),
        posted_date=_listing_date(record.get("DateAvailable") or record.get("DatePosted") or record.get("PostDate")),
        closing_date=_listing_date(record.get("DateClosing") or record.get("ClosingDate") or record.get("BidClosingDate")),
        portal_url=portal_url,
        source="bids_tenders",
    )


def store_listings(conn, records, buyer_ids: dict) -> int:
    """Upsert each parsed listing into agency_solicitation with overwrite=False (backfill):
    fills any NULL field — a portal-only solicitation gets its full record including title,
    while a board-report row keeps its title/status and only has its empty dates filled in.
    Status refresh remains deferred to #140: existing non-null fields stay authoritative.
    Validate every row before writing, so an unknown layout cannot partially write a batch.
    Skips unseeded buyers. Returns the number of listing records processed; repeated native
    references can enrich the same solicitation row."""
    rows = [parse_listing(record, buyer_ids[record["buyer_slug"]]) for record in records
            if record.get("buyer_slug") in buyer_ids]
    for row in rows:
        db.upsert_row(conn, row, overwrite=False)
    conn.commit()
    return len(rows)


def record_listings(records, out_dir) -> int:
    """Keep content-addressed raw record snapshots without overwriting prior observations."""
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
    """Fetch, record nonempty replies, and store every enabled portal, per-body isolated: one
    body's failure is caught and reported, never stops the others. Returns {slug: count | 'FAILED: ...'}."""
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
        except Exception as exc:                       # per-body isolation (empty portal is fine; a real error is caught)
            result[portal["slug"]] = f"FAILED: {exc}"
            log(f"  FAILED {portal['slug']}: {exc}")
    return result
