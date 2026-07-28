"""`tb nightly` — what the systemd timer calls (deployment spec §3.3).

Every step is isolated the way pipeline.run_source already isolates sources: one failure never
stops the steps behind it. These tests are offline — every network-touching call is patched.
"""
import sqlite3

import pytest

from toronto_bids import cli, config, notify


@pytest.fixture
def nightly(conn, monkeypatch, tmp_path):
    """A `tb nightly` with the network removed and the data dir pointed at tmp."""
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [])
    monkeypatch.setattr(cli.HttpClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(cli.HttpClient, "close", lambda self: None)
    from toronto_bids.sources import award_summary, bids_tenders
    monkeypatch.setattr(award_summary, "download_award_summaries", lambda *a, **k: 0)
    monkeypatch.setattr(award_summary, "store_award_summary_bids", lambda *a, **k: 0)
    monkeypatch.setattr(bids_tenders, "run_portal_capture", lambda *a, **k: {})
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(ariba_attachments, "capture_attachments", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_is_first_of_month", lambda: False)
    from toronto_bids.sources import council as council_src
    monkeypatch.setattr(council_src, "enrich_council", lambda *a, **k: 0)
    monkeypatch.setattr(notify, "post", lambda *a, **k: False)
    return lambda: cli.main(["nightly"])


def test_a_clean_run_exits_zero_and_writes_the_export(nightly, tmp_path):
    assert nightly() == 0
    assert (tmp_path / "export" / "bids.json").exists()
    # schema.json rides the export step (#168) — publish-data.sh requires it beside bids.json.
    assert (tmp_path / "export" / "schema.json").exists()
    assert (tmp_path / "export" / "bids-csv.zip").exists()
    assert (tmp_path / "export" / "solicitation.parquet").exists()


def test_a_failed_source_exits_non_zero_so_systemd_sees_it(nightly, monkeypatch):
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [("ariba_discovery", "boom")])
    assert nightly() == 1


def test_the_export_runs_even_after_a_partial_sync(nightly, monkeypatch, tmp_path):
    """Rows are committed per-source and never deleted, so partial data is still data.
    Skipping the export would discard a good artifact over one bad feed."""
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [("ariba_discovery", "boom")])
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_raising_sync_is_caught_and_the_run_continues(nightly, monkeypatch, tmp_path):
    """pipeline.sync catches per-source, but a failure in the pass machinery itself would
    otherwise take the export down with it."""
    def boom(*a, **k):
        raise RuntimeError("pipeline exploded")
    monkeypatch.setattr(cli.pipeline, "sync", boom)
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_raising_award_summary_step_does_not_stop_the_export(nightly, monkeypatch, tmp_path):
    from toronto_bids.sources import award_summary
    def boom(*a, **k):
        raise RuntimeError("portal down")
    monkeypatch.setattr(award_summary, "download_award_summaries", boom)
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_raising_portal_step_does_not_stop_the_export(nightly, monkeypatch, tmp_path):
    """The portal step (bids_tenders.run_portal_capture) is isolated the same way sync and
    award_summary are: a failure records to `failures` and the export still runs."""
    from toronto_bids.sources import bids_tenders
    def boom(*a, **k):
        raise RuntimeError("portal down")
    monkeypatch.setattr(bids_tenders, "run_portal_capture", boom)
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_failed_portal_body_is_recorded_but_does_not_fail_the_run_alone(nightly, monkeypatch):
    """run_portal_capture already isolates per-body; a `FAILED: ...` string in its result dict
    is surfaced into `failures` (and therefore the exit code), without raising."""
    from toronto_bids.sources import bids_tenders
    monkeypatch.setattr(bids_tenders, "run_portal_capture",
                         lambda *a, **k: {"trca": "FAILED: boom", "toronto-zoo": 0})
    assert nightly() == 1


