from toronto_bids.models import Award, NonCompetitive, Solicitation
from toronto_bids.store import db


def test_init_creates_tables(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"solicitation", "award", "noncompetitive", "sync_run"} <= names


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
