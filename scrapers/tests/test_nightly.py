import sqlite3

import pytest

from toronto_bids import cli, config, notify
from toronto_bids.cli import _capture_agency_bodies as _real_agency_capture
from toronto_bids.sources.bids_tenders import run_portal_capture as _real_portal_capture
from toronto_bids.sources.bonfire import run_bonfire_capture as _real_bonfire_capture
from toronto_bids.sources.merx import run_merx_capture as _real_merx_capture


class _KeepOpen:
    # Proxy sqlite3.Connection so tests can inspect tables after nightly() calls close().
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def nightly(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [])
    monkeypatch.setattr(cli.HttpClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(cli.HttpClient, "close", lambda self: None)
    from toronto_bids.sources import ariba_attachments, award_summary, bids_tenders, bonfire, council as council_src, merx, waterfront
    monkeypatch.setattr(merx, "run_merx_capture", lambda *a, **k: {})
    monkeypatch.setattr(bonfire, "run_bonfire_capture", lambda *a, **k: {})
    monkeypatch.setattr(waterfront, "capture_awards", lambda *a, **k: {"blocks": 0, "failures": []})
    monkeypatch.setattr(award_summary, "download_award_summaries", lambda *a, **k: 0)
    monkeypatch.setattr(award_summary, "store_award_summary_bids", lambda *a, **k: 0)
    monkeypatch.setattr(bids_tenders, "run_portal_capture", lambda *a, **k: {})
    monkeypatch.setattr(ariba_attachments, "capture_attachments", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_is_first_of_month", lambda: False)
    monkeypatch.setattr(council_src, "enrich_council", lambda *a, **k: 0)
    monkeypatch.setattr(notify, "post", lambda *a, **k: False)
    return lambda: cli.main(["nightly"])


def _sync_run_sources(conn):
    from toronto_bids.store import db as _db
    return {r["source"]: r["status"] for r in _db.last_runs(conn)}


def test_clean_run_exports_records_sync_runs_and_posts_summary(nightly, monkeypatch, conn, tmp_path):
    from toronto_bids.sources import award_summary

    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    seen = {}

    def clean_store(c, log=lambda _m: None, failures=None):
        seen["failures"] = failures
        return 0

    monkeypatch.setattr(award_summary, "store_award_summary_bids", clean_store)
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)

    assert nightly() == 0
    assert seen["failures"] is not None
    for name in ("bids.json", "schema.json", "bids-csv.zip", "solicitation.parquet"):
        assert (tmp_path / "export" / name).exists()

    assert len(posted) == 1
    text = posted[0]
    assert text.startswith("*✅ toronto-bids nightly*")
    assert "*Steps*" in text
    assert "✅ sync" in text
    assert "❌ award summaries" not in text
    for name in ("sync", "award summaries", "ariba attachments", "agencies", "export"):
        assert name in text

    sources = _sync_run_sources(conn)
    for step in ("award summaries", "portal", "merx", "ariba attachments", "agencies", "supplier rebuild", "export"):
        assert sources.get(step) == "ok", sources
    assert "sync" not in sources
    assert "council" not in sources


def test_failed_sync_source_marks_step_posts_failure_and_still_exports(nightly, monkeypatch, tmp_path):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [("ariba_discovery", "boom")])

    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()
    assert len(posted) == 1
    text = posted[0]
    assert text.startswith("*❌ toronto-bids nightly*")
    assert "*Failures" in text and "ariba_discovery" in text
    assert "❌ sync" in text and "✅ sync" not in text


