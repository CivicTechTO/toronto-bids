import json

import pytest

from toronto_bids.linking.supplier import build_supplier_dimension, supplier_key
from toronto_bids.models import Award, Bid, NonCompetitive, SuspendedFirm
from toronto_bids.store import db


# --- supplier_key ---


@pytest.mark.parametrize(
    "a,b",
    [
        ("Compugen Inc.", "Compugen Inc"),
        ("Direct Construction Company limited", "Direct Construction Company Limited"),
        ("QRX TECHNOLOGY GROUP INC", "Qrx Technology Group Inc"),
        ("Joe Pace & Sons Contracting Inc.", "JOE PACE & SONS CONTRACTING INC"),
        ("Acme  Co", "Acme Co"),
    ],
)
def test_variants_share_a_key(a, b):
    assert supplier_key(a) == supplier_key(b)
    assert supplier_key(a) != ""


def test_key_is_lowercase_alnum_spaces_only():
    assert supplier_key("QRX TECHNOLOGY GROUP INC") == "qrx technology group inc"
    assert supplier_key("Joe Pace & Sons Contracting Inc.") == "joe pace sons contracting inc"


def test_strips_submitted_by_suffix():
    assert supplier_key("SCA Office Solutions (Submitted by: Acme Reseller)") == \
        supplier_key("SCA Office Solutions")


def test_distinct_entities_stay_distinct():
    assert supplier_key("Capital Sewer Services Inc.") != supplier_key("Capital Sewer Services Ltd.")


@pytest.mark.parametrize("raw", [None, "", "   ", "()", "!!!"])
def test_blank_or_garbage_yields_empty_key(raw):
    assert supplier_key(raw) == ""


def test_numbered_company_variants_collapse_to_the_number():
    for raw in [
        "2489960 ONTARIO INC",
        "2489960 Ontario Inc. o/a Kore Infrastructure Group",
        "2489960 ONTARIO LTD",
        "2489960 ONTARIO INCORPORATED",
        "2489960 Ontario Inc. operating as Kore Infrastructure Group",
    ]:
        assert supplier_key(raw) == "#2489960", raw


def test_numbered_reverse_order_and_typo_still_key_to_the_number():
    assert supplier_key("TRISAN CONSTRUCTION O/A 614128 ONTARIO LTD.") == "#614128"
    assert supplier_key("614128 ONTARIO LTD, O/A TRISAN CONSTRUCTION") == "#614128"
    assert supplier_key("1568796 ONTARIO INC, 0/A RENOKREW") == "#1568796"


def test_a_stray_parenthetical_number_does_not_steal_the_key():
    assert supplier_key("614128 ONTARIO LTD O/A TRISAN CONSTRUCTION (3059515)") == "#614128"


def test_a_short_or_addressy_number_is_not_a_corp_number():
    assert supplier_key("123 Ontario Street Holdings Inc.") != "#123"


def test_numbered_company_with_marker_but_no_province_token_still_keys_to_the_number():
    assert supplier_key("1818620 Ontario Ltd. o/a Emission Tree") == "#1818620"
    assert supplier_key("1818620 o/a Emission Tree") == "#1818620"


def test_marker_with_short_leading_number_is_not_a_corp_number():
    assert supplier_key("123 Main o/a Corner Store") != "#123"


def test_named_trade_name_folds_to_the_legal_base():
    assert supplier_key("Corporate Express Canada Inc., operating as Staples Business Advantage") == \
        supplier_key("Corporate Express Canada Inc. (operating as Staples Advantage Canada)")
    assert supplier_key("R.O.M. Contractors Inc. o/a Ross Clair Contractors") == \
        supplier_key("R.O.M. Contractors Inc o/a Ross Clair Contractor")


def test_marker_strip_is_refused_when_the_base_is_generic():
    assert supplier_key("Ontario Ltd. o/a Trans Canada Construction") != "ontario ltd"


def test_appendix_and_noncompliant_noise_is_salvaged_not_excluded():
    assert supplier_key('Fermar Paving Limited* Non-compliant') == supplier_key("Fermar Paving Limited")
    assert supplier_key('Appendix "C" Benson Group Inc.') == supplier_key("Benson Group Inc.")
    assert supplier_key('LTH Electric Inspection & Service Ltd. Appendix "C" Price Form') == \
        supplier_key("LTH Electric Inspection & Service Ltd.")


