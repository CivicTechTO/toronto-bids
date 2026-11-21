"""Bridge Ariba postings to solicitation rows via solicitation.ariba_posting_link."""
import re

# Modern Discovery preview links only; legacy /rfx/<id> links are not in ariba_posting.
_RFX_ID = re.compile(r"/RfxEvent/preview/(\d+)")


def rfx_id_from_link(link: str | None) -> str | None:
    """Extract the Ariba rfx_id from a modern preview link, or None."""
    if not link:
        return None
    match = _RFX_ID.search(str(link))
    return match.group(1) if match else None


def bridge_postings_to_spine(conn) -> int:
    """Fill NULL ariba_posting.document_number values from solicitation.ariba_posting_link. Idempotent."""
    pairs = [
        (row["document_number"], rfx)
        for row in conn.execute(
            "SELECT document_number, ariba_posting_link FROM solicitation "
            "WHERE ariba_posting_link IS NOT NULL AND document_number IS NOT NULL"
        )
        if (rfx := rfx_id_from_link(row["ariba_posting_link"]))
    ]
    before = conn.total_changes
    conn.executemany(
        "UPDATE ariba_posting SET document_number = ? "
        "WHERE rfx_id = ? AND document_number IS NULL",
        pairs,
    )
    conn.commit()
    return conn.total_changes - before