@pytest.mark.parametrize(
    "step_name, patch_kind, err_msg, sync_run_source",
    [
        ("sync", "sync", "pipeline exploded", None),
        ("award summaries", "award_summary", "portal down", "award summaries"),
        ("portal", "portal", "portal down", "portal"),
        ("merx", "merx", "MERX unavailable", "merx"),
        ("ariba attachments", "ariba", "browser died", "ariba attachments"),
        ("agencies", "agencies", "tmmis blocked", "agencies"),
        (None, "http", "no http for you", None),
    ],
)
def test_raising_step_is_isolated_recorded_and_still_exports(
    nightly, monkeypatch, conn, tmp_path, step_name, patch_kind, err_msg, sync_run_source
):
    from toronto_bids.sources import ariba_attachments, award_summary, bids_tenders, merx

    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))

    def boom(*a, **k):
        raise RuntimeError(err_msg)

    targets = {
        "sync": (cli.pipeline, "sync"),
        "award_summary": (award_summary, "download_award_summaries"),
        "portal": (bids_tenders, "run_portal_capture"),
        "merx": (merx, "run_merx_capture"),
        "ariba": (ariba_attachments, "capture_attachments"),
        "agencies": (cli, "_capture_agency_bodies"),
        "http": (cli.HttpClient, "__init__"),
    }
    monkeypatch.setattr(*targets[patch_kind], boom)

    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)

    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()
    assert len(posted) == 1
    text = posted[0]
    assert "*Failures (1)*" in text and err_msg in text
    if step_name is not None:
        assert f"❌ {step_name}" in text
    if sync_run_source is not None:
        row = conn.execute(
            "SELECT status, error FROM sync_run WHERE source=?", (sync_run_source,)
        ).fetchone()
        assert row["status"] == "failed" and row["error"] == err_msg
        assert conn.execute("SELECT status FROM sync_run WHERE source='export'").fetchone()[0] == "ok"
        if sync_run_source == "merx":
            assert conn.execute("SELECT status FROM sync_run WHERE source='ariba attachments'").fetchone()[0] == "ok"


@pytest.mark.parametrize(
    "step_name, patch_kind, expected_in_error, expected_in_slack",
    [
        ("agencies", "agencies", ("eSCRIBE unreachable",), ()),
        ("portal", "portal", ("403 Forbidden",), ()),
        ("ariba attachments", "ariba", ("ariba:Doc1234567890", "content tree never resolved"), ()),
        ("award summaries", "award_summary", ("extract:award_summary", "3 of 3 documents failed extraction"), ()),
        ("waterfront awards", "waterfront", ("supplier not published",), ()),
        ("bonfire listings", "bonfire", ("retry deferred",), ()),
        ("merx", "merx", ("changing total",), ("879704954", "legacy detail requires entitlement")),
    ],
)
def test_swallowed_step_failure_marks_step_and_sync_run_failed(
    nightly, monkeypatch, conn, tmp_path, step_name, patch_kind, expected_in_error, expected_in_slack
):
    from toronto_bids.sources import ariba_attachments, award_summary, bids_tenders, bonfire, merx, waterfront

    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))

    if patch_kind == "agencies":
        monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [("trca", "eSCRIBE unreachable")])
    elif patch_kind == "portal":
        monkeypatch.setattr(
            bids_tenders, "run_portal_capture", lambda *a, **k: {"trca": "FAILED: 403 Forbidden", "toronto-zoo": 0}
        )
    elif patch_kind == "ariba":
        def swallowing_capture(c, log=lambda _m: None, failures=None, **k):
            if failures is not None:
                failures.append(("ariba:Doc1234567890", "content tree never resolved"))
            return 0
        monkeypatch.setattr(ariba_attachments, "capture_attachments", swallowing_capture)
    elif patch_kind == "award_summary":
        def swallowing_store(c, log=lambda _m: None, failures=None):
            if failures is not None:
                failures.append(("extract:award_summary", "3 of 3 documents failed extraction"))
            return 0
        monkeypatch.setattr(award_summary, "store_award_summary_bids", swallowing_store)
    elif patch_kind == "waterfront":
        monkeypatch.setattr(
            waterfront,
            "capture_awards",
            lambda *a, **k: {"blocks": 5, "failures": [("waterfront:2023-03", "supplier not published")]},
        )
    elif patch_kind == "bonfire":
        monkeypatch.setattr(
            bonfire,
            "run_bonfire_capture",
            lambda *a, **k: {"toronto-public-library": "FAILED: rate limited; retry deferred"},
        )
    elif patch_kind == "merx":
        monkeypatch.setattr(
            merx,
            "run_merx_capture",
            lambda *a, **k: {
                "toronto-transit-commission": dict(
                    listings=25,
                    failures=["changing total"],
                    detail_gaps=[dict(notice_id="879704954", reason="legacy detail requires entitlement")],
                )
            },
        )

    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)

    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()
    assert f"❌ {step_name}" in posted[0]
    for needle in expected_in_slack:
        assert needle in posted[0]
    row = conn.execute("SELECT status, error FROM sync_run WHERE source=?", (step_name,)).fetchone()
    assert row["status"] == "failed"
    for needle in expected_in_error:
        assert needle in row["error"]