def test_the_summary_is_posted(nightly, monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    assert nightly() == 0
    assert len(posted) == 1
    assert posted[0].startswith("*✅ toronto-bids nightly*")


def test_a_failing_run_posts_the_failure(nightly, monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [("ariba_discovery", "boom")])
    assert nightly() == 1
    assert posted[0].startswith("*❌ toronto-bids nightly*")
    assert "*Failures" in posted[0]
    assert "ariba_discovery" in posted[0]


def test_a_broken_database_still_reports_to_slack(nightly, monkeypatch):
    """The one class of failure the guards missed. If _open_db raises and nothing catches it,
    the exception leaves _cmd_nightly and notify.post never fires — the run breaks in exactly
    the way nobody is told about."""
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    def boom():
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(cli, "_open_db", boom)
    assert nightly() == 1
    assert posted and posted[0].startswith("*❌ toronto-bids nightly*")
    assert "unable to open database file" in posted[0]


def test_counting_the_archive_failing_still_reports_to_slack(nightly, monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    def boom(_conn):
        raise sqlite3.DatabaseError("database disk image is malformed")
    monkeypatch.setattr(cli.db, "counts", boom)
    assert nightly() == 1
    assert posted and posted[0].startswith("*❌ toronto-bids nightly*")


def test_a_failure_building_the_http_client_does_not_cost_us_the_export(nightly, monkeypatch,
                                                                       tmp_path):
    """The export needs only the database — it does not need the network. Losing a good
    artifact because an HTTP client would not construct is the failure this whole command is
    shaped to avoid."""
    def boom(self, *a, **k):
        raise RuntimeError("no http for you")
    monkeypatch.setattr(cli.HttpClient, "__init__", boom)
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_counts_failure_is_labelled_counts_not_open_db(nightly, monkeypatch):
    """Blaming 'open_db' for a failure in the counting query sends the reader to the wrong
    system. The open succeeded; the count did not."""
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    calls = {"n": 0}
    real_counts = cli.db.counts
    def flaky(conn):
        calls["n"] += 1
        if calls["n"] == 1:               # the `before` count fails
            raise sqlite3.DatabaseError("locked")
        return real_counts(conn)           # the `after` count recovers
    monkeypatch.setattr(cli.db, "counts", flaky)
    assert nightly() == 1
    assert posted
    assert "counts: locked" in posted[0]
    # and the recovered `after` count must NOT be reported as a delta against a zero `before`
    assert "(+" not in posted[0]


def test_a_failure_closing_the_database_does_not_swallow_the_summary(nightly, monkeypatch, conn):
    """A successful export must still get reported: conn.close() raising (a wedged disk, a
    locked file, a competing writer) must not prevent notify.post from firing after a run
    that otherwise completed cleanly.

    sqlite3.Connection is a C type — its methods can't be monkeypatched directly (`cannot set
    'close' attribute of immutable type`), so a thin proxy stands in for `conn` and forwards
    everything except `close`.
    """
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)

    class _CloseFails:
        def close(self):
            raise sqlite3.OperationalError("disk I/O error")

        def __getattr__(self, name):
            return getattr(conn, name)

    monkeypatch.setattr(cli, "_open_db", lambda: _CloseFails())
    assert nightly() == 1
    assert posted and posted[0].startswith("*❌ toronto-bids nightly*")


def test_a_raising_ariba_attachment_step_does_not_stop_the_export(nightly, monkeypatch, tmp_path):
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(ariba_attachments, "capture_attachments",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("browser died")))
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_raising_agency_capture_does_not_stop_the_export(nightly, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_capture_agency_bodies",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tmmis blocked")))
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_an_agency_body_failure_is_recorded_but_export_still_runs(nightly, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [("zoo", "boom")])
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_report_sources_excludes_linking_passes_and_validator(conn):
    # sync_run records the linking passes and the schema-drift validator too; they are not
    # fetch sources (a pass touching 0 rows is normal), so the Sources list must drop them —
    # otherwise every night falsely ⚠-flags title_cleanup/ariba_bridge/schema_check as broken.
    from toronto_bids import cli
    from toronto_bids.store import db as _db
    for src, fetched in (("schema_check", 0), ("odata_solicitations", 7446),
                         ("supplier_dimension", 8020), ("title_cleanup", 0)):
        rid = _db.start_sync_run(conn, src)
        _db.finish_sync_run(conn, rid, status="ok", rows_fetched=fetched, rows_upserted=fetched)
    names = [r["source"] for r in cli._report_sources(conn, 0)]
    assert "odata_solicitations" in names
    assert "schema_check" not in names          # validator, fetches 0 by nature
    assert "supplier_dimension" not in names     # linking pass, not a fetch
    assert "title_cleanup" not in names          # linking pass, not a fetch


# --- #178: the sync step's headline must describe the run, not a static list ------------

