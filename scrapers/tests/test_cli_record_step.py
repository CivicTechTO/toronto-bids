from toronto_bids import cli
from toronto_bids.store import db


def _run(monkeypatch, tmp_path, argv):
    db_path = tmp_path / "test.sqlite"

    def _open():
        conn = db.connect(db_path)
        db.init_db(conn)
        return conn
    monkeypatch.setattr(cli, "_open_db", _open)
    exit_code = cli.main(argv)
    conn = db.connect(db_path)
    try:
        return exit_code, db.last_runs(conn)
    finally:
        conn.close()


def test_record_step_ok_writes_a_clean_sync_run_row(monkeypatch, tmp_path):
    code, runs = _run(monkeypatch, tmp_path, ["record-step", "publish", "ok"])
    assert code == 0
    assert len(runs) == 1
    assert runs[0]["source"] == "publish"
    assert runs[0]["status"] == "ok"
    assert runs[0]["error"] is None


def test_record_step_failed_carries_the_error(monkeypatch, tmp_path):
    code, runs = _run(monkeypatch, tmp_path,
                      ["record-step", "r2_mirror", "failed", "--error", "wrangler exit 1"])
    assert code == 0
    assert runs[0]["source"] == "r2_mirror"
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"] == "wrangler exit 1"


def test_record_step_rejects_an_unknown_status(capsys, tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["record-step", "publish", "not-a-status"])


def test_two_calls_to_the_same_name_keep_only_the_latest_in_last_runs(monkeypatch, tmp_path):
    db_path = tmp_path / "test.sqlite"

    def _open():
        conn = db.connect(db_path)
        db.init_db(conn)
        return conn
    monkeypatch.setattr(cli, "_open_db", _open)
    cli.main(["record-step", "publish", "failed", "--error", "yesterday's problem"])
    cli.main(["record-step", "publish", "ok"])
    conn = db.connect(db_path)
    try:
        runs = db.last_runs(conn)
    finally:
        conn.close()
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"
