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
    monkeypatch.setattr(notify, "post", lambda *a, **k: False)
    return lambda: cli.main(["nightly"])


def test_a_clean_run_exits_zero_and_writes_the_export(nightly, tmp_path):
    assert nightly() == 0
    assert (tmp_path / "export" / "bids.json").exists()


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
    assert posted[0].startswith("✅ toronto-bids")


def test_a_failing_run_posts_the_failure(nightly, monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [("ariba_discovery", "boom")])
    assert nightly() == 1
    assert posted[0].startswith("❌ toronto-bids")
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
    assert posted and posted[0].startswith("❌ toronto-bids")
    assert "unable to open database file" in posted[0]


def test_counting_the_archive_failing_still_reports_to_slack(nightly, monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    def boom(_conn):
        raise sqlite3.DatabaseError("database disk image is malformed")
    monkeypatch.setattr(cli.db, "counts", boom)
    assert nightly() == 1
    assert posted and posted[0].startswith("❌ toronto-bids")


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
    assert posted and posted[0].startswith("❌ toronto-bids")