def _record(conn, source, status="ok"):
    from toronto_bids.store import db as _db
    rid = _db.start_sync_run(conn, source)
    _db.finish_sync_run(conn, rid, status=status, error=None if status == "ok" else "boom")


def test_sync_detail_counts_every_unit_the_run_recorded(conn):
    """The old detail was `len(default_sources())` — a static 9 against the 14 rows a real
    run writes, undercounting by exactly the five post-source passes (#178)."""
    from toronto_bids import cli, pipeline
    _record(conn, "schema_check")
    for s in pipeline.default_sources():
        if s.name != "schema_check":
            _record(conn, s.name)
    for name, _fn in pipeline.linking_passes():
        _record(conn, name)

    detail = cli._sync_detail(conn, 0)
    assert detail == "schema check · 8 sources · 5 passes"
    # The categories account for every row, which is the property that actually broke.
    assert 1 + 8 + 5 == len(cli.db.sync_runs_since(conn, 0)) == 14


def test_sync_detail_names_a_failure_wherever_it_happened(conn):
    """A linking pass is isolated and recorded exactly like a source, so a pass failure is a
    real sync failure — the thing the old count could not have accounted for."""
    from toronto_bids import cli
    _record(conn, "schema_check")
    _record(conn, "odata_solicitations")
    _record(conn, "supplier_dimension", status="failed")
    assert cli._sync_detail(conn, 0) == "schema check · 1 source · 1 pass · 1 FAILED"


def test_sync_detail_says_so_when_the_run_recorded_nothing(conn):
    """`0 sources · 0 passes` reads like a healthy empty run; it is a broken one."""
    from toronto_bids import cli
    assert cli._sync_detail(conn, 0) == "nothing recorded"


def test_linking_passes_is_the_list_sync_actually_runs(conn):
    """The count is only honest while the report's idea of the passes and the pipeline's are
    the same object. Run sync with no sources and read back what it recorded."""
    from toronto_bids import pipeline
    from toronto_bids.store import db as _db
    assert pipeline.sync(conn, http=None, sources=[]) == []
    recorded = [r["source"] for r in _db.sync_runs_since(conn, 0)]
    assert recorded == [name for name, _fn in pipeline.linking_passes()]


def test_a_failed_source_marks_the_sync_step_failed(nightly, monkeypatch):
    """pipeline.sync RETURNS failures rather than raising — per-source isolation — so
    _run_step only ever saw the ok branch and a night where every feed died still read
    `✅ sync` (#178)."""
    posted = {}
    from toronto_bids import notify
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [("ariba_discovery", "boom")])
    assert nightly() == 1
    assert "❌ sync" in posted["text"]
    assert "✅ sync" not in posted["text"]


def test_a_clean_sync_step_still_reads_ok(nightly, monkeypatch):
    posted = {}
    from toronto_bids import notify
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    nightly()
    assert "✅ sync" in posted["text"]


# --- #176: every nightly step writes a sync_run row, not just the 14 sync sources ---------

def _sync_run_sources(conn):
    from toronto_bids.store import db as _db
    return {r["source"]: r["status"] for r in _db.last_runs(conn)}


class _KeepOpen:
    """`nightly()` closes the connection it's given, same as a real process would on exit.
    sqlite3.Connection is a C type and can't be monkeypatched directly (#176's own tests need
    the SAME pattern `test_a_failure_closing_the_database_does_not_swallow_the_summary` above
    already established) — this proxy forwards everything except `close`, so the test can
    still query `sync_run` afterward."""
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_nightly_records_a_sync_run_row_for_every_step_but_sync(nightly, monkeypatch, conn):
    """Before this, everything past the 14 `tb sync` sources — award summaries, portal, Ariba
    attachments, agencies, export, supplier rebuild — wrote nothing durable, so a step failing
    every night was invisible to `tb status` (#176). "sync" itself is deliberately excluded —
    it already writes 14 rows of its own via pipeline.sync."""
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    nightly()
    sources = _sync_run_sources(conn)
    for step in ("award summaries", "portal", "ariba attachments", "agencies",
                "supplier rebuild", "export"):
        assert sources.get(step) == "ok", sources
    assert "sync" not in sources


