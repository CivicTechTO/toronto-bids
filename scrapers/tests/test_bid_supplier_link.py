"""#87: bids join the supplier dimension.

A dimension built only from winners cannot answer who lost, who only ever bids unopposed,
or whether a suspended firm kept bidding.
"""
from toronto_bids.linking.supplier import build_supplier_dimension, supplier_key
from toronto_bids.models import Award, Bid, SuspendedFirm
from toronto_bids.store import db


def test_a_bidder_gets_a_supplier_id(conn):
    db.upsert_row(conn, Bid(reference="2022.BA1.1", bidder_name_raw="Maple-Crete Inc.",
                            source="bid_award_panel"), overwrite=True)
    conn.commit()
    build_supplier_dimension(conn)
    assert conn.execute("SELECT supplier_id FROM bid").fetchone()[0] is not None


def test_a_bidder_and_a_winner_resolve_to_the_same_supplier(conn):
    """The whole point: 'did this firm bid and lose?' needs both sides on one id."""
    db.upsert_row(conn, Award("3303123110", supplier_name_raw="Maple-Crete Inc.",
                              source="odata"), overwrite=True)
    db.upsert_row(conn, Bid(reference="2022.BA1.1", bidder_name_raw="Maple-Crete Inc",
                            source="bid_award_panel"), overwrite=True)
    conn.commit()
    build_supplier_dimension(conn)
    award_id = conn.execute("SELECT supplier_id FROM award").fetchone()[0]
    bid_id = conn.execute("SELECT supplier_id FROM bid").fetchone()[0]
    assert award_id == bid_id


def test_a_losing_bidder_that_never_won_still_gets_a_supplier(conn):
    """Most bidders never win, so most of the dimension's growth is firms award never saw."""
    db.upsert_row(conn, Award("3303123110", supplier_name_raw="Winner Inc.",
                              source="odata"), overwrite=True)
    db.upsert_row(conn, Bid(reference="2022.BA1.1", bidder_name_raw="Loser Ltd.",
                            source="bid_award_panel"), overwrite=True)
    conn.commit()
    assert build_supplier_dimension(conn) == 2
    loser = conn.execute("SELECT supplier_id FROM supplier WHERE supplier_key=?",
                         (supplier_key("Loser Ltd."),)).fetchone()
    assert loser is not None


def test_a_suspended_firm_that_bids_links_to_the_same_supplier(conn):
    """The question this issue exists for: did a suspended firm keep bidding?"""
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                                      council_authority="2025.GG19.17",
                                      source="suspended_firms"), overwrite=True)
    db.upsert_row(conn, Bid(reference="2022.BA1.1", bidder_name_raw="DURON ONTARIO LTD",
                            source="bid_award_panel"), overwrite=True)
    conn.commit()
    build_supplier_dimension(conn)
    firm = conn.execute("SELECT supplier_id FROM suspended_firm").fetchone()[0]
    bid = conn.execute("SELECT supplier_id FROM bid").fetchone()[0]
    assert firm == bid and firm is not None


def test_the_dimension_pass_stays_idempotent_with_bids(conn):
    db.upsert_row(conn, Bid(reference="2022.BA1.1", bidder_name_raw="Maple-Crete Inc.",
                            source="bid_award_panel"), overwrite=True)
    conn.commit()
    assert build_supplier_dimension(conn) == 1
    assert build_supplier_dimension(conn) == 1
    assert db.counts(conn)["supplier"] == 1


def test_a_stale_bid_supplier_id_is_cleared(conn):
    """The pass rebuilds from scratch; a bid whose name blanks out must lose its FK."""
    db.upsert_row(conn, Bid(reference="2022.BA1.1", bidder_name_raw="Maple-Crete Inc.",
                            source="bid_award_panel"), overwrite=True)
    conn.commit()
    build_supplier_dimension(conn)
    assert conn.execute("SELECT supplier_id FROM bid").fetchone()[0] is not None
    conn.execute("UPDATE bid SET bidder_name_raw=''")
    conn.commit()
    build_supplier_dimension(conn)
    assert conn.execute("SELECT supplier_id FROM bid").fetchone()[0] is None