def test_a_broken_database_still_reports_to_slack(nightly, monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)

    def boom():
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(cli, "_open_db", boom)
    assert nightly() == 1
    assert posted and posted[0].startswith("*❌ toronto-bids nightly*")
    assert "unable to open database file" in posted[0]


@pytest.mark.parametrize("fail_mode, message", [("always", "database disk image is malformed"), ("first_only", "locked")])
def test_counts_failure_reports_to_slack_under_counts_label(nightly, monkeypatch, fail_mode, message):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    calls = {"n": 0}
    real_counts = cli.db.counts

    def flaky(c):
        calls["n"] += 1
        if fail_mode == "always" or calls["n"] == 1:
            raise sqlite3.DatabaseError(message)
        return real_counts(c)

    monkeypatch.setattr(cli.db, "counts", flaky)
    assert nightly() == 1
    assert posted and posted[0].startswith("*❌ toronto-bids nightly*")
    assert f"counts: {message}" in posted[0]
    assert "(+" not in posted[0]


def test_a_failure_closing_the_database_does_not_swallow_the_summary(nightly, monkeypatch, conn):
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


def test_report_sources_excludes_linking_passes_and_validator(conn):
    from toronto_bids.store import db as _db
    for src, fetched in (("schema_check", 0), ("odata_solicitations", 7446),
                         ("supplier_dimension", 8020), ("title_cleanup", 0)):
        rid = _db.start_sync_run(conn, src)
        _db.finish_sync_run(conn, rid, status="ok", rows_fetched=fetched, rows_upserted=fetched)
    names = [r["source"] for r in cli._report_sources(conn, 0)]
    assert "odata_solicitations" in names
    assert "schema_check" not in names
    assert "supplier_dimension" not in names
    assert "title_cleanup" not in names


def _record(conn, source, status="ok"):
    from toronto_bids.store import db as _db
    rid = _db.start_sync_run(conn, source)
    _db.finish_sync_run(conn, rid, status=status, error=None if status == "ok" else "boom")


def test_sync_detail_counts_every_unit_the_run_recorded(conn):
    from toronto_bids import pipeline
    _record(conn, "schema_check")
    for s in pipeline.default_sources():
        if s.name != "schema_check":
            _record(conn, s.name)
    for name, _fn in pipeline.linking_passes():
        _record(conn, name)

    detail = cli._sync_detail(conn, 0)
    assert detail == "schema check · 8 sources · 6 passes"
    assert 1 + 8 + 6 == len(cli.db.sync_runs_since(conn, 0)) == 15


def test_sync_detail_names_a_failure_wherever_it_happened(conn):
    _record(conn, "schema_check")
    _record(conn, "odata_solicitations")
    _record(conn, "supplier_dimension", status="failed")
    assert cli._sync_detail(conn, 0) == "schema check · 1 source · 1 pass · 1 FAILED"


def test_sync_detail_says_so_when_the_run_recorded_nothing(conn):
    assert cli._sync_detail(conn, 0) == "nothing recorded"


def test_linking_passes_is_the_list_sync_actually_runs(conn):
    from toronto_bids import pipeline
    from toronto_bids.store import db as _db
    assert pipeline.sync(conn, http=None, sources=[]) == []
    recorded = [r["source"] for r in _db.sync_runs_since(conn, 0)]
    assert recorded == [name for name, _fn in pipeline.linking_passes()]


def test_agency_store_extraction_errors_reach_the_agencies_failures(monkeypatch, conn):
    from toronto_bids.sources import ep_board, trca_board, zoo_board

    def failing_store(corpus):
        def store(c, log=lambda _m: None, failures=None):
            failures.append((f"extract:{corpus}", "1 of 2 documents failed extraction"))
            return {"solicitations": 0, "awards": 0, "bids": 0}
        return store

    monkeypatch.setattr(trca_board, "store_trca_reports", failing_store("trca"))
    monkeypatch.setattr(zoo_board, "store_zoo_reports", failing_store("zoo"))
    monkeypatch.setattr(ep_board, "store_ep_reports", failing_store("ep"))
    monkeypatch.setattr(zoo_board, "cached_zb_agendas", lambda: [])
    monkeypatch.setattr(ep_board, "cached_ep_agendas", lambda: [])
    got = cli._capture_agency_bodies(
        conn, bodies=["trca", "zoo", "ep"], fetch=False, scrape=False,
        virtual_display=False, out=lambda _m: None)
    assert [name for name, _ in got] == ["extract:trca", "extract:zoo", "extract:ep"]


