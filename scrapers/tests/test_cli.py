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


# --- enrich-titles (#65) -----------------------------------------------------------------

def _titles_env(monkeypatch, tmp_path):
    """Isolate the DB and both title sources so nothing touches the real store."""
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
    """No --scrape means no browser: agendas already on disk are enough."""
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
    assert "1 -> 1" in out       # nothing named, and it says so rather than claiming success


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
