from contextlib import contextmanager
import json
import pathlib
from types import SimpleNamespace

import httpx
import pytest

from toronto_bids import cli, config
from toronto_bids.sources import trca_board as trca
from toronto_bids.sources.trca_board import (
    _prune_page_assets,
    escribe_asset_urls,
    escribe_document_urls,
    meeting_detail_urls,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"
BASE = "https://pub-trca.escribemeetings.com/"
FILESTREAM_BASE = BASE + "filestream.ashx?DocumentId="
PDF = (FIXTURES / "trca_20812.pdf").read_bytes()
CHALLENGE = b"<html>Verifying your browser - eScribe</html>"


def _read(name):
    return (FIXTURES / name).read_text()


def test_escribe_document_urls_extracts_filestream_links():
    html = _read("trca_escribe_2023.html")
    urls = escribe_document_urls(html)
    assert urls
    assert all(u.startswith(BASE) for u in urls)


def test_meeting_detail_urls_from_calendar_json():
    cal = json.loads(_read("trca_getcalendarmeetings_2023q1.json"))
    urls = meeting_detail_urls(cal)
    assert len(urls) == 5
    assert all("Meeting.aspx?Id=" in u for u in urls)
    assert all(u.startswith(BASE) for u in urls)
    assert any("82fa331c-e7cb-4e9a-87e2-093a1a51899f" in u for u in urls)


def test_a_stylesheet_and_logo_served_through_filestream_are_not_documents():
    html = _read("trca_escribe_meeting_assets.html")
    docs = escribe_document_urls(html)
    assert [u.rsplit("=", 1)[-1] for u in docs] == ["3216", "3235", "3236"]
    assert not any("3253" in u or "3251" in u for u in docs)
    assert sorted(u.rsplit("=", 1)[-1] for u in escribe_asset_urls(html)) == ["3251", "3253"]


def test_href_is_not_always_the_first_attribute_of_a_document_anchor():
    urls = escribe_document_urls(
        "<a class='Link' tabindex='15' href='filestream.ashx?DocumentId=99'>r.pdf</a>"
    )
    assert urls == [FILESTREAM_BASE + "99"]


def test_escribe_document_urls_decodes_html_entities():
    page = (
        '<a href="https&#58;//pub-trca.escribemeetings.com/'
        'FileStream.ashx?DocumentId=10661&amp;lang=en">report</a>'
    )
    assert escribe_document_urls(page) == [
        BASE + "FileStream.ashx?DocumentId=10661&lang=en"
    ]


def test_prune_unqueues_page_assets_but_never_a_document_or_held_bytes(conn):
    asset = BASE + "FileStream.ashx?DocumentId=3253"
    dead = BASE + "FileStream.ashx?DocumentId=5558"
    held_asset = BASE + "FileStream.ashx?DocumentId=7777"
    for url in (asset, dead):
        conn.execute(
            "INSERT INTO background_pdf (url, kind) VALUES (?, 'agency_board')", (url,)
        )
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256) VALUES (?, 'agency_board', 'x')",
        (held_asset,),
    )
    conn.commit()

    assert _prune_page_assets(conn, {asset, held_asset}, lambda _m: None) == 1
    left = {r[0] for r in conn.execute("SELECT url FROM background_pdf").fetchall()}
    assert left == {dead, held_asset}


def test_prune_does_nothing_when_no_page_loaded(conn):
    conn.execute(
        "INSERT INTO background_pdf (url, kind) VALUES "
        "('https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=1', 'agency_board')"
    )
    conn.commit()
    assert _prune_page_assets(conn, set(), lambda _m: None) == 0
    assert conn.execute("SELECT COUNT(*) FROM background_pdf").fetchone()[0] == 1


