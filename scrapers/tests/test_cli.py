import json

from toronto_bids import cli, config, pipeline
from toronto_bids.buyers import seed_buyers
from toronto_bids.cli import main
from toronto_bids.models import Award, Solicitation
from toronto_bids.store import db

from tests.test_pipeline import FakeSource


def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite")


def test_main_with_no_args_returns_zero():
    assert main([]) == 0


class _DummyHttp:
    def close(self):
        pass


def test_sync_exits_zero_and_says_complete_when_sources_succeed(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "HttpClient", _DummyHttp)
    monkeypatch.setattr(pipeline, "default_sources", lambda: [
        FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")]),
    ])
    assert cli.main(["sync"]) == 0
    assert "Sync complete" in capsys.readouterr().out


def test_sync_exits_nonzero_and_names_the_failure(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "HttpClient", _DummyHttp)
    monkeypatch.setattr(pipeline, "default_sources", lambda: [
        FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")]),
        FakeSource("ckan_open", [], boom=True),
    ])
    assert cli.main(["sync"]) == 1
    out = capsys.readouterr()
    assert "Sync complete" not in out.out
    assert "1 failed source(s)" in out.out
    assert "ckan_open" in out.err and "network exploded" in out.err


def test_sync_only_unknown_source_warns(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "HttpClient", _DummyHttp)
    monkeypatch.setattr(pipeline, "default_sources", lambda: [
        FakeSource("odata_solicitations", []),
    ])
    assert cli.main(["sync", "--only", "no_such_source"]) == 0
    assert "Warning: unknown source name(s): no_such_source" in capsys.readouterr().out


def test_status_shows_counts_and_last_run_per_source(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    db.finish_sync_run(conn, db.start_sync_run(conn, "ckan_open"),
                       status="failed", error="network exploded")
    conn.close()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "solicitation" in out
    assert "ckan_open" in out and "failed" in out and "network exploded" in out


# --- record-step ---

def _run_record_step(monkeypatch, tmp_path, argv):
    _isolate_db(monkeypatch, tmp_path)
    code = cli.main(argv)
    conn = db.connect(config.DB_PATH)
    try:
        return code, db.last_runs(conn)
    finally:
        conn.close()


def test_record_step_ok_writes_a_clean_sync_run_row(monkeypatch, tmp_path):
    code, runs = _run_record_step(monkeypatch, tmp_path, ["record-step", "publish", "ok"])
    assert code == 0
    assert len(runs) == 1
    assert runs[0]["source"] == "publish"
    assert runs[0]["status"] == "ok"
    assert runs[0]["error"] is None


def test_record_step_failed_carries_the_error(monkeypatch, tmp_path):
    code, runs = _run_record_step(
        monkeypatch, tmp_path,
        ["record-step", "r2_mirror", "failed", "--error", "wrangler exit 1"],
    )
    assert code == 0
    assert runs[0]["source"] == "r2_mirror"
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"] == "wrangler exit 1"


def test_record_step_two_calls_keep_only_the_latest_in_last_runs(monkeypatch, tmp_path):
    _isolate_db(monkeypatch, tmp_path)
    cli.main(["record-step", "publish", "failed", "--error", "yesterday's problem"])
    cli.main(["record-step", "publish", "ok"])
    conn = db.connect(config.DB_PATH)
    try:
        runs = db.last_runs(conn)
    finally:
        conn.close()
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"


# --- enrich-titles ---

def _titles_env(monkeypatch, tmp_path):
    _isolate_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "COUNCIL_AGENDAS_DIR", tmp_path / "council" / "agendas")
    monkeypatch.setattr(config, "LEGACY_ARIBA_DIR", tmp_path / "legacy" / "ariba_data")


def _seed_titleless(doc="3234668279"):
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    db.upsert_row(conn, Solicitation(doc, title=None, source="odata"), overwrite=True)
    conn.commit()
    conn.close()


def test_enrich_titles_runs_offline_from_cached_agendas(monkeypatch, tmp_path, capsys):
    _titles_env(monkeypatch, tmp_path)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _seed_titleless()
    agendas = tmp_path / "council" / "agendas"
    agendas.mkdir(parents=True)
    (agendas / "2022.BA189.html").write_text(
        "<html><body><h3>BA189.1 - Award of Ariba Document Number 3234668279 to GHD "
        "Limited for the Aeration Blower System Upgrades</h3></body></html>")

    assert cli.main(["enrich-titles"]) == 0
    out = capsys.readouterr().out
    assert "cached" in out
    assert "1 -> 0" in out

    conn = db.connect(config.DB_PATH)
    row = conn.execute("SELECT title, title_source FROM solicitation").fetchone()
    assert "Aeration Blower System Upgrades" in row["title"]
    assert row["title_source"] == "bid_award_panel"
    conn.close()


def test_enrich_titles_says_so_when_there_is_nothing_cached(monkeypatch, tmp_path, capsys):
    _titles_env(monkeypatch, tmp_path)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _seed_titleless()
    assert cli.main(["enrich-titles"]) == 0
    out = capsys.readouterr().out
    assert "download the council-agendas archive" in out
    assert "1 -> 1" in out