def test_council_is_recorded_only_when_it_actually_runs(nightly, monkeypatch, conn):
    """The 1st-of-the-month gate short-circuits before `_run_step` — a skip must not fabricate
    a sync_run row (there is nothing to report a status for)."""
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    nightly()
    assert "council" not in _sync_run_sources(conn)


def test_a_swallowed_agencies_failure_marks_the_step_and_the_sync_run_row_failed(
        nightly, monkeypatch, conn):
    """`_capture_agency_bodies` records a per-body failure into the shared `failures` list
    rather than raising (per-body isolation) — so `_agencies()` itself never raises, and
    `_run_step` alone would read this as ✅ agencies, exactly the #178 pattern one level up.
    The sync_run row must be corrected too, not just the Steps entry — that row is the whole
    point of #176."""
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    monkeypatch.setattr(cli, "_capture_agency_bodies",
                        lambda *a, **k: [("trca", "eSCRIBE unreachable")])
    posted = {}
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    assert nightly() == 1
    assert "❌ agencies" in posted["text"]
    row = conn.execute(
        "SELECT status, error FROM sync_run WHERE source='agencies'").fetchone()
    assert row["status"] == "failed"
    assert "eSCRIBE unreachable" in row["error"]


def test_a_swallowed_portal_failure_marks_the_step_and_the_sync_run_row_failed(
        nightly, monkeypatch, conn):
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    from toronto_bids.sources import bids_tenders
    monkeypatch.setattr(bids_tenders, "run_portal_capture",
                        lambda *a, **k: {"trca": "FAILED: 403 Forbidden"})
    posted = {}
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    assert nightly() == 1
    assert "❌ portal" in posted["text"]
    row = conn.execute(
        "SELECT status, error FROM sync_run WHERE source='portal'").fetchone()
    assert row["status"] == "failed"
    assert "403 Forbidden" in row["error"]


def test_a_step_that_raises_is_recorded_failed_in_sync_run_too(nightly, monkeypatch, conn):
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(ariba_attachments, "capture_attachments",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("browser died")))
    assert nightly() == 1
    row = conn.execute(
        "SELECT status, error FROM sync_run WHERE source='ariba attachments'").fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "browser died"


def test_report_has_a_steps_section_naming_each_step(nightly, monkeypatch):
    posted = {}
    from toronto_bids import notify
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    nightly()
    t = posted["text"]
    assert "*Steps*" in t
    for name in ("sync", "award summaries", "ariba attachments", "agencies", "export"):
        assert name in t


def test_a_failed_step_appears_in_both_failures_and_steps(nightly, monkeypatch):
    posted = {}
    from toronto_bids import notify
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(ariba_attachments, "capture_attachments",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("browser died")))
    assert nightly() == 1
    t = posted["text"]
    assert "*Failures (1)*" in t
    assert "browser died" in t
    assert "❌ ariba attachments" in t


def test_run_step_records_ok_and_isolates_failure():
    from toronto_bids import cli
    steps, failures = [], []
    cli._run_step(steps, failures, "demo", lambda: "+3 things")
    assert steps[0]["status"] == "ok" and steps[0]["detail"] == "+3 things"
    cli._run_step(steps, failures, "boom", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert steps[1]["status"] == "fail" and steps[1]["error"] == "x"
    assert failures == [("boom", "x")]   # failure mirrored for the exit-code contract


def test_council_runs_only_on_the_first_of_the_month(nightly, monkeypatch):
    calls = []
    from toronto_bids.sources import council as council_src
    monkeypatch.setattr(council_src, "enrich_council", lambda *a, **k: calls.append(1) or 0)
    monkeypatch.setattr(cli, "_is_first_of_month", lambda: False)
    nightly()
    assert calls == []            # not the 1st -> council skipped
    monkeypatch.setattr(cli, "_is_first_of_month", lambda: True)
    # `nightly()` closes the connection it was given, same as a real process would on exit —
    # the fixture's `_open_db` always returns the SAME connection object, so a second call in
    # the same test needs a fresh one (#176 gave `_run_step` its first genuinely unmocked SQL
    # write in this path, which is what turned "closed but never actually touched" into a
    # real `ProgrammingError` here).
    from toronto_bids.store import db as _db
    fresh = _db.connect(":memory:")
    _db.init_db(fresh)
    monkeypatch.setattr(cli, "_open_db", lambda: fresh)
    nightly()
    assert calls == [1]           # the 1st -> council runs
