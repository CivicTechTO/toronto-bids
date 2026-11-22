import json

from toronto_bids import pipeline
from toronto_bids.models import Award, Solicitation
from toronto_bids.sources import ckan, odata
from toronto_bids.sources.ariba import normalize_posting
from toronto_bids.store import db
from tests.conftest import FIXTURES


class FakeSource:
    def __init__(self, name, rows, overwrite=True, boom=False):
        self.name = name
        self._rows = rows
        self.overwrite = overwrite
        self._boom = boom

    def fetch(self, http):
        if self._boom:
            raise RuntimeError("network exploded")
        return [{"i": i} for i in range(len(self._rows))]

    def normalize(self, raw):
        return [self._rows[raw["i"]]]


class _MidStreamFailSource:
    name = "mid_stream"
    overwrite = True

    def fetch(self, http):
        yield {"doc": "3303123110"}
        raise RuntimeError("stream cut off")

    def normalize(self, raw):
        return [Solicitation(raw["doc"], source="odata")]


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_run_source_upserts_and_records_ok(conn):
    src = FakeSource("odata_solicitations", [
        Solicitation("3303123110", status="Awarded", source="odata"),
        Award("3303123110", supplier_name_raw="Computer Media Group", source="odata"),
    ])
    fetched, upserted, error = pipeline.run_source(conn, http=None, source=src)
    assert (fetched, upserted, error) == (2, 2, None)
    assert db.counts(conn)["solicitation"] == 1
    assert db.counts(conn)["award"] == 1
    run = conn.execute("SELECT status FROM sync_run WHERE source='odata_solicitations'").fetchone()
    assert run["status"] == "ok"


def test_run_source_isolates_failure(conn):
    src = FakeSource("odata_solicitations", [], boom=True)
    fetched, upserted, error = pipeline.run_source(conn, http=None, source=src)
    assert (fetched, upserted) == (0, 0)
    assert "network exploded" in error
    run = conn.execute("SELECT status, error FROM sync_run WHERE source='odata_solicitations'").fetchone()
    assert run["status"] == "failed"
    assert "network exploded" in run["error"]


def test_run_source_mid_stream_failure_commits_partial_progress(conn):
    fetched, upserted, error = pipeline.run_source(conn, http=None, source=_MidStreamFailSource())
    assert (fetched, upserted) == (1, 1)
    assert "stream cut off" in error
    assert db.counts(conn)["solicitation"] == 1
    run = conn.execute(
        "SELECT status, rows_fetched, rows_upserted, error FROM sync_run WHERE source='mid_stream'"
    ).fetchone()
    assert run["status"] == "failed"
    assert (run["rows_fetched"], run["rows_upserted"]) == (1, 1)
    assert "stream cut off" in run["error"]


def test_sync_runs_all_and_one_failure_does_not_stop_others(conn):
    good = FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")])
    bad = FakeSource("ckan_open", [], boom=True)
    also_good = FakeSource("ckan_awarded", [Solicitation("5749398870", source="ckan_awarded")], overwrite=False)
    failures = pipeline.sync(conn, http=None, sources=[good, bad, also_good])
    assert db.counts(conn)["solicitation"] == 2
    assert db.counts(conn)["sync_run"] == 9
    assert [name for name, _ in failures] == ["ckan_open"]
    assert "network exploded" in failures[0][1]


def test_sync_reports_every_failed_source(conn):
    bad = FakeSource("ckan_open", [], boom=True)
    worse = FakeSource("ariba_discovery", [], boom=True)
    failures = pipeline.sync(conn, http=None, sources=[bad, worse])
    assert sorted(name for name, _ in failures) == ["ariba_discovery", "ckan_open"]


def test_sync_only_filters_sources(conn):
    good = FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")])
    other = FakeSource("ckan_open", [Solicitation("5749398870", source="ckan_open")])
    pipeline.sync(conn, http=None, sources=[good, other], only=["odata_solicitations"])
    assert db.counts(conn)["solicitation"] == 1
    assert db.counts(conn)["sync_run"] == 7