def test_real_identity_refusal_marks_nightly_and_history_incomplete(
        nightly, monkeypatch, conn, tmp_path):
    from tests.test_agency_extraction_fields import cache, contract
    from toronto_bids.sources import ep_board, trca_board, zoo_board
    import toronto_bids.extract as extract

    cache(conn, "trca", [contract("10008808"), contract(None)])
    monkeypatch.setattr(extract, "ExtractionClient", lambda: (_ for _ in ()).throw(ValueError("no key")))
    monkeypatch.setattr(trca_board, "download_reports", lambda *a, **k: 0)
    monkeypatch.setattr(zoo_board, "scrape_zb_agendas", lambda **k: {})
    monkeypatch.setattr(ep_board, "scrape_ep_agendas", lambda **k: {})
    monkeypatch.setattr(cli, "_capture_agency_bodies", _real_agency_capture)
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    posted = {}
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    assert nightly() == 1
    assert "❌ agencies" in posted["text"]
    assert "identity:trca" in posted["text"]
    row = conn.execute("SELECT status,error FROM sync_run WHERE source='agencies'").fetchone()
    assert row["status"] == "failed"
    assert "identity:trca" in row["error"]
    assert "download:trca" not in row["error"]
    assert conn.execute("SELECT count(*) FROM agency_derivation WHERE reason IS NOT NULL").fetchone()[0] == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_run_step_records_ok_and_isolates_failure():
    steps, failures = [], []
    cli._run_step(steps, failures, "demo", lambda: "+3 things")
    assert steps[0]["status"] == "ok" and steps[0]["detail"] == "+3 things"
    cli._run_step(steps, failures, "boom", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert steps[1]["status"] == "fail" and steps[1]["error"] == "x"
    assert failures == [("boom", "x")]


def test_council_runs_only_on_the_first_of_the_month(nightly, monkeypatch):
    calls = []
    from toronto_bids.sources import council as council_src
    monkeypatch.setattr(council_src, "enrich_council", lambda *a, **k: calls.append(1) or 0)
    monkeypatch.setattr(cli, "_is_first_of_month", lambda: False)
    nightly()
    assert calls == []
    monkeypatch.setattr(cli, "_is_first_of_month", lambda: True)
    from toronto_bids.store import db as _db
    fresh = _db.connect(":memory:")
    _db.init_db(fresh)
    monkeypatch.setattr(cli, "_open_db", lambda: fresh)
    nightly()
    assert calls == [1]


def _rebuilding_store(corpus):
    def store(c, log=lambda _m: None, failures=None):
        c.execute("DELETE FROM bid WHERE source='award_summary'")
        for i, name in enumerate(corpus):
            c.execute(
                "INSERT INTO bid (bidder_name_raw, document_number, source) "
                "VALUES (?, ?, 'award_summary')",
                (name, f"{i:010d}"),
            )
        c.commit()
        return len(corpus)
    return store


def _award_detail(text):
    return next(line for line in text.splitlines() if "award summaries" in line)


def _two_nights(nightly, monkeypatch, conn, corpus, between=lambda: None):
    from toronto_bids.sources import award_summary
    monkeypatch.setattr(award_summary, "store_award_summary_bids", _rebuilding_store(corpus))
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    posted: list[str] = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text))
    nightly()
    between()
    nightly()
    return [_award_detail(t) for t in posted]


def test_award_summary_step_reports_the_delta_not_the_corpus_total(nightly, monkeypatch, conn):
    first, second = _two_nights(
        nightly, monkeypatch, conn, ["Alpha Co.", "Beta Ltd.", "Gamma Inc."]
    )
    assert "+3 bids (3 total)" in first
    assert "+0 bids (3 total)" in second
    assert "bids stored" not in second


def test_award_summary_step_renders_a_shrinking_rebuild_as_negative(nightly, monkeypatch, conn):
    corpus = ["Alpha Co.", "Beta Ltd.", "Gamma Inc."]
    _, second = _two_nights(nightly, monkeypatch, conn, corpus, between=corpus.pop)
    assert "-1 bids (2 total)" in second


def test_bid_delta_is_signed():
    assert cli._bid_delta(5, 5) == "+0 bids (5 total)"
    assert cli._bid_delta(5, 7) == "+2 bids (7 total)"
    assert cli._bid_delta(7, 5) == "-2 bids (5 total)"