def _prepare_capture(conn, monkeypatch, tmp_path, replies):
    @contextmanager
    def blocked_browser():
        yield lambda url: {"status": 403, "url": url, "body": b"Forbidden"}

    monkeypatch.setattr(trca, "_browser_fetch", blocked_browser)
    monkeypatch.setattr(config, "TRCA_ESCRIBE_YEARS", [])
    monkeypatch.setattr(config, "TRCA_REPORTS_DIR", tmp_path / "reports")
    extraction_calls = []
    monkeypatch.setattr(
        trca,
        "store_trca_reports",
        lambda *a, **k: extraction_calls.append(True) or {"solicitations": 0, "awards": 0},
    )

    class Http:
        def get_bytes(self, url):
            reply = replies[url]
            if isinstance(reply, Exception):
                raise reply
            return reply

        def close(self):
            pass

    monkeypatch.setattr(cli, "HttpClient", Http)
    for url in replies:
        conn.execute("INSERT INTO background_pdf (url, kind) VALUES (?, 'agency_board')", (url,))
    conn.commit()
    return extraction_calls


def _capture(conn, out=lambda _m: None):
    return cli._capture_agency_bodies(
        conn, bodies=["trca"], fetch=True, scrape=False, virtual_display=False, out=out
    )


@pytest.mark.parametrize("status", [403, 404, 503])
def test_http_gap_is_reported_while_good_and_held_reports_survive(
    conn, monkeypatch, tmp_path, status
):
    missing = BASE + "FileStream.ashx?DocumentId=5558"
    good = BASE + "FileStream.ashx?DocumentId=1"
    held = BASE + "FileStream.ashx?DocumentId=2"
    request = httpx.Request("GET", missing)
    error = httpx.HTTPStatusError(
        str(status), request=request, response=httpx.Response(status, request=request)
    )
    calls = _prepare_capture(
        conn,
        monkeypatch,
        tmp_path,
        {missing: error, good: b"%PDF-1.7\nnew report", held: AssertionError("held bytes refetched")},
    )
    conn.execute("UPDATE background_pdf SET sha256='held', text='preserved' WHERE url=?", (held,))
    conn.commit()
    monkeypatch.setattr(trca, "_pdftotext", lambda _p: "new report text")

    failures = _capture(conn)

    assert len(failures) == 1
    assert failures[0][0] == "download:trca"
    assert missing in failures[0][1] and str(status) in failures[0][1]
    assert "left queued" in failures[0][1]
    assert calls == [True]
    rows = {r["url"]: dict(r) for r in conn.execute("SELECT * FROM background_pdf")}
    assert rows[missing]["sha256"] is None
    assert rows[good]["sha256"] and rows[good]["text"] == "new report text"
    assert rows[held]["sha256"] == "held" and rows[held]["text"] == "preserved"
    assert len(list((tmp_path / "reports").glob("*.pdf"))) == 1


@pytest.mark.parametrize(
    "reply,expected_log",
    [
        (b"@media Print{...}", "not a PDF"),
        (b"<html>Verifying your browser</html>", None),
        (httpx.ConnectError("connection lost"), None),
    ],
)
def test_non_pdf_and_transport_errors_are_operational_failures(
    conn, monkeypatch, tmp_path, reply, expected_log
):
    url = BASE + "FileStream.ashx?DocumentId=6819"
    _prepare_capture(conn, monkeypatch, tmp_path, {url: reply})
    lines = []
    failures = _capture(conn, out=lines.append)
    assert len(failures) == 1 and failures[0][0] == "download:trca"
    assert url in failures[0][1]
    if expected_log:
        assert any(expected_log in line and url in line for line in lines)
    assert conn.execute("SELECT sha256 FROM background_pdf").fetchone()[0] is None


@pytest.mark.parametrize("stage", ["calendar", "meeting"])
def test_discovery_gaps_are_reported_and_pending_fetch_continues(conn, monkeypatch, tmp_path, stage):
    monkeypatch.setattr(config, "TRCA_ESCRIBE_YEARS", [2020])
    monkeypatch.setattr(config, "TRCA_REPORTS_DIR", tmp_path)
    pending = []
    monkeypatch.setattr(trca, "_store_pending_pdfs", lambda *a, **k: pending.append(True) or 0)
    request = httpx.Request("GET", BASE + "missing")
    error = httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))

    class Http:
        def post_json(self, *a, **k):
            if stage == "calendar":
                raise error
            return {"d": [{"ID": "public-meeting", "HasAgenda": True}]}

        def get_text(self, *a, **k):
            raise error

    failures = []
    assert trca.download_reports(conn, Http(), failures=failures) == 0
    assert len(failures) == 1 and failures[0][0] == "download:trca"
    assert stage in failures[0][1] and pending == [True]