def test_sync_runs_supplier_dimension_after_sources(conn):
    good = FakeSource("odata_solicitations", [
        Award("3303123110", supplier_name_raw="Compugen Inc.", source="odata"),
    ])
    pipeline.sync(conn, http=None, sources=[good])
    assert db.counts(conn)["supplier"] == 1
    row = conn.execute("SELECT status FROM sync_run WHERE source='supplier_dimension'").fetchone()
    assert row is not None and row["status"] == "ok"


def test_sync_ariba_bridge_failure_is_isolated(conn, monkeypatch):
    def boom(_conn):
        raise RuntimeError("bridge exploded")
    monkeypatch.setattr(pipeline, "bridge_postings_to_spine", boom)
    failures = pipeline.sync(conn, http=None, sources=[])
    row = conn.execute("SELECT status, error FROM sync_run WHERE source='ariba_bridge'").fetchone()
    assert row["status"] == "failed"
    assert "bridge exploded" in row["error"]
    assert failures == [("ariba_bridge", "bridge exploded")]
    assert conn.execute(
        "SELECT status FROM sync_run WHERE source='supplier_dimension'"
    ).fetchone()["status"] == "ok"


def test_run_linking_pass_rolls_back_on_failure(conn):
    def failing_link(c):
        db.upsert_row(c, Solicitation("9999999999", source="odata"), overwrite=True)
        raise RuntimeError("link pass blew up")

    error = pipeline._run_linking_pass(conn, "broken_pass", failing_link)
    assert error == "link pass blew up"
    assert db.counts(conn)["solicitation"] == 0
    row = conn.execute("SELECT status, error FROM sync_run WHERE source='broken_pass'").fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "link pass blew up"


def test_odata_spine_wins_and_ckan_links_on_document_number(conn):
    for raw in _load("odata_solicitation.json")["value"]:
        for row in odata.normalize_solicitation(raw):
            db.upsert_row(conn, row, overwrite=True)
    for raw in _load("ckan_awarded.json")["result"]["records"]:
        if raw.get("Document Number") == "3303123110":
            raw = {**raw, "Division": "CKAN DIVISION - MUST NOT WIN"}
        for row in ckan.normalize_awarded(raw):
            db.upsert_row(conn, row, overwrite=False)
    conn.commit()

    sol = conn.execute(
        "SELECT title, status, division FROM solicitation WHERE document_number='3303123110'"
    ).fetchone()
    assert sol is not None
    assert sol["status"] == "Awarded"
    assert sol["division"] == "Purchasing & Materials Management"
    assert sol["title"] == "Toner Cartridges"

    sources = {r["source"] for r in conn.execute(
        "SELECT source FROM award WHERE document_number='3303123110'"
    )}
    assert "odata" in sources
    assert "ckan_awarded" in sources


def test_ariba_fetch_normalize_upsert_bridges_and_archives(conn):
    search = _load("ariba_search_record.json")
    detail = _load("ariba_detail.json")
    raws = [
        {"search": search, "detail": detail},
        {"search": {**search, "rfxID": "1110099999", "title": "no doc here"}, "detail": None},
    ]
    for raw in raws:
        for row in normalize_posting(raw):
            db.upsert_row(conn, row, overwrite=True)
    conn.commit()

    assert db.counts(conn)["ariba_posting"] == 2
    bridged = conn.execute(
        "SELECT document_number, raw_json FROM ariba_posting WHERE rfx_id='1110015885'"
    ).fetchone()
    assert bridged["document_number"] == "5672751291"
    assert bridged["raw_json"] is not None
    unbridged = conn.execute(
        "SELECT document_number, raw_json FROM ariba_posting WHERE rfx_id='1110099999'"
    ).fetchone()
    assert unbridged["document_number"] is None
    assert unbridged["raw_json"] is None