def test_near_close_postings_reach_slack_without_failing_the_run(nightly, monkeypatch):
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(ariba_attachments, "near_close_uncaptured", lambda c: [
        {"document_number": "5713434353", "closes": "2026-10-03", "days_left": 2}])
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    assert nightly() == 0
    assert posted[0].startswith("*✅ toronto-bids nightly*")
    assert "Doc5713434353 · closes 2026-10-03 · 2 days left" in posted[0]


def test_a_raising_near_close_check_is_reported_but_does_not_fail_the_run(nightly, monkeypatch):
    from toronto_bids.sources import ariba_attachments

    def boom(c):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(ariba_attachments, "near_close_uncaptured", boom)
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    assert nightly() == 0
    assert "Ariba near-close check failed: disk gone" in posted[0]


def _run_trca_downloads(nightly, monkeypatch, conn, tmp_path, ids):
    import httpx
    from toronto_bids.sources import trca_board

    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    monkeypatch.setattr(config, "TRCA_ESCRIBE_YEARS", [])
    monkeypatch.setattr(config, "TRCA_REPORTS_DIR", tmp_path / "trca")
    urls = [f"https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId={n}" for n in ids]
    for url in urls:
        conn.execute("INSERT INTO background_pdf (url, kind) VALUES (?, 'agency_board')", (url,))
    conn.commit()

    def missing(self, url):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("404", request=request,
                                    response=httpx.Response(404, request=request))

    monkeypatch.setattr(cli.HttpClient, "get_bytes", missing)
    monkeypatch.setattr(trca_board, "store_trca_reports", lambda *a, **k:
                        {"solicitations": 0, "awards": 0, "bids": 0})
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda c, **k:
                        _real_agency_capture(c, bodies=["trca"], fetch=True, scrape=False,
                                             virtual_display=False, out=k["out"]))
    posted = {}
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    return nightly(), posted["text"], urls


def test_trca_download_gaps_reach_nightly_status_history_and_summary(
        nightly, monkeypatch, conn, tmp_path):
    code, text, urls = _run_trca_downloads(nightly, monkeypatch, conn, tmp_path,
                                           (99999991, 99999992, 5558))
    new, reviewed = urls[:2], urls[2]
    assert code == 1
    assert "❌ agencies" in text and "1 known gaps" in text
    failures, known = text.split("*Failures (2)*")[1], text.split("*Known gaps (1)*")[1]
    assert all(url in failures for url in new) and reviewed not in failures
    assert "download:trca: reviewed unavailable TRCA original (#259)" in known
    row = conn.execute("SELECT status, error FROM sync_run WHERE source='agencies'").fetchone()
    assert row["status"] == "failed" and all(url in row["error"] for url in new)
    assert reviewed not in row["error"]
    assert (tmp_path / "export" / "bids.json").exists()
    assert conn.execute("SELECT count(*) FROM background_pdf WHERE sha256 IS NULL").fetchone()[0] == 3


def test_only_reviewed_trca_gaps_leave_the_nightly_green_and_queued(
        nightly, monkeypatch, conn, tmp_path):
    code, text, urls = _run_trca_downloads(nightly, monkeypatch, conn, tmp_path,
                                           (5558, 7234, 6819))
    assert code == 0
    assert text.startswith("*✅ toronto-bids nightly*")
    assert "✅ agencies" in text and "3 known gaps" in text
    assert "*Known gaps (3)*" in text and "download:trca ×3:" in text
    assert "Failures" not in text
    row = conn.execute("SELECT status, error FROM sync_run WHERE source='agencies'").fetchone()
    assert row["status"] == "ok" and row["error"] is None
    assert conn.execute("SELECT count(*) FROM background_pdf WHERE sha256 IS NULL").fetchone()[0] == 3


def test_verified_training_restrictions_are_explicit_not_false_nightly_failures(
        nightly, monkeypatch, conn):
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))

    def capture(c, *, training_restrictions, **kwargs):
        training_restrictions.append({"document_number": "3861692164", "rfx_id": "15346406",
                                      "reason": "public_sector_disabled",
                                      "detail": "Doc3861692164: public sector sourcing is not enabled"})
        return 0

    monkeypatch.setattr(ariba_attachments, "capture_attachments", capture)
    posted = {}
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    assert nightly() == 0
    assert "✅ ariba attachments" in posted["text"]
    assert "1 training restrictions" in posted["text"]
    assert "*Training event restrictions (1)*" in posted["text"]
    assert "Doc3861692164: public sector sourcing is not enabled" in posted["text"]
    assert conn.execute("SELECT status FROM sync_run WHERE source='ariba attachments'").fetchone()[0] == "ok"


