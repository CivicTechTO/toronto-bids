from toronto_bids.models import Award, NonCompetitive, Solicitation, AribaPosting, SuspendedFirm
from toronto_bids.store import db


def test_init_creates_tables(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"solicitation", "award", "noncompetitive", "ariba_posting", "sync_run"} <= names


def test_upsert_solicitation_is_idempotent(conn):
    sol = Solicitation(document_number="3303123110", status="Awarded", source="odata")
    db.upsert_row(conn, sol, overwrite=True)
    db.upsert_row(conn, sol, overwrite=True)
    assert db.counts(conn)["solicitation"] == 1


def test_overwrite_true_lets_new_nonnull_win(conn):
    db.upsert_row(conn, Solicitation("3303123110", title=None, source="ckan"), overwrite=False)
    db.upsert_row(conn, Solicitation("3303123110", title="Toner Cartridges", source="odata"), overwrite=True)
    row = conn.execute("SELECT title FROM solicitation WHERE document_number='3303123110'").fetchone()
    assert row["title"] == "Toner Cartridges"


def test_overwrite_false_only_fills_nulls(conn):
    db.upsert_row(conn, Solicitation("3303123110", division="Purchasing", source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation("3303123110", division="SOMETHING ELSE", source="ckan"), overwrite=False)
    row = conn.execute("SELECT division FROM solicitation WHERE document_number='3303123110'").fetchone()
    assert row["division"] == "Purchasing"  # backfill must not clobber existing value


def test_overwrite_false_backfills_a_null(conn):
    db.upsert_row(conn, Solicitation("3303123110", division=None, source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation("3303123110", division="Toronto Water", source="ckan"), overwrite=False)
    row = conn.execute("SELECT division FROM solicitation WHERE document_number='3303123110'").fetchone()
    assert row["division"] == "Toronto Water"


def test_upsert_award_dedupes_on_docnum_supplier_source(conn):
    a = Award("3303123110", supplier_name_raw="Computer Media Group", award_amount="26773.58", source="odata")
    db.upsert_row(conn, a, overwrite=True)
    db.upsert_row(conn, a, overwrite=True)
    assert db.counts(conn)["award"] == 1


def test_upsert_noncompetitive_is_idempotent(conn):
    nc = NonCompetitive("8614", supplier_name_raw="Accuworx Inc", reason="Emergency", source="odata")
    db.upsert_row(conn, nc, overwrite=True)
    db.upsert_row(conn, nc, overwrite=True)
    assert db.counts(conn)["noncompetitive"] == 1


def test_sync_run_lifecycle(conn):
    run_id = db.start_sync_run(conn, "odata")
    db.finish_sync_run(conn, run_id, status="ok", rows_fetched=10, rows_upserted=10)
    row = conn.execute("SELECT status, rows_fetched FROM sync_run WHERE id=?", (run_id,)).fetchone()
    assert row["status"] == "ok" and row["rows_fetched"] == 10


def test_upsert_ariba_posting_is_idempotent(conn):
    p = AribaPosting(rfx_id="1110015885", document_number="5672751291",
                     title="RFT Watermain", raw_json="{}", source="ariba_discovery")
    db.upsert_row(conn, p, overwrite=True)
    db.upsert_row(conn, p, overwrite=True)
    assert db.counts(conn)["ariba_posting"] == 1


def test_ariba_posting_later_500_does_not_wipe_snapshot(conn):
    # Run 1: detail succeeded -> raw_json + document_number captured.
    db.upsert_row(conn, AribaPosting(rfx_id="1110015885", document_number="5672751291",
                                     raw_json="{\"x\":1}", source="ariba_discovery"), overwrite=True)
    # Run 2: detail 500'd -> those fields arrive as None. overwrite=True must NOT clobber them.
    db.upsert_row(conn, AribaPosting(rfx_id="1110015885", document_number=None,
                                     raw_json=None, source="ariba_discovery"), overwrite=True)
    row = conn.execute(
        "SELECT document_number, raw_json FROM ariba_posting WHERE rfx_id='1110015885'"
    ).fetchone()
    assert row["document_number"] == "5672751291"
    assert row["raw_json"] == "{\"x\":1}"


def test_counts_includes_ariba_posting(conn):
    assert "ariba_posting" in db.counts(conn)


def test_upsert_suspended_firm_is_idempotent(conn):
    firm = SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                         start_date="March 27, 2025", council_authority="2025.GG19.17",
                         source="suspended_firms")
    db.upsert_row(conn, firm, overwrite=True)
    db.upsert_row(conn, firm, overwrite=True)
    assert db.counts(conn)["suspended_firm"] == 1


def test_upsert_suspended_firm_distinct_authority_is_new_row(conn):
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Acme", council_authority="A1",
                                      source="suspended_firms"), overwrite=True)
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Acme", council_authority="A2",
                                      source="suspended_firms"), overwrite=True)
    assert db.counts(conn)["suspended_firm"] == 2


def test_suspended_firm_overwrite_updates_status(conn):
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                                      council_authority="2025.GG19.17", source="suspended_firms"),
                  overwrite=True)
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Reinstated",
                                      council_authority="2025.GG19.17", source="suspended_firms"),
                  overwrite=True)
    row = conn.execute("SELECT status FROM suspended_firm WHERE supplier_name_raw='Duron Ontario Ltd.'").fetchone()
    assert row["status"] == "Reinstated"


def test_counts_includes_suspended_firm(conn):
    assert "suspended_firm" in db.counts(conn)
