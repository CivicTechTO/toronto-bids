from toronto_bids.linking.ariba import bridge_postings_to_spine, rfx_id_from_link
from toronto_bids.models import AribaPosting, Solicitation
from toronto_bids.store import db

_LINK = ("https://portal.us.bn.cloud.ariba.com/dashboard/public/appext/comsapsbncdiscoveryui"
         "#/RfxEvent/preview/1110017742?anId=ANONYMOUS")


def _seed(conn, *, link=_LINK, doc="5732578927", rfx="1110017742", bridged=None):
    db.upsert_row(conn, Solicitation(document_number=doc, status="Open",
                                     ariba_posting_link=link, source="odata"), overwrite=True)
    db.upsert_row(conn, AribaPosting(rfx_id=rfx, document_number=bridged,
                                     source="ariba_discovery"), overwrite=True)
    conn.commit()


def test_rfx_id_from_link():
    assert rfx_id_from_link(_LINK) == "1110017742"
    # dead formats the spine still carries for long-closed postings
    assert rfx_id_from_link("https://discovery.ariba.com/rfx/22538756") is None
    assert rfx_id_from_link("https://www.merx.com/solicitations/open-bids/Jack-Layton") is None
    assert rfx_id_from_link("n/a") is None
    assert rfx_id_from_link(None) is None


def test_bridges_unbridged_posting_via_spine_link(conn):
    _seed(conn)
    assert bridge_postings_to_spine(conn) == 1
    assert conn.execute(
        "SELECT document_number FROM ariba_posting WHERE rfx_id='1110017742'"
    ).fetchone()[0] == "5732578927"


def test_leaves_already_bridged_posting_alone(conn):
    # sources/ariba.py already bridged this one; the spine agrees, so nothing to do.
    _seed(conn, bridged="5732578927")
    assert bridge_postings_to_spine(conn) == 0
    assert conn.execute(
        "SELECT document_number FROM ariba_posting WHERE rfx_id='1110017742'"
    ).fetchone()[0] == "5732578927"


def test_dead_link_format_bridges_nothing(conn):
    _seed(conn, link="https://discovery.ariba.com/rfx/22538756", rfx="22538756")
    assert bridge_postings_to_spine(conn) == 0
    assert conn.execute(
        "SELECT document_number FROM ariba_posting WHERE rfx_id='22538756'"
    ).fetchone()[0] is None


def test_posting_with_no_matching_spine_row_stays_unbridged(conn):
    db.upsert_row(conn, AribaPosting(rfx_id="9999999999", source="ariba_discovery"),
                  overwrite=True)
    conn.commit()
    assert bridge_postings_to_spine(conn) == 0
    assert conn.execute(
        "SELECT document_number FROM ariba_posting WHERE rfx_id='9999999999'"
    ).fetchone()[0] is None


def test_is_idempotent(conn):
    _seed(conn)
    assert bridge_postings_to_spine(conn) == 1
    assert bridge_postings_to_spine(conn) == 0  # second run has nothing left to fill