def test_real_event_failure_is_preserved_alongside_training_restriction_notices(
        nightly, monkeypatch, conn):
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))

    def capture(c, *, failures, training_restrictions, **kwargs):
        training_restrictions.append({"document_number": "3861744322", "rfx_id": "15346471",
                                      "reason": "closed", "detail": "Doc3861744322: event is closed"})
        failures.append(("ariba:Doc5999999999", "Ariba denied permission to view the event"))
        return 0

    monkeypatch.setattr(ariba_attachments, "capture_attachments", capture)
    posted = {}
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    assert nightly() == 1
    assert "❌ ariba attachments" in posted["text"]
    assert "*Training event restrictions (1)*" in posted["text"]
    assert "ariba:Doc5999999999" in posted["text"]
    row = conn.execute("SELECT status,error FROM sync_run WHERE source='ariba attachments'").fetchone()
    assert row["status"] == "failed" and "ariba:Doc5999999999" in row["error"]


def test_real_portal_refresh_reaches_nightly_history_and_export(nightly, monkeypatch, conn, tmp_path):
    import json
    from pathlib import Path
    import httpx
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.sources import bids_tenders as bt

    fixtures = Path(__file__).parent / "fixtures/agencies"
    rows = {
        "trca": json.loads((fixtures / "bids_tenders_trca_identity_2026-10-22.json").read_text()),
        "toronto-zoo": json.loads((fixtures / "bids_tenders_zoo_2026-10-21.json").read_text()),
    }
    control = next(r for r in rows["toronto-zoo"] if r["Id"] == "6fc2f901-6847-469e-b630-d09fc5e75cb9")
    bt.store_listings(conn, [dict(control, Status="Open")], seed_buyers(conn))
    old = dict(conn.execute("SELECT * FROM portal_listing").fetchone())
    real_client = httpx.Client
    requests = []

    def respond(request):
        slug = "toronto-zoo" if request.url.host.startswith("torontozoo") else "trca"
        if request.method == "GET":
            return httpx.Response(200, text='<input id="NodeId" value="offline-node">'
                                  '<input name="__RequestVerificationToken" value="offline-token">')
        requests.append((slug, int(request.url.params["start"])))
        assert request.url.params["status"] == ""
        start = int(request.url.params["start"])
        limit = int(request.url.params["limit"])
        return httpx.Response(200, json={"success": True, "total": len(rows[slug]), "data": rows[slug][start:start + limit]})

    monkeypatch.setattr(bt.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(bt.time, "sleep", lambda _: None)
    monkeypatch.setattr(bt, "run_portal_capture", _real_portal_capture)
    monkeypatch.setattr(config, "PORTAL_RECORDINGS_DIR", tmp_path / "recordings")
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    assert nightly() == 0
    current = dict(conn.execute("SELECT * FROM portal_listing WHERE id=?", (old["id"],)).fetchone())
    assert current["status"] == "Awarded"
    assert (current["procurement_id"], current["first_seen"]) == (old["procurement_id"], old["first_seen"])
    assert conn.execute("SELECT status FROM sync_run WHERE source='portal' ORDER BY id DESC LIMIT 1").fetchone()[0] == "ok"
    assert len(requests) == 8
    assert len(list((tmp_path / "recordings").glob("*.json"))) == 371
    exported = json.loads((tmp_path / "export/bids.json").read_text())
    zoo = next(b for b in exported["buyers"] if b["slug"] == "toronto-zoo")
    assert len(zoo["portal_listings"]) == 122 and zoo["awards"] == []
    assert next(p for p in zoo["portal_listings"] if p["portal_url"].endswith(control["Id"]))["status"] == "Awarded"


def test_real_bonfire_capture_reaches_nightly_history_and_bulk_exports(nightly, monkeypatch, conn, tmp_path):
    import csv
    import io
    import json
    import zipfile
    from datetime import datetime, timezone
    from pathlib import Path
    import httpx
    import pyarrow.parquet as pq
    from toronto_bids.sources import bonfire

    fixtures = Path(__file__).parent / "fixtures/bonfire"
    bodies = {
        host: {"/portal/": (fixtures / f"{prefix}-portal.html").read_bytes(),
               bonfire.SECTIONS["open"]: (fixtures / f"{prefix}-open.json").read_bytes(),
               bonfire.SECTIONS["past"]: (fixtures / f"{prefix}-past.json").read_bytes()}
        for host, prefix in [("torontopubliclibrary.bonfirehub.ca", "library"),
                             ("torontohousing.bonfirehub.ca", "housing"),
                             ("waterfrontoronto.bonfirehub.ca", "waterfront"),
                             ("explace.bonfirehub.ca", "exhibition"),
                             ("taf.bonfirehub.ca", "taf")]
    }
    bodies["ttc.bonfirehub.ca"] = {
        "/portal/": (fixtures / "ttc-portal.html").read_bytes(),
        bonfire.SECTIONS["open"]: (fixtures / "ttc-open.json").read_bytes(),
    }
    requests, waits = [], []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=bodies[request.url.host][request.url.path])

    actual_capture = bonfire.capture_portal

    def capture(c, portal):
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            return actual_capture(c, portal, client=client, sleep=waits.append,
                                  clock=lambda: datetime(2026, 10, 24, 4, tzinfo=timezone.utc))

    monkeypatch.setattr(bonfire, "capture_portal", capture)
    monkeypatch.setattr(bonfire, "run_bonfire_capture", _real_bonfire_capture)
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    assert nightly() == 0
    assert len(requests) == 17 and waits == [1] * 17
    assert conn.execute("SELECT status FROM sync_run WHERE source='bonfire listings'").fetchone()[0] == "ok"
    export = tmp_path / "export"
    doc = json.loads((export / "bids.json").read_text())
    library = next(b for b in doc["buyers"] if b["slug"] == "toronto-public-library")
    assert len(library["portal_listings"]) == 412 and library["awards"] == library["bids"] == []
    housing = next(b for b in doc["buyers"] if b["slug"] == "toronto-community-housing")
    assert len(housing["portal_listings"]) == 961 and housing["awards"] == housing["bids"] == []
    for slug, count in [("waterfront-toronto", 78), ("exhibition-place", 94),
                        ("the-atmospheric-fund", 16), ("toronto-transit-commission", 14)]:
        buyer = next(b for b in doc["buyers"] if b["slug"] == slug)
        assert len(buyer["portal_listings"]) == count and buyer["awards"] == buyer["bids"] == []
    assert pq.read_metadata(export / "portal_listing.parquet").num_rows == 1575
    with zipfile.ZipFile(export / "bids-csv.zip") as z:
        assert len(list(csv.DictReader(io.StringIO(z.read("portal_listing.csv").decode())))) == 1575


