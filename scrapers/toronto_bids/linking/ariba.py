"""Bridge Ariba postings to the spine using the link the City already gives us (#67).

`sources/ariba.py` bridges a posting by reading `externalRfxId` off the detail call and
falling back to a `Doc##########` token in the title — reconstructing a join the spine hands
us directly, and dropping roughly half of it on the way (22 of 44 open solicitations linked).
`solicitation.ariba_posting_link` carries the rfx id, so this pass just reads it.

Runs after the sources, like `build_supplier_dimension`: postings and spine rows both exist
by then, and the ~48% of detail calls that 500 (see the Ariba gotcha) cost us nothing here —
the bridge no longer depends on the detail call succeeding.
"""
import re

# The modern Discovery link: .../RfxEvent/preview/1110017742?anId=ANONYMOUS
# Genuinely dead formats: merx.com, the retired Lotus Notes callawards.nsf, literal "n/a" (19
# rows). Deliberately unhandled.
#
# discovery.ariba.com/rfx/<id> (1,380 rows) is NOT dead (#117): it redirects into the modern
# viewer, which accepts those legacy 8-digit ids directly. Still unhandled *here* because this
# pass bridges to ariba_posting, which the open-only detail API never populated for them — but
# a browser renders them fine, closed or not, so this is a gap to fill, not a dead end.
_RFX_ID = re.compile(r"/RfxEvent/preview/(\d+)")


def rfx_id_from_link(link: str | None) -> str | None:
    """The Ariba rfx id in a spine posting link, or None if it is not the modern format."""
    if not link:
        return None
    match = _RFX_ID.search(str(link))
    return match.group(1) if match else None


def bridge_postings_to_spine(conn) -> int:
    """Fill `ariba_posting.document_number` from the spine's link. Idempotent.

    Returns the number of postings newly bridged.

    Only fills NULLs. Where `sources/ariba.py` already bridged a posting the two agree —
    verified against every currently-bridged posting — so there are no conflicts to resolve,
    only gaps to fill. If they ever diverge, the spine is authoritative and this should
    become an overwrite; it is a fill today because that is all the data asks for.
    """
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
