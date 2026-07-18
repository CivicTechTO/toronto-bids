"""The bids&tenders portal source (#135) — GATED, and currently a gate only.

Listing capture is written when the first written permission lands in docs/permissions/:
the parser needs recorded fixtures, and recording a fixture means fetching the portal,
which is exactly what the gate forbids until then. Bid DOCUMENTS are out of scope
regardless of permission state — they sit behind the Vendor clickwrap.
"""


def fetch_listings(http, portal: dict):
    if not portal.get("enabled"):
        raise PermissionError(
            f"bids&tenders portal '{portal['slug']}' is not enabled: fetching requires the "
            f"body's written permission recorded in docs/permissions/ (see #135 / #103). "
            f"Current permission record: {portal.get('permission')!r}")
    raise NotImplementedError(
        "Listing capture is unwritten by design — record fixtures under the granted "
        "permission first, then implement normalize() against them (spec 2026-07-18).")