def test_enrich_agencies_exits_failed_for_a_download_gap(conn, monkeypatch, tmp_path, capsys):
    url = BASE + "FileStream.ashx?DocumentId=7234"
    request = httpx.Request("GET", url)
    error = httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
    _prepare_capture(conn, monkeypatch, tmp_path, {url: error})
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    assert cli.main(["enrich-agencies", "--only", "trca", "--fetch"]) == 1
    assert "FAILED  download:trca" in capsys.readouterr().err


def _setup_browser(conn, monkeypatch, responses):
    visits, sessions, closed = [], [], []

    @contextmanager
    def browser():
        sessions.append(True)

        def fetch(url):
            visits.append(url)
            return responses[url]

        try:
            yield fetch
        finally:
            closed.append(True)

    monkeypatch.setattr(trca, "_browser_fetch", browser, raising=False)
    for url in responses:
        conn.execute("INSERT INTO background_pdf(url,kind) VALUES (?,'agency_board')", (url,))
    return visits, sessions, closed


@pytest.mark.parametrize("status", [200, 403])
def test_verification_uses_one_browser_session_and_archives_exact_pdf(conn, monkeypatch, tmp_path, status):
    replies = {FILESTREAM_BASE + str(n): {"status": 200, "url": FILESTREAM_BASE + str(n), "body": PDF} for n in [21060, 21063]}
    visits, sessions, closed = _setup_browser(conn, monkeypatch, replies)

    def direct(url):
        if status == 200:
            return CHALLENGE
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, content=CHALLENGE, request=req))

    failures = []
    n = trca._store_pending_pdfs(
        conn, SimpleNamespace(get_bytes=direct), tmp_path, "%escribemeetings%", lambda _m: None, "trca", failures=failures
    )
    assert n == 2 and not failures
    assert visits == list(replies) and sessions == [True] and closed == [True]
    rows = conn.execute("SELECT * FROM background_pdf").fetchall()
    assert all(pathlib.Path(r["local_path"]).read_bytes() == PDF and r["text"] for r in rows)


@pytest.mark.parametrize(
    "reply",
    [
        {"status": 403, "body": CHALLENGE},
        {"status": 200, "body": CHALLENGE},
        {"status": 200, "body": b"%PDF-1.7\nnot a valid document"},
        {"status": 200, "body": PDF, "url": "https://other.example/file.pdf"},
    ],
)
def test_browser_failures_stay_queued_and_reported(conn, monkeypatch, tmp_path, reply):
    url = FILESTREAM_BASE + "21060"
    visits, _, closed = _setup_browser(conn, monkeypatch, {url: {"url": url, **reply}})
    failures = []
    n = trca._store_pending_pdfs(
        conn, SimpleNamespace(get_bytes=lambda _u: CHALLENGE), tmp_path, "%escribemeetings%", lambda _m: None, "trca", failures=failures
    )
    assert n == 0 and len(failures) == 1 and visits == [url] and closed == [True]
    assert conn.execute("SELECT sha256 FROM background_pdf").fetchone()[0] is None
    assert not list(tmp_path.glob("*.pdf"))


def test_browser_rate_limit_defers_rest_of_pass(conn, monkeypatch, tmp_path):
    replies = {FILESTREAM_BASE + str(n): {"status": 429, "url": FILESTREAM_BASE + str(n), "body": b"limited", "retry_after": "120"} for n in [1, 2]}
    visits, _, closed = _setup_browser(conn, monkeypatch, replies)
    failures = []
    n = trca._store_pending_pdfs(
        conn, SimpleNamespace(get_bytes=lambda _u: CHALLENGE), tmp_path, "%escribemeetings%", lambda _m: None, "trca", failures=failures
    )
    assert n == 0 and visits == [FILESTREAM_BASE + "1"] and closed == [True]
    assert len(failures) == 1 and "deferred" in failures[0][1]


