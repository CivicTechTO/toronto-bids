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
    fetched, upserted = pipeline.run_source(conn, http=None, source=src)
    assert (fetched, upserted) == (2, 2)
    assert db.counts(conn)["solicitation"] == 1
    assert db.counts(conn)["award"] == 1
    run = conn.execute("SELECT status FROM sync_run WHERE source='odata_solicitations'").fetchone()
    assert run["status"] == "ok"


def test_run_source_isolates_failure(conn):
    src = FakeSource("odata_solicitations", [], boom=True)
    fetched, upserted = pipeline.run_source(conn, http=None, source=src)
    assert (fetched, upserted) == (0, 0)
    run = conn.execute("SELECT status, error FROM sync_run WHERE source='odata_solicitations'").fetchone()
    assert run["status"] == "failed"
    assert "network exploded" in run["error"]


def test_sync_runs_all_and_one_failure_does_not_stop_others(conn):
    good = FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")])
    bad = FakeSource("ckan_open", [], boom=True)
    also_good = FakeSource("ckan_awarded", [Solicitation("5749398870", source="ckan_awarded")], overwrite=False)
    pipeline.sync(conn, http=None, sources=[good, bad, also_good])
    assert db.counts(conn)["solicitation"] == 2
    assert db.counts(conn)["sync_run"] == 3


def test_sync_only_filters_sources(conn):
    good = FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")])
    other = FakeSource("ckan_open", [Solicitation("5749398870", source="ckan_open")])
    pipeline.sync(conn, http=None, sources=[good, other], only=["odata_solicitations"])
    assert db.counts(conn)["solicitation"] == 1
    assert db.counts(conn)["sync_run"] == 1
