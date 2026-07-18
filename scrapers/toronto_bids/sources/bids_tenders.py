"""The bids&tenders portal source (#135) — listing metadata, PROVISIONAL parser.

Reads public bid listings from a body's bids&tenders portal via its plain-HTTP JSON grid
endpoint (no browser). GATED: fetch_listings raises PermissionError unless the portal's
config entry is enabled, which happens only when the body's written grant is recorded in
docs/permissions/ (the PMMD/Ariba precedent). Listings only — bid documents sit behind the
Vendor clickwrap and are never fetched.

PROVISIONAL: as of 2026-07-18 both permitted portals (TRCA, Zoo) are empty, so parse_listing
is mapped against the field names documented in the portal's own grid JS but has NOT been
validated against a real record. When a bid first appears, `tb enrich-agencies --portal
--record` captures real JSON to fixtures and the parser is completed and re-validated.
"""
import re
import time

import httpx

from toronto_bids import config
from toronto_bids.models import AgencySolicitation
from toronto_bids.store import db

_LANDING = "Module/Tenders/en"
_SEARCH = "Module/Tenders/en/Tender/Search/"
_NODE_RE = re.compile(r'id="NodeId"[^>]*value="([^"]+)"')
_TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
# The status codes the endpoint accepts (Open / Awarded / Cancelled / ...). The exact
# code->label mapping is recorded when data first appears; the sweep covers them all.
_STATUS_CODES = range(0, 6)
_PAGE = 50
_DELAY = 1.5


def _search_params(status: int, start: int, limit: int = _PAGE) -> dict:
    # NEVER include 'sort' — 'sort=ClosingDate desc,Id' (space/comma) triggers a server error
    # that redirects to Error?aspxerrorpath (verified live 2026-07-18). Default order is fine.
    return {"status": status, "limit": limit, "start": start, "dir": "desc", "from": "", "to": ""}


def fetch_listings(portal: dict, *, delay: float = _DELAY, log=lambda _m: None):
    """Yield every listing record for a portal, across all statuses, paged and rate-limited.

    Manages its own httpx.Client (the antiforgery cookie set on the landing GET must persist
    to the search POSTs — a session concern specific to this source, not the shared HttpClient).
    Yields each raw JSON record with `buyer_slug` and `status_code` attached. On an empty portal
    (total=0, the current reality) this yields nothing — a clean no-op.
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
        land = client.get(base + _LANDING).text
        node_m, tok_m = _NODE_RE.search(land), _TOKEN_RE.search(land)
        if not (node_m and tok_m):
            raise RuntimeError(f"bids&tenders {portal['slug']}: no NodeId/token on landing page")
        node, token = node_m.group(1), tok_m.group(1)   # FIRST token (bidDetail-scoped one 302s)
        for status in _STATUS_CODES:
            start = 0
            while True:
                time.sleep(delay)                        # rate-limit (permission condition)
                resp = client.post(base + _SEARCH + node,
                                   params=_search_params(status, start),
                                   data={"keywords": "", "__RequestVerificationToken": token})
                try:
                    payload = resp.json()
                except ValueError:
                    log(f"  {portal['slug']} status={status} start={start}: non-JSON, skipping")
                    break
                rows = payload.get("data") or []
                total = payload.get("total") or 0
                for rec in rows:
                    yield {**rec, "buyer_slug": portal["slug"], "status_code": status}
                start += len(rows)
                if not rows or start >= total:
                    break
    finally:
        client.close()
