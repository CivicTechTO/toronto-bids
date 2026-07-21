import json

from toronto_bids.linking.supplier import build_supplier_dimension, supplier_key
from toronto_bids.models import Award, NonCompetitive, SuspendedFirm
from toronto_bids.store import db


def _seed(conn):
    # Two spellings of the same supplier across award + noncompetitive.
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
    n = build_supplier_dimension(conn)
    # Compugen (2 spellings) + Accuworx + Duron = 3 distinct suppliers.
    assert n == 3
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
    # both award rows (different spellings) point at the same supplier_id
    award_ids = {r[0] for r in conn.execute("SELECT supplier_id FROM award")}
    assert sid in award_ids
    assert conn.execute(
        "SELECT COUNT(*) FROM award WHERE supplier_id=?", (sid,)
    ).fetchone()[0] == 2
    # noncompetitive + suspended_firm are also linked
    assert conn.execute("SELECT supplier_id FROM noncompetitive WHERE workspace_number='8614'").fetchone()[0] is not None
    assert conn.execute("SELECT supplier_id FROM suspended_firm WHERE supplier_name_raw='Duron Ontario Ltd.'").fetchone()[0] is not None


def test_is_idempotent(conn):
    _seed(conn)
    build_supplier_dimension(conn)
    build_supplier_dimension(conn)
    assert db.counts(conn)["supplier"] == 3  # no duplicate suppliers on re-run


def test_blank_supplier_name_is_skipped(conn):
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="", source="odata"),
                  overwrite=True)
    conn.commit()
    assert build_supplier_dimension(conn) == 0
    assert db.counts(conn)["supplier"] == 0


def test_stale_supplier_row_is_pruned_when_its_key_stops_occurring(conn):
    # #171: the dimension is rebuilt from scratch, so a supplier row whose key no longer occurs
    # in ANY source (the raw name changed, or supplier_key's format changed under it — e.g. a
    # numbered company re-keying '1818620' -> '#1818620') must be DELETED, not left behind. A
    # lingering stale row collides with its own replacement and breaks the frontend's slug build.
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="Compugen Inc.",
                              source="odata"), overwrite=True)
    conn.commit()
    build_supplier_dimension(conn)
    assert db.counts(conn)["supplier"] == 1
    # The raw name changes; the old key no longer occurs in any source table.
    conn.execute("UPDATE award SET supplier_name_raw='Different Firm Ltd.' "
                 "WHERE document_number='3303123110'")
    conn.commit()
    build_supplier_dimension(conn)
    keys = {r[0] for r in conn.execute("SELECT supplier_key FROM supplier")}
    assert keys == {supplier_key("Different Firm Ltd.")}   # old 'compugen inc' pruned
    assert db.counts(conn)["supplier"] == 1


def test_numbered_reformat_prunes_the_old_bare_key(conn):
    # The concrete #171 shape: the same two raw variants of one numbered company must resolve to
    # a SINGLE supplier row keyed '#<number>' — no leftover bare-number row to collide with it.
    db.upsert_row(conn, Award(document_number="3303123110",
                              supplier_name_raw="1818620 Ontario Ltd. o/a Emission Tree",
                              source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5749398870",
                              supplier_name_raw="1818620 o/a Emission Tree",
                              source="ckan_awarded"), overwrite=True)
    conn.commit()
    build_supplier_dimension(conn)
    keys = {r[0] for r in conn.execute("SELECT supplier_key FROM supplier")}
    assert keys == {"#1818620"}


def test_backfill_clears_stale_fk_when_name_blanks(conn):
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="Compugen Inc.",
                              source="odata"), overwrite=True)
    conn.commit()
    build_supplier_dimension(conn)
    assert conn.execute("SELECT supplier_id FROM award").fetchone()[0] is not None
    conn.execute("UPDATE award SET supplier_name_raw='' WHERE document_number='3303123110'")
    conn.commit()
    build_supplier_dimension(conn)
    assert conn.execute("SELECT supplier_id FROM award").fetchone()[0] is None  # stale FK cleared
