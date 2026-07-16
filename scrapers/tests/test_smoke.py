from toronto_bids.cli import main
from toronto_bids.models import Solicitation
from toronto_bids.store import db
import toronto_bids.pipeline as pipeline_mod


def test_main_with_no_args_returns_zero():
    assert main([]) == 0


def test_status_on_empty_db_prints_zero_counts(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("toronto_bids.config.DB_PATH", tmp_path / "bids.sqlite")
    monkeypatch.setattr("toronto_bids.config.DATA_DIR", tmp_path)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "solicitation" in out


def test_sync_uses_pipeline_and_persists(tmp_path, monkeypatch):
    db_path = tmp_path / "bids.sqlite"
    monkeypatch.setattr("toronto_bids.config.DB_PATH", db_path)
    monkeypatch.setattr("toronto_bids.config.DATA_DIR", tmp_path)

    def fake_sync(conn, http, sources=None, only=None):
        db.upsert_row(conn, Solicitation("3303123110", source="odata"), overwrite=True)
        conn.commit()
        return []  # no failures
    monkeypatch.setattr(pipeline_mod, "sync", fake_sync)

    assert main(["sync"]) == 0
    conn = db.connect(db_path)
    assert db.counts(conn)["solicitation"] == 1
    conn.close()


def test_export_writes_default_path(tmp_path, monkeypatch, capsys):
    from toronto_bids.models import Solicitation
    monkeypatch.setattr("toronto_bids.config.DB_PATH", tmp_path / "bids.sqlite")
    monkeypatch.setattr("toronto_bids.config.DATA_DIR", tmp_path)
    # Seed one row via a sync-less direct write path: open the db the CLI will use.
    from toronto_bids.store import db
    conn = db.connect(tmp_path / "bids.sqlite")
    db.init_db(conn)
    db.upsert_row(conn, Solicitation(document_number="5672751291", source="odata"), overwrite=True)
    conn.commit()
    conn.close()

    assert main(["export"]) == 0
    import json
    out = tmp_path / "export" / "bids.json"
    assert out.exists()
    doc = json.loads(out.read_text())
    assert doc["solicitations"][0]["document_number"] == "5672751291"
    assert "Exported" in capsys.readouterr().out
