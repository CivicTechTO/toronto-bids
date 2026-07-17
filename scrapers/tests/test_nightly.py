"""`tb nightly` — what the systemd timer calls (deployment spec §3.3).

Every step is isolated the way pipeline.run_source already isolates sources: one failure never
stops the steps behind it. These tests are offline — every network-touching call is patched.
"""
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
    from toronto_bids.sources import award_summary
    monkeypatch.setattr(award_summary, "download_award_summaries", lambda *a, **k: 0)
    monkeypatch.setattr(award_summary, "store_award_summary_bids", lambda *a, **k: 0)
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