def test_plain_denial_does_not_start_browser(conn, monkeypatch, tmp_path):
    url = FILESTREAM_BASE + "1"
    visits, sessions, closed = _setup_browser(conn, monkeypatch, {url: {}})
    req = httpx.Request("GET", url)

    def denied(_u):
        raise httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, content=b"Forbidden", request=req))

    failures = []
    assert trca._store_pending_pdfs(
        conn, SimpleNamespace(get_bytes=denied), tmp_path, "%escribemeetings%", lambda _m: None, "trca", failures=failures
    ) == 0
    assert not visits and not sessions and not closed and len(failures) == 1


def test_pending_agenda_context_survives_calendar_passage(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TRCA_ESCRIBE_YEARS", [2026])
    monkeypatch.setattr(config, "TRCA_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(trca, "_store_pending_pdfs", lambda *a, **kw: 0)
    meeting = {"ID": "meeting-id", "HasAgenda": True, "MeetingPassed": False, "MeetingDate": "2026-11-06"}
    http = SimpleNamespace(
        post_json=lambda *a, **kw: {"d": [meeting]},
        get_text=lambda _u: f'<a href="{FILESTREAM_BASE}21060">Report</a>',
    )
    trca.download_reports(conn, http)
    before = conn.execute("SELECT award_review_context_json FROM background_pdf").fetchone()[0]
    assert json.loads(before)["meeting"]["MeetingPassed"] is False
    meeting["MeetingPassed"] = True
    trca.download_reports(conn, http)
    assert conn.execute("SELECT award_review_context_json FROM background_pdf").fetchone()[0] == before


def test_future_agenda_award_is_withheld_but_supported_bids_survive(conn):
    from tests.test_agency_extraction_fields import cache, contract
    from toronto_bids.extraction import backfill_from_extraction

    url = cache(conn, "trca", [contract("10044924")])
    context = json.dumps({"meeting": {"ID": "meeting-id", "MeetingPassed": False}})
    conn.execute("UPDATE background_pdf SET award_review_context_json=? WHERE url=?", (context, url))
    conn.execute("INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,'agency_board','one','held text')", (url + "&duplicate",))
    result = backfill_from_extraction(conn, "trca")
    assert conn.execute("SELECT count(*) FROM agency_bid").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM agency_award").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM agency_solicitation").fetchone()[0] is None
    rows = conn.execute("SELECT * FROM agency_derivation").fetchall()
    assert all("decision verification" in r["reason"] for r in rows)
    assert result["coverage"]["withheld_award_facts"] == 1


def test_direct_rate_limit_defers_without_browser(conn, monkeypatch, tmp_path):
    replies = {FILESTREAM_BASE + str(n): {} for n in [1, 2]}
    visits, sessions, _ = _setup_browser(conn, monkeypatch, replies)
    direct_visits = []

    def limited(url):
        direct_visits.append(url)
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, headers={"Retry-After": "120"}, request=req))

    failures = []
    assert trca._store_pending_pdfs(
        conn, SimpleNamespace(get_bytes=limited), tmp_path, "%escribemeetings%", lambda _m: None, "trca", failures=failures
    ) == 0
    assert direct_visits == [FILESTREAM_BASE + "1"] and not visits and not sessions
    assert len(failures) == 1 and "deferred" in failures[0][1]


def test_browser_launch_failure_does_not_launch_once_per_document(conn, monkeypatch, tmp_path):
    attempts = []
    for n in [1, 2]:
        conn.execute("INSERT INTO background_pdf(url,kind) VALUES (?,'agency_board')", (FILESTREAM_BASE + str(n),))

    @contextmanager
    def unavailable():
        attempts.append(True)
        raise RuntimeError("browser unavailable")
        yield

    monkeypatch.setattr(trca, "_browser_fetch", unavailable)
    failures = []
    assert trca._store_pending_pdfs(
        conn, SimpleNamespace(get_bytes=lambda _u: CHALLENGE), tmp_path, "%escribemeetings%", lambda _m: None, "trca", failures=failures
    ) == 0
    assert attempts == [True] and len(failures) == 2