def test_nightly_batches_uncached_work_and_reports_pending(nightly, monkeypatch, capsys, tmp_path):
    import httpx
    from toronto_bids.batch_extraction import ExtractionPending, batching_enabled
    from toronto_bids.extract import ExtractionClient
    from toronto_bids.sources import award_summary

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("TB_CLAUDE_MODE", "batch")
    monkeypatch.setenv("TB_LLM_PROVIDER", "anthropic")
    requests = []

    def post(url, **kwargs):
        assert url.endswith("/messages/batches")
        requests.append(kwargs["json"])
        return httpx.Response(200, json={"id": "msgbatch_nightly"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)

    def store(*args, **kwargs):
        assert batching_enabled()
        try:
            ExtractionClient().extract("Uncached report")
        except ExtractionPending:
            pass
        return 0

    monkeypatch.setattr(award_summary, "store_award_summary_bids", store)
    assert nightly() == 0
    assert len(requests) == 1
    text = capsys.readouterr().out
    assert "⏳" in text and "Extraction pending" in text
    assert (tmp_path / "export/bids.json").exists()
    assert not batching_enabled()


def test_nightly_merx_counts_actual_bid_and_award_growth(nightly, monkeypatch, conn):
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.models import AgencyAward, AgencyBid
    from toronto_bids.sources import merx

    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    buyer = seed_buyers(conn)["toronto-transit-commission"]

    def capture(*a, **k):
        cli.db.upsert_row(conn, AgencyBid(buyer_id=buyer, native_ref="page", bidder_name_raw="Bidder", source="merx"), overwrite=True)
        cli.db.upsert_row(conn, AgencyAward(buyer_id=buyer, native_ref="page", supplier_name_raw="Awardee", source="merx"), overwrite=True)
        conn.commit()
        return {"toronto-transit-commission": dict(listings=25, failures=[], detail_gaps=[])}

    monkeypatch.setattr(merx, "run_merx_capture", capture)
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text))
    assert nightly() == 0
    assert "25 listings · +1 bids · +1 awards" in posted[0]
    posted.clear()
    assert nightly() == 0
    assert "25 listings · +0 bids · +0 awards" in posted[0]