def test_pure_footnote_is_excluded():
    for raw in [
        "Please see Scope for Award Details",
        "See Prequalified List below in the Scope of Work",
        "Various ( see the link)",
        "1 Bidder was found non-compliant with mandatory requirements.",
        "Part A: Econolite Canada Incorporated Orange Traffic Tacel Ltd. Fortran Traffic",
    ]:
        assert supplier_key(raw) == "", raw


def test_a_long_real_consortium_name_is_kept_not_excluded():
    name = (
        "Alaimo Architecture Inc., Bortolotto Design Architects Inc., "
        "Paul Didur Architect Inc., Unlimited Design Studio Inc."
    )
    assert supplier_key(name) != ""


# --- supplier dimension & bid links ---


def _seed(conn):
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="Compugen Inc.",
                              source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5749398870", supplier_name_raw="Compugen Inc",
                              source="ckan_awarded"), overwrite=True)
    db.upsert_row(conn, NonCompetitive(workspace_number="8614", supplier_name_raw="Accuworx Inc",
                                       source="odata"), overwrite=True)
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                                      council_authority="2025.GG19.17", source="suspended_firms"),
                  overwrite=True)
    conn.commit()


def test_merges_spelling_variants_into_one_supplier(conn):
    _seed(conn)
    assert build_supplier_dimension(conn) == 3
    assert db.counts(conn)["supplier"] == 3
    compugen = conn.execute(
        "SELECT variants FROM supplier WHERE supplier_key=?", (supplier_key("Compugen Inc."),)
    ).fetchone()
    assert set(json.loads(compugen["variants"])) == {"Compugen Inc.", "Compugen Inc"}


def test_backfills_supplier_id_on_all_source_tables(conn):
    _seed(conn)
    build_supplier_dimension(conn)
    key = supplier_key("Compugen Inc.")
    sid = conn.execute("SELECT supplier_id FROM supplier WHERE supplier_key=?", (key,)).fetchone()[0]
    award_ids = {r[0] for r in conn.execute("SELECT supplier_id FROM award")}
    assert sid in award_ids
    assert conn.execute("SELECT COUNT(*) FROM award WHERE supplier_id=?", (sid,)).fetchone()[0] == 2
    assert conn.execute("SELECT supplier_id FROM noncompetitive WHERE workspace_number='8614'").fetchone()[0] is not None
    assert conn.execute("SELECT supplier_id FROM suspended_firm WHERE supplier_name_raw='Duron Ontario Ltd.'").fetchone()[0] is not None


def test_supplier_dimension_is_idempotent(conn):
    _seed(conn)
    build_supplier_dimension(conn)
    build_supplier_dimension(conn)
    assert db.counts(conn)["supplier"] == 3


def test_blank_supplier_name_is_skipped(conn):
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="", source="odata"),
                  overwrite=True)
    conn.commit()
    assert build_supplier_dimension(conn) == 0
    assert db.counts(conn)["supplier"] == 0


def test_stale_supplier_row_is_pruned_when_its_key_stops_occurring(conn):
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="Compugen Inc.",
                              source="odata"), overwrite=True)
    conn.commit()
    build_supplier_dimension(conn)
    assert db.counts(conn)["supplier"] == 1
    conn.execute("UPDATE award SET supplier_name_raw='Different Firm Ltd.' "
                 "WHERE document_number='3303123110'")
    conn.commit()
    build_supplier_dimension(conn)
    keys = {r[0] for r in conn.execute("SELECT supplier_key FROM supplier")}
    assert keys == {supplier_key("Different Firm Ltd.")}
    assert db.counts(conn)["supplier"] == 1


def test_backfill_clears_stale_fk_when_name_blanks(conn):
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="Compugen Inc.",
                              source="odata"), overwrite=True)
    conn.commit()
    build_supplier_dimension(conn)
    assert conn.execute("SELECT supplier_id FROM award").fetchone()[0] is not None
    conn.execute("UPDATE award SET supplier_name_raw='' WHERE document_number='3303123110'")
    conn.commit()
    build_supplier_dimension(conn)
    assert conn.execute("SELECT supplier_id FROM award").fetchone()[0] is None


def test_a_bidder_and_a_winner_resolve_to_the_same_supplier(conn):
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
