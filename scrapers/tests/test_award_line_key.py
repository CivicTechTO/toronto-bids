"""#73: one row per award LINE, not per (document, supplier)."""
import sqlite3

from toronto_bids.models import Award
from toronto_bids.store import db


def _amounts(conn, doc):
    return sorted(r[0] for r in conn.execute(
        "SELECT award_amount FROM award WHERE document_number=?", (doc,)))


def test_multiple_lines_for_one_supplier_all_survive(conn):
    """Cascades Recovery has 10 lines on one doc; we used to keep one."""
    for amt, date in [("9975.00", "2020-01-01"), ("20564.90", "2020-02-01"),
                      ("170000.00", "2020-03-01")]:
        db.upsert_row(conn, Award("3601120065", supplier_name_raw="Weston Forest Corporation",
                                  award_amount=amt, award_date=date, source="odata"),
                      overwrite=True)
    conn.commit()
    assert _amounts(conn, "3601120065") == ["170000.00", "20564.90", "9975.00"]


def test_re_running_the_same_lines_does_not_duplicate(conn):
    def load():
        for amt, date in [("100.00", "2020-01-01"), ("200.00", "2020-02-01")]:
            db.upsert_row(conn, Award("3601120065", supplier_name_raw="Acme Ltd",
                                      award_amount=amt, award_date=date, source="odata"),
                          overwrite=True)
        conn.commit()
    load()
    load()
    assert len(_amounts(conn, "3601120065")) == 2


def test_null_amount_does_not_duplicate_on_every_sync(conn):
    """SQLite treats NULLs as distinct in a UNIQUE index; 864 real awards have no amount,
    so a bare key would insert a fresh row for each of them on every single sync."""
    for _ in range(3):
        db.upsert_row(conn, Award("3601120065", supplier_name_raw="Acme Ltd",
                                  award_amount=None, award_date=None, source="odata"),
                      overwrite=True)
        conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
    assert conn.execute("SELECT award_amount FROM award").fetchone()[0] is None  # stays NULL


def test_same_line_from_both_feeds_stays_two_rows(conn):
    """source is still part of the key: the CKAN cross-check must not collide with OData."""
    for src in ("odata", "ckan_awarded"):
        db.upsert_row(conn, Award("3601120065", supplier_name_raw="Acme Ltd",
                                  award_amount="100.00", award_date="2020-01-01", source=src),
                      overwrite=True)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 2


def test_upsert_still_updates_a_matching_line(conn):
    db.upsert_row(conn, Award("3601120065", supplier_name_raw="Acme Ltd",
                              award_amount="100.00", award_date="2020-01-01", source="odata"),
                  overwrite=True)
    conn.commit()
    first = conn.execute("SELECT first_seen FROM award").fetchone()[0]
    db.upsert_row(conn, Award("3601120065", supplier_name_raw="Acme Ltd",
                              award_amount="100.00", award_date="2020-01-01", source="odata"),
                  overwrite=True)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
    assert conn.execute("SELECT first_seen FROM award").fetchone()[0] == first  # archive kept


def _pre73_db(tmp_path):
    """A database as it existed before #73: table-level UNIQUE, no line key."""
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE award (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_number TEXT NOT NULL, supplier_name_raw TEXT, supplier_id INTEGER,
            award_amount TEXT, award_date TEXT, source TEXT,
            first_seen TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (document_number, supplier_name_raw, source)
        );
        INSERT INTO award (document_number, supplier_name_raw, award_amount, award_date,
                           source, first_seen)
        VALUES ('3601120065', 'Weston Forest Corporation', '9975.00', '2020-01-01',
                'odata', '2020-06-01 00:00:00');
    """)
    old.commit()
    old.close()
    return path


def test_migration_drops_the_old_unique_and_keeps_first_seen(tmp_path):
    path = _pre73_db(tmp_path)
    conn = db.connect(path)
    db.init_db(conn)

    # first_seen survives — no feed can tell us when we first saw a row
    assert conn.execute("SELECT first_seen FROM award").fetchone()[0] == "2020-06-01 00:00:00"

    # and the lines the old key would have rejected now insert
    for amt, date in [("20564.90", "2020-02-01"), ("170000.00", "2020-03-01")]:
        db.upsert_row(conn, Award("3601120065", supplier_name_raw="Weston Forest Corporation",
                                  award_amount=amt, award_date=date, source="odata"),
                      overwrite=True)
    conn.commit()
    assert len(_amounts(conn, "3601120065")) == 3
    assert "_award_pre73" not in {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_migration_is_idempotent(tmp_path):
    path = _pre73_db(tmp_path)
    conn = db.connect(path)
    assert db._rebuild_award_for_line_key(
        conn, db.resources.files("toronto_bids.store").joinpath("schema.sql").read_text())
    # second call finds nothing to do
    assert not db._rebuild_award_for_line_key(
        conn, db.resources.files("toronto_bids.store").joinpath("schema.sql").read_text())
    assert conn.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