def test_real_merx_capture_reaches_nightly_history_and_export(nightly, monkeypatch, conn, tmp_path):
    import json
    import re
    from pathlib import Path
    import httpx
    from lxml import html
    from toronto_bids.sources import merx

    fixtures = Path(__file__).parent / "fixtures/merx"
    portal = config.MERX_PORTALS[0]
    monkeypatch.setattr(config, "MERX_PORTALS", [portal])
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))

    def listing(name, keep):
        tree = html.fromstring((fixtures / (name + ".html")).read_bytes())
        for a in tree.xpath('//a[contains(@class,"solicitation-link")]'):
            if not keep or not a.get("href").split("?")[0].endswith("/" + keep):
                a.xpath("ancestor::tr[1]")[0].drop_tree()
        count = 1 if keep else 0
        label = {"ttc": "Open Solicitations", "closed": "Closed Solicitations",
                 "bidresults": "Bid Results", "awarded": "Awarded Solicitations"}[name]
        nodes = tree.xpath('//*[not(*) and contains(text(),"' + label + '")]')
        headings = [e for e in nodes if re.search(r"\d[\d,]*\s+" + label, e.text or "")]
        assert len(headings) == 1
        headings[0].text = str(count) + " " + label
        return html.tostring(tree)

    pages = {"open-bids": listing("ttc", None), "closed-bids": listing("closed", "0000333265"),
             "bidresults-bids": listing("bidresults", "0000333265"),
             "awarded-bids": listing("awarded", "0000331101")}
    calls = []

    def handle(request):
        path = request.url.path
        calls.append(path)
        if path.rsplit("/", 1)[-1] in pages:
            return httpx.Response(200, content=pages[path.rsplit("/", 1)[-1]])
        if path.endswith("/0000333265"):
            name = "result-detail.html"
        elif path.endswith("/0000331101"):
            name = "award-detail.html"
        elif path.endswith("/4109202665/abstract/bid-results"):
            name = "bid-fragment.js"
        elif path.endswith("/4076848809/abstract/bid-results"):
            name = "award-bid-fragment.js"
        elif path.endswith("/4076848809/abstract/award"):
            name = "award-fragment.js"
        else:
            raise AssertionError("Unexpected MERX request: " + path)
        return httpx.Response(200, content=(fixtures / name).read_bytes())

    actual = merx.capture_portal
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(merx, "capture_portal", lambda c, p, **kw:
            actual(c, p, client=client, sleep=lambda n: None, **kw))
        monkeypatch.setattr(merx, "run_merx_capture", _real_merx_capture)
        assert nightly() == 0
    assert len(calls) == 9
    assert conn.execute("SELECT status FROM sync_run WHERE source='merx'").fetchone()[0] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM agency_bid WHERE source='merx'").fetchone()[0] == 15
    assert conn.execute("SELECT COUNT(*) FROM agency_award WHERE source='merx'").fetchone()[0] == 1
    doc = json.loads((tmp_path / "export/bids.json").read_text())
    buyer = next(b for b in doc["buyers"] if b["slug"] == portal["slug"])
    assert len(buyer["portal_listings"]) == 2 and len(buyer["bids"]) == 15 and len(buyer["awards"]) == 1
    assert {r["result_stage"] for r in buyer["bids"]} == {"Unofficial Results"}
    assert buyer["awards"][0]["award_date"] == "2026-10-05"
    assert buyer["awards"][0]["publication_date"] == "2026-10-08"


def test_nightly_surfaces_rejected_award_summary_downloads(nightly, conn, monkeypatch, capsys):
    from toronto_bids.sources import award_summary

    def reject_one(_conn, _http, *, log=lambda _m: None, failures=None):
        if failures is not None:
            failures.append(("award_summary_download:maint001", "Response is not a PDF"))
        return 1

    monkeypatch.setattr(award_summary, "download_award_summaries", reject_one)
    monkeypatch.setattr(cli, "_open_db", lambda: _KeepOpen(conn))
    assert nightly() == 1
    out = capsys.readouterr().out
    assert "award_summary_download:maint001" in out
    status = conn.execute(
        "SELECT status FROM sync_run WHERE source='award summaries' ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert status == "failed"
