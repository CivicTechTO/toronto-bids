"""CLI-level tests for failure visibility (#18).

The bug these lock down: a sync where every source blew up still printed
"Sync complete" and exited 0, so nothing — human or cron — could tell.
"""
from toronto_bids import cli, config, pipeline
from toronto_bids.models import Solicitation
from toronto_bids.store import db

from tests.test_pipeline import FakeSource


def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite")


def test_sync_exits_zero_and_says_complete_when_sources_succeed(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "default_sources", lambda: [
        FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")]),
    ])
    assert cli.main(["sync"]) == 0
    assert "Sync complete" in capsys.readouterr().out


def test_sync_exits_nonzero_and_names_the_failure(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "default_sources", lambda: [
        FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")]),
        FakeSource("ckan_open", [], boom=True),
    ])
    assert cli.main(["sync"]) == 1
    out = capsys.readouterr()
    assert "Sync complete" not in out.out
    assert "1 failed source(s)" in out.out
    assert "ckan_open" in out.err and "network exploded" in out.err


def test_status_shows_last_run_per_source(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    db.finish_sync_run(conn, db.start_sync_run(conn, "ckan_open"),
                       status="failed", error="network exploded")
    conn.close()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "ckan_open" in out and "failed" in out and "network exploded" in out
