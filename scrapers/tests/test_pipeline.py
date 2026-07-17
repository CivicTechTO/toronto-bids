from toronto_bids import pipeline
from toronto_bids.models import Award, Solicitation
from toronto_bids.store import db


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
    assert "network exploded" in error  # returned to the caller, not just buried in sync_run
    run = conn.execute("SELECT status, error FROM sync_run WHERE source='odata_solicitations'").fetchone()
    assert run["status"] == "failed"
    assert "network exploded" in run["error"]


def test_sync_runs_all_and_one_failure_does_not_stop_others(conn):
    good = FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")])
    bad = FakeSource("ckan_open", [], boom=True)
    also_good = FakeSource("ckan_awarded", [Solicitation("5749398870", source="ckan_awarded")], overwrite=False)
    failures = pipeline.sync(conn, http=None, sources=[good, bad, also_good])
    assert db.counts(conn)["solicitation"] == 2
    # 3 sources + 5 post-source passes (title_cleanup, ariba_bridge, amount_backfill,
    # amount_labels, supplier_dimension) = 8
    assert db.counts(conn)["sync_run"] == 8
    # the failure is isolated but NOT swallowed: sync hands it back
    assert [name for name, _ in failures] == ["ckan_open"]
    assert "network exploded" in failures[0][1]


def test_sync_returns_no_failures_when_all_sources_succeed(conn):
    good = FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")])
    assert pipeline.sync(conn, http=None, sources=[good]) == []


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
    # 1 source + 5 post-source passes (which run regardless of --only) = 6
    assert db.counts(conn)["sync_run"] == 6


def test_sync_runs_supplier_dimension_after_sources(conn):
    from toronto_bids import pipeline
    from toronto_bids.models import Award
    good = FakeSource("odata_solicitations", [
        Award("3303123110", supplier_name_raw="Compugen Inc.", source="odata"),
    ])
    pipeline.sync(conn, http=None, sources=[good])
    # the linking pass ran: a supplier exists and a sync_run named supplier_dimension is recorded
    assert db.counts(conn)["supplier"] == 1
    row = conn.execute("SELECT status FROM sync_run WHERE source='supplier_dimension'").fetchone()
    assert row is not None and row["status"] == "ok"


def test_sync_ariba_bridge_failure_is_isolated(conn, monkeypatch):
    from toronto_bids import pipeline
    def boom(_conn):
        raise RuntimeError("bridge exploded")
    monkeypatch.setattr(pipeline, "bridge_postings_to_spine", boom)
    failures = pipeline.sync(conn, http=None, sources=[])
    row = conn.execute("SELECT status, error FROM sync_run WHERE source='ariba_bridge'").fetchone()
    assert row["status"] == "failed"
    assert "bridge exploded" in row["error"]
    assert failures == [("ariba_bridge", "bridge exploded")]
    # the pass behind it still ran: one failure never stops the next
    assert conn.execute(
        "SELECT status FROM sync_run WHERE source='supplier_dimension'"
    ).fetchone()["status"] == "ok"


def test_sync_supplier_dimension_failure_is_isolated(conn, monkeypatch):
    from toronto_bids import pipeline
    def boom(_conn):
        raise RuntimeError("link exploded")
    monkeypatch.setattr(pipeline, "build_supplier_dimension", boom)
    failures = pipeline.sync(conn, http=None, sources=[])  # no sources; linking still fails safely
    row = conn.execute("SELECT status, error FROM sync_run WHERE source='supplier_dimension'").fetchone()
    assert row["status"] == "failed"
    assert "link exploded" in row["error"]
    assert failures == [("supplier_dimension", "link exploded")]