def test_enrich_titles_prefers_the_legacy_posting_page_over_a_council_heading(
        monkeypatch, tmp_path, capsys):
    _titles_env(monkeypatch, tmp_path)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _seed_titleless()
    agendas = tmp_path / "council" / "agendas"
    agendas.mkdir(parents=True)
    (agendas / "2022.BA189.html").write_text(
        "<html><body><h3>BA189.1 - Award of Ariba Document Number 3234668279 to GHD "
        "Limited for the Aeration Blower System Upgrades</h3></body></html>")
    legacy = tmp_path / "legacy" / "ariba_data" / "Doc3234668279"
    legacy.mkdir(parents=True)
    (legacy / "Doc3234668279.html").write_text(
        "<html><head><title>RFP for Aeration Blower System Upgrades</title></head></html>")

    assert cli.main(["enrich-titles"]) == 0
    conn = db.connect(config.DB_PATH)
    row = conn.execute("SELECT title, title_source FROM solicitation").fetchone()
    assert row["title"] == "RFP for Aeration Blower System Upgrades"
    assert row["title_source"] == "legacy_ariba_html"
    conn.close()


def test_enrich_titles_does_not_touch_a_title_the_city_published(monkeypatch, tmp_path):
    _titles_env(monkeypatch, tmp_path)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    db.upsert_row(conn, Solicitation("3234668279", title="Urban Forestry Supplies",
                                     source="odata"), overwrite=True)
    conn.commit()
    conn.close()
    agendas = tmp_path / "council" / "agendas"
    agendas.mkdir(parents=True)
    (agendas / "2022.BA189.html").write_text(
        "<html><body><h3>BA189.1 - Award of Ariba Document Number 3234668279 to GHD "
        "Limited for the Aeration Blower System Upgrades</h3></body></html>")

    assert cli.main(["enrich-titles"]) == 0
    conn = db.connect(config.DB_PATH)
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] == "Urban Forestry Supplies"
    conn.close()


# --- amounts ---

def test_amounts_unlabelled_exits_zero_when_clean(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    assert cli.main(["amounts", "unlabelled"]) == 0
    assert "No unlabelled amounts" in capsys.readouterr().out


def test_amounts_unlabelled_exits_nonzero_when_unlabelled_rows_exist(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    db.upsert_row(conn, Award(
        document_number="3303123110",
        supplier_name_raw="Acme",
        award_amount="not-a-real-amount-xyz",
        source="odata",
    ), overwrite=True)
    conn.commit()
    conn.close()

    assert cli.main(["amounts", "unlabelled"]) == 1
    out = capsys.readouterr().out
    assert "not-a-real-amount-xyz" in out


# --- manifest ---

def test_manifest_command_writes_sizes(tmp_path):
    a = tmp_path / "bids.json"
    a.write_bytes(b"x" * 12)
    out = tmp_path / "manifest.json"
    rc = main(["manifest", str(a), "--out", str(out)])
    assert rc == 0
    doc = json.loads(out.read_text())
    assert doc["artifacts"] == [{"name": "bids.json", "bytes": 12}]


# --- export ---

def _run_export(monkeypatch, conn, tmp_path, capsys, argv=None):
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert cli.main(argv or ["export"]) == 0
    return capsys.readouterr().out


def test_export_writes_default_path_and_bulk_bundles(monkeypatch, tmp_path, capsys):
    _isolate_db(monkeypatch, tmp_path)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    db.upsert_row(conn, Solicitation(document_number="5672751291", source="odata"), overwrite=True)
    conn.commit()
    conn.close()

    assert main(["export"]) == 0
    export_dir = tmp_path / "export"
    bids_json = export_dir / "bids.json"
    assert bids_json.exists()
    assert json.loads(bids_json.read_text())["solicitations"][0]["document_number"] == "5672751291"
    assert (export_dir / "bids-csv.zip").exists()
    assert (export_dir / "solicitation.parquet").exists()
    assert "Exported 1 solicitations to" in capsys.readouterr().out


def test_export_respects_custom_out_path(monkeypatch, tmp_path):
    _isolate_db(monkeypatch, tmp_path)
    custom = tmp_path / "custom" / "out.json"
    assert main(["export", "--out", str(custom)]) == 0
    assert custom.exists()
    assert (custom.parent / "schema.json").exists()
    assert (custom.parent / "bids-csv.zip").exists()
    assert (custom.parent / "solicitation.parquet").exists()


def test_export_mentions_agency_buyers_only_when_seeded(conn, monkeypatch, tmp_path, capsys):
    buyers = seed_buyers(conn)
    out = _run_export(monkeypatch, conn, tmp_path, capsys)
    assert f"0 solicitations + {len(buyers)} agency buyer(s)" in out


def test_export_plain_city_spine_omits_agency_buyer_suffix(conn, monkeypatch, tmp_path, capsys):
    out = _run_export(monkeypatch, conn, tmp_path, capsys)
    assert "Exported 0 solicitations to" in out
    assert "agency buyer" not in out
