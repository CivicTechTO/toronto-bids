import sqlite3

import pytest

from toronto_bids.models import (
    AribaPosting,
    Award,
    BackgroundPdf,
    Bid,
    BidResponse,
    CouncilItem,
    NonCompetitive,
    Solicitation,
    Supplier,
    SuspendedFirm,
)
from toronto_bids.store import db


def test_init_creates_tables(conn):
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "solicitation",
        "award",
        "noncompetitive",
        "ariba_posting",
        "sync_run",
    } <= names


def test_counts_covers_all_export_tables(conn):
    assert set(db.counts(conn)) == set(db.EXPORT_TABLES)


@pytest.mark.parametrize(
    ("row", "table"),
    [
        (Solicitation(document_number="3303123110", status="Awarded", source="odata"), "solicitation"),
        (Award("3303123110", supplier_name_raw="Computer Media Group", award_amount="26773.58", source="odata"), "award"),
        (NonCompetitive("8614", supplier_name_raw="Accuworx Inc", reason="Emergency", source="odata"), "noncompetitive"),
        (AribaPosting(rfx_id="1110015885", document_number="5672751291", title="RFT Watermain", raw_json="{}", source="ariba_discovery"), "ariba_posting"),
        (SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended", start_date="March 27, 2025", council_authority="2025.GG19.17", source="suspended_firms"), "suspended_firm"),
        (Supplier(supplier_key="compugen inc", display_name="Compugen Inc.", variants='["Compugen Inc."]'), "supplier"),
        (CouncilItem(reference="2025.GG26.3", title="Suspension of X", decision_text="Adopted."), "council_item"),
        (BackgroundPdf(url="https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf", reference="2025.GG26.3", kind="bgrd", sha256="abc", text="REPORT FOR ACTION"), "background_pdf"),
    ],
)
def test_upsert_row_is_idempotent(conn, row, table):
    db.upsert_row(conn, row, overwrite=True)
    db.upsert_row(conn, row, overwrite=True)
    assert db.counts(conn)[table] == 1


def test_upsert_row_rejects_unknown_model_type(conn):
    with pytest.raises(TypeError):
        db.upsert_row(conn, object(), overwrite=True)


def test_overwrite_true_lets_new_nonnull_win(conn):
    db.upsert_row(
        conn, Solicitation("3303123110", title=None, source="ckan"), overwrite=False
    )
    db.upsert_row(
        conn,
        Solicitation("3303123110", title="Toner Cartridges", source="odata"),
        overwrite=True,
    )
    row = conn.execute(
        "SELECT title FROM solicitation WHERE document_number='3303123110'"
    ).fetchone()
    assert row["title"] == "Toner Cartridges"


def test_overwrite_false_only_fills_nulls(conn):
    db.upsert_row(
        conn,
        Solicitation("3303123110", division="Purchasing", source="odata"),
        overwrite=True,
    )
    db.upsert_row(
        conn,
        Solicitation("3303123110", division="SOMETHING ELSE", source="ckan"),
        overwrite=False,
    )
    row = conn.execute(
        "SELECT division FROM solicitation WHERE document_number='3303123110'"
    ).fetchone()
    assert row["division"] == "Purchasing"


def test_overwrite_false_backfills_a_null(conn):
    db.upsert_row(
        conn, Solicitation("3303123110", division=None, source="odata"), overwrite=True
    )
    db.upsert_row(
        conn,
        Solicitation("3303123110", division="Toronto Water", source="ckan"),
        overwrite=False,
    )
    row = conn.execute(
        "SELECT division FROM solicitation WHERE document_number='3303123110'"
    ).fetchone()
    assert row["division"] == "Toronto Water"


def test_sync_run_lifecycle(conn):
    run_id = db.start_sync_run(conn, "odata")
    db.finish_sync_run(conn, run_id, status="ok", rows_fetched=10, rows_upserted=10)
    row = conn.execute(
        "SELECT status, rows_fetched FROM sync_run WHERE id=?", (run_id,)
    ).fetchone()
    assert row["status"] == "ok" and row["rows_fetched"] == 10


def test_ariba_posting_later_500_does_not_wipe_snapshot(conn):
    db.upsert_row(
        conn,
        AribaPosting(
            rfx_id="1110015885",
            document_number="5672751291",
            raw_json='{"x":1}',
            source="ariba_discovery",
        ),
        overwrite=True,
    )
    db.upsert_row(
        conn,
        AribaPosting(
            rfx_id="1110015885",
            document_number=None,
            raw_json=None,
            source="ariba_discovery",
        ),
        overwrite=True,
    )
    row = conn.execute(
        "SELECT document_number, raw_json FROM ariba_posting WHERE rfx_id='1110015885'"
    ).fetchone()
    assert row["document_number"] == "5672751291"
    assert row["raw_json"] == '{"x":1}'


def test_upsert_suspended_firm_distinct_authority_is_new_row(conn):
    db.upsert_row(
        conn,
        SuspendedFirm(
            supplier_name_raw="Acme", council_authority="A1", source="suspended_firms"
        ),
        overwrite=True,
    )
    db.upsert_row(
        conn,
        SuspendedFirm(
            supplier_name_raw="Acme", council_authority="A2", source="suspended_firms"
        ),
        overwrite=True,
    )
    assert db.counts(conn)["suspended_firm"] == 2


def test_suspended_firm_overwrite_updates_status(conn):
    db.upsert_row(
        conn,
        SuspendedFirm(
            supplier_name_raw="Duron Ontario Ltd.",
            status="Suspended",
            council_authority="2025.GG19.17",
            source="suspended_firms",
        ),
        overwrite=True,
    )
    db.upsert_row(
        conn,
        SuspendedFirm(
            supplier_name_raw="Duron Ontario Ltd.",
            status="Reinstated",
            council_authority="2025.GG19.17",
            source="suspended_firms",
        ),
        overwrite=True,
    )
    row = conn.execute(
        "SELECT status FROM suspended_firm WHERE supplier_name_raw='Duron Ontario Ltd.'"
    ).fetchone()
    assert row["status"] == "Reinstated"


def test_suspended_firm_null_authority_is_idempotent(conn):
    firm = SuspendedFirm(
        supplier_name_raw="No Authority Co",
        council_authority=None,
        source="suspended_firms",
    )
    db.upsert_row(conn, firm, overwrite=True)
    db.upsert_row(conn, firm, overwrite=True)
    assert db.counts(conn)["suspended_firm"] == 1
    row = conn.execute(
        "SELECT council_authority FROM suspended_firm WHERE supplier_name_raw='No Authority Co'"
    ).fetchone()
    assert row["council_authority"] == ""


def test_upsert_supplier_updates_variants(conn):
    db.upsert_row(
        conn,
        Supplier(
            supplier_key="compugen inc",
            display_name="Compugen Inc",
            variants='["Compugen Inc"]',
        ),
        overwrite=True,
    )
    db.upsert_row(
        conn,
        Supplier(
            supplier_key="compugen inc",
            display_name="Compugen Inc.",
            variants='["Compugen Inc", "Compugen Inc."]',
        ),
        overwrite=True,
    )
    row = conn.execute(
        "SELECT variants FROM supplier WHERE supplier_key='compugen inc'"
    ).fetchone()
    assert row["variants"] == '["Compugen Inc", "Compugen Inc."]'


def test_init_db_adds_missing_column_to_pre_p5a_table():
    c = db.connect(":memory:")
    c.execute(
        "CREATE TABLE suspended_firm ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " supplier_name_raw TEXT NOT NULL,"
        " council_authority TEXT,"
        " source TEXT,"
        " UNIQUE (supplier_name_raw, council_authority))"
    )
    c.commit()

    db.init_db(c)

    cols = {r[1] for r in c.execute("PRAGMA table_info(suspended_firm)")}
    assert "supplier_id" in cols
    c.execute(
        "INSERT INTO suspended_firm (supplier_name_raw, council_authority, source)"
        " VALUES ('Acme', 'A1', 'x')"
    )
    c.execute(
        "UPDATE suspended_firm SET supplier_id = 7 WHERE supplier_name_raw = 'Acme'"
    )
    row = c.execute(
        "SELECT supplier_id FROM suspended_firm WHERE supplier_name_raw = 'Acme'"
    ).fetchone()
    assert row["supplier_id"] == 7
    c.close()


def test_sync_runs_since_returns_only_newer_rows(conn):
    r1 = db.start_sync_run(conn, "odata_solicitations")
    db.finish_sync_run(conn, r1, status="ok", rows_fetched=7446, rows_upserted=12)
    cutoff = r1
    r2 = db.start_sync_run(conn, "ariba_discovery")
    db.finish_sync_run(conn, r2, status="ok", rows_fetched=1670, rows_upserted=0)
    r3 = db.start_sync_run(conn, "ckan_awarded")
    db.finish_sync_run(
        conn, r3, status="failed", rows_fetched=0, rows_upserted=0, error="boom"
    )

    rows = db.sync_runs_since(conn, cutoff)
    assert [r["source"] for r in rows] == ["ariba_discovery", "ckan_awarded"]
    assert rows[0]["rows_fetched"] == 1670 and rows[0]["rows_upserted"] == 0
    assert rows[1]["status"] == "failed" and rows[1]["error"] == "boom"


def test_solicitation_link_table_exists_and_is_counted(conn):
    conn.execute(
        "INSERT INTO solicitation_link (reference, document_number, method) "
        "VALUES ('2016.BD106.3', '5672751291', 'council_pre_ariba')"
    )
    conn.commit()
    assert db.counts(conn)["solicitation_link"] == 1


# --- rebuild_rows ---


def test_rebuild_rows_scoped_delete_preserves_identity_and_coalesces_repeats(conn):
    db.upsert_row(
        conn,
        Bid(reference="2020.BA1.1", document_number="1001", bidder_name_raw="Acme",
            bid_price="100", hst_basis="excluding", price_header="Old Header",
            status="Awarded", source="bid_award_panel"),
        overwrite=True,
    )
    db.upsert_row(
        conn,
        Bid(reference="2020.BA1.1", document_number="1002", bidder_name_raw="To Delete",
            bid_price="200", source="bid_award_panel"),
        overwrite=True,
    )
    db.upsert_row(
        conn,
        Bid(reference="2020.BA2.2", document_number="2001", bidder_name_raw="Out Of Scope",
            bid_price="300", source="other_source"),
        overwrite=True,
    )
    conn.execute("UPDATE bid SET first_seen = '2020-01-01 00:00:00' WHERE bidder_name_raw = 'Acme'")
    conn.commit()
    old_id = conn.execute("SELECT id FROM bid WHERE bidder_name_raw = 'Acme'").fetchone()[0]

    deleted = db.rebuild_rows(
        conn,
        [
            Bid(reference="2020.BA1.1", document_number="1001", bidder_name_raw="Acme",
                bid_price="100", hst_basis=None, price_header="New Header",
                status=None, source="bid_award_panel"),
            Bid(reference="2020.BA1.1", document_number="1001", bidder_name_raw="Acme",
                bid_price="100", hst_basis=None, price_header=None,
                status="Compliant", source="bid_award_panel"),
        ],
        scope="source = ?",
        params=("bid_award_panel",),
    )
    conn.commit()

    assert deleted == 1
    kept = conn.execute(
        "SELECT id, first_seen, hst_basis, price_header, status FROM bid WHERE bidder_name_raw = 'Acme'"
    ).fetchone()
    assert kept["id"] == old_id
    assert kept["first_seen"] == "2020-01-01 00:00:00"
    assert kept["hst_basis"] is None
    assert kept["price_header"] == "New Header"
    assert kept["status"] == "Compliant"
    assert conn.execute("SELECT 1 FROM bid WHERE bidder_name_raw = 'To Delete'").fetchone() is None
    assert conn.execute("SELECT 1 FROM bid WHERE bidder_name_raw = 'Out Of Scope'").fetchone() is not None


def test_rebuild_rows_rejects_empty_or_mixed_rows(conn):
    with pytest.raises(ValueError):
        db.rebuild_rows(conn, [], scope="1=1")
    with pytest.raises(ValueError):
        db.rebuild_rows(
            conn,
            [Bid("Acme", source="s"), Award("1001", source="s")],
            scope="1=1",
        )


# --- award line key & migration ---


def _award_amounts(conn, doc):
    return sorted(
        r[0] for r in conn.execute("SELECT award_amount FROM award WHERE document_number=?", (doc,))
    )


def test_multiple_award_lines_for_one_supplier_all_survive(conn):
    for amt, date in [("9975.00", "2020-01-01"), ("20564.90", "2020-02-01"), ("170000.00", "2020-03-01")]:
        db.upsert_row(
            conn,
            Award("3601120065", supplier_name_raw="Weston Forest Corporation",
                  award_amount=amt, award_date=date, source="odata"),
            overwrite=True,
        )
    conn.commit()
    assert _award_amounts(conn, "3601120065") == ["170000.00", "20564.90", "9975.00"]


def test_award_re_running_same_lines_does_not_duplicate(conn):
    for _ in range(2):
        for amt, date in [("100.00", "2020-01-01"), ("200.00", "2020-02-01")]:
            db.upsert_row(
                conn,
                Award("3601120065", supplier_name_raw="Acme Ltd",
                      award_amount=amt, award_date=date, source="odata"),
                overwrite=True,
            )
        conn.commit()
    assert len(_award_amounts(conn, "3601120065")) == 2


def test_award_null_amount_does_not_duplicate_on_every_sync(conn):
    for _ in range(3):
        db.upsert_row(
            conn,
            Award("3601120065", supplier_name_raw="Acme Ltd",
                  award_amount=None, award_date=None, source="odata"),
            overwrite=True,
        )
        conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
    assert conn.execute("SELECT award_amount FROM award").fetchone()[0] is None


def test_award_same_line_from_both_feeds_stays_two_rows(conn):
    for src in ("odata", "ckan_awarded"):
        db.upsert_row(
            conn,
            Award("3601120065", supplier_name_raw="Acme Ltd",
                  award_amount="100.00", award_date="2020-01-01", source=src),
            overwrite=True,
        )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 2


def test_award_upsert_updates_matching_line_and_keeps_first_seen(conn):
    row = Award("3601120065", supplier_name_raw="Acme Ltd",
                award_amount="100.00", award_date="2020-01-01", source="odata")
    db.upsert_row(conn, row, overwrite=True)
    conn.commit()
    first = conn.execute("SELECT first_seen FROM award").fetchone()[0]
    db.upsert_row(conn, row, overwrite=True)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
    assert conn.execute("SELECT first_seen FROM award").fetchone()[0] == first


def _pre73_db(tmp_path):
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


def test_award_line_key_migration_drops_old_unique_and_keeps_first_seen(tmp_path):
    path = _pre73_db(tmp_path)
    conn = db.connect(path)
    db.init_db(conn)

    assert conn.execute("SELECT first_seen FROM award").fetchone()[0] == "2020-06-01 00:00:00"
    for amt, date in [("20564.90", "2020-02-01"), ("170000.00", "2020-03-01")]:
        db.upsert_row(
            conn,
            Award("3601120065", supplier_name_raw="Weston Forest Corporation",
                  award_amount=amt, award_date=date, source="odata"),
            overwrite=True,
        )
    conn.commit()
    assert len(_award_amounts(conn, "3601120065")) == 3
    assert "_award_pre73" not in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()


def test_award_line_key_migration_is_idempotent(tmp_path):
    path = _pre73_db(tmp_path)
    conn = db.connect(path)
    schema = db.resources.files("toronto_bids.store").joinpath("schema.sql").read_text()
    assert db._rebuild_award_for_line_key(conn, schema)
    assert not db._rebuild_award_for_line_key(conn, schema)
    assert conn.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize("already_affected", [False, True], ids=["legacy-bid", "broken-response-fk"])
def test_bid_upgrade_preserves_rows_and_repairs_response_links(tmp_path, already_affected):
    path = tmp_path / "legacy.sqlite"
    schema = db.resources.files("toronto_bids.store").joinpath("schema.sql").read_text()
    if already_affected:
        schema = schema.replace('REFERENCES bid(id)', 'REFERENCES "_bid_pre114"(id)')
    else:
        schema = schema.replace("reference          TEXT,", "reference          TEXT NOT NULL,", 1)

    old = sqlite3.connect(path)
    old.executescript(schema)
    old.executescript("""
        INSERT INTO bid(id, reference, bidder_name_raw, first_seen)
        VALUES (42, '2020.BA5.3', 'Held Supplier', '2020-07-01');
        INSERT INTO city_scoped_bid_evidence VALUES
            ('proof', '5672751291', '2020.BA5.3', 'test', 'url', 'report', 'result', 1, 1, 1, '{}', '2020-07-01');
        INSERT INTO bid_scope(id, reference, source, scope_key, scope_kind, proof_sha256)
        VALUES (7, '2020.BA5.3', 'test', 'lot', 'lot', 'proof');
        INSERT INTO bid_response(id, scope_id, response_key, bid_id, bidder_name_raw,
                                 identity_status, response_kind, row_quotation, first_seen)
        VALUES (13, 7, 'held', 42, 'Held Supplier', 'named', 'base', 'held quote', '2020-07-02');
        INSERT INTO bid_response_price(id, response_id, price_key, value_raw, value_kind, price_header, quotation)
        VALUES (5, 13, 'total', '300', 'money', 'Total', 'quoted price');
    """)
    old.execute("UPDATE sqlite_sequence SET seq=100 WHERE name IN ('bid', 'bid_response')")
    if already_affected:
        old.execute(
            "INSERT INTO bid_response (scope_id, response_key, bid_id, bidder_name_raw, "
            "identity_status, response_kind, row_quotation) VALUES (7, 'orphan', 99, 'Supplier', 'named', 'base', '')"
        )
    old.commit()
    old.close()

    conn = db.connect(path)
    try:
        db.init_db(conn)
        db.upsert_row(conn, BidResponse(7, "new", "Held Supplier", "named", "base", "new quote", bid_id=42),
                      overwrite=True)
        db.upsert_row(conn, Bid(bidder_name_raw="Later Supplier", source="award_summary"), overwrite=True)
        conn.commit()
        db.init_db(conn)

        assert tuple(conn.execute("SELECT id, reference, first_seen FROM bid WHERE id=42").fetchone()) == (
            42, "2020.BA5.3", "2020-07-01",
        )
        assert tuple(conn.execute("SELECT id, bid_id, first_seen FROM bid_response WHERE response_key='held'").fetchone()) == (
            13, 42, "2020-07-02",
        )
        assert tuple(conn.execute("SELECT id, bid_id FROM bid_response WHERE response_key='new'").fetchone()) == (
            102 if already_affected else 101, 42,
        )
        if already_affected:
            assert conn.execute("SELECT bid_id FROM bid_response WHERE response_key='orphan'").fetchone()[0] is None
        assert conn.execute("SELECT id FROM bid WHERE reference IS NULL").fetchone()[0] == 101
        assert tuple(conn.execute("SELECT id, response_id, value_raw FROM bid_response_price").fetchone()) == (5, 13, "300")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            conn.execute("UPDATE bid_response SET bid_id = 999 WHERE id = 13")
    finally:
        conn.close()


# --- extraction cache ---


def test_extraction_cache_tracks_presence_by_version_idempotently(conn):
    assert not db.is_extracted(conn, "abc123", "v1")
    db.mark_extracted(conn, "abc123", "v1")
    assert db.is_extracted(conn, "abc123", "v1")
    assert not db.is_extracted(conn, "abc123", "v2")
    db.mark_extracted(conn, "abc123", "v1")
    assert conn.execute("SELECT COUNT(*) FROM extraction_cache").fetchone()[0] == 1


def test_extraction_cache_stores_and_updates_result_json(conn):
    assert db.get_extraction(conn, "abc123", "v1") is None
    db.mark_extracted(conn, "abc123", "v1", result_json='{"contracts": []}')
    assert db.get_extraction(conn, "abc123", "v1") == '{"contracts": []}'
    db.mark_extracted(conn, "abc123", "v1", result_json='{"contracts": [{"ref": "X"}]}')
    assert db.get_extraction(conn, "abc123", "v1") == '{"contracts": [{"ref": "X"}]}'
    assert conn.execute("SELECT COUNT(*) FROM extraction_cache").fetchone()[0] == 1
