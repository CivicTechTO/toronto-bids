"""TRCA download gaps must survive per-document isolation into operational status (#244)."""

import httpx
import pytest

from toronto_bids import cli, config
from toronto_bids.sources import trca_board as trca

BASE = "https://pub-trca.escribemeetings.com/"


def prepare_capture(conn, monkeypatch, tmp_path, replies):
    monkeypatch.setattr(config, "TRCA_ESCRIBE_YEARS", [])
    monkeypatch.setattr(config, "TRCA_REPORTS_DIR", tmp_path / "reports")
    extraction_calls = []
    monkeypatch.setattr(trca, "store_trca_reports", lambda *a, **k:
                        extraction_calls.append(True) or {"solicitations": 0, "awards": 0})

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


def capture(conn):
    return cli._capture_agency_bodies(conn, bodies=["trca"], fetch=True, scrape=False,
                                      virtual_display=False, out=lambda _m: None)


@pytest.mark.parametrize("status", [403, 404, 503])
def test_http_gap_is_reported_while_good_and_held_reports_survive(
        conn, monkeypatch, tmp_path, status):
    missing = BASE + "FileStream.ashx?DocumentId=5558"
    good = BASE + "FileStream.ashx?DocumentId=1"
    held = BASE + "FileStream.ashx?DocumentId=2"
    request = httpx.Request("GET", missing)
    error = httpx.HTTPStatusError(str(status), request=request,
                                 response=httpx.Response(status, request=request))
    calls = prepare_capture(conn, monkeypatch, tmp_path, {
        missing: error, good: b"%PDF-1.7\nnew report", held: AssertionError("held bytes refetched")})
    conn.execute("UPDATE background_pdf SET sha256='held', text='preserved' WHERE url=?", (held,))
    conn.commit()
    monkeypatch.setattr(trca, "_pdftotext", lambda p: "new report text")

    failures = capture(conn)

    assert len(failures) == 1
    assert failures[0][0] == "download:trca"
    assert missing in failures[0][1] and str(status) in failures[0][1]
    assert "left queued" in failures[0][1]
    assert calls == [True], "cached extraction still runs after isolated download failures"
    rows = {r['url']: dict(r) for r in conn.execute("SELECT * FROM background_pdf")}
    assert rows[missing]['sha256'] is None
    assert rows[good]['sha256'] and rows[good]['text'] == "new report text"
    assert rows[held]['sha256'] == 'held' and rows[held]['text'] == 'preserved'
    assert len(list((tmp_path / 'reports').glob('*.pdf'))) == 1


@pytest.mark.parametrize("reply", [b"<html>Verifying your browser</html>",
                                   httpx.ConnectError("connection lost")])
def test_non_pdf_and_transport_errors_are_operational_failures(conn, monkeypatch, tmp_path, reply):
    url = BASE + "FileStream.ashx?DocumentId=6819"
    prepare_capture(conn, monkeypatch, tmp_path, {url: reply})
    failures = capture(conn)
    assert len(failures) == 1 and failures[0][0] == "download:trca"
    assert url in failures[0][1]
    assert conn.execute("SELECT sha256 FROM background_pdf").fetchone()[0] is None


@pytest.mark.parametrize("stage", ["calendar", "meeting"])
def test_discovery_gaps_are_reported_and_pending_fetch_continues(conn, monkeypatch, tmp_path, stage):
    monkeypatch.setattr(config, "TRCA_ESCRIBE_YEARS", [2020])
    monkeypatch.setattr(config, "TRCA_REPORTS_DIR", tmp_path)
    pending = []
    monkeypatch.setattr(trca, "_store_pending_pdfs", lambda *a, **k: pending.append(True) or 0)
    request = httpx.Request("GET", BASE + "missing")
    error = httpx.HTTPStatusError("404", request=request,
                                 response=httpx.Response(404, request=request))

    class Http:
        def post_json(self, *a, **k):
            if stage == "calendar":
                raise error
            return {"d": [{"ID": "public-meeting", "HasAgenda": True}]}

        def get_text(self, *a, **k):
            raise error

    failures = []
    assert trca.download_reports(conn, Http(), failures=failures) == 0
    assert len(failures) == 1 and failures[0][0] == 'download:trca'
    assert stage in failures[0][1] and pending == [True]


def test_enrich_agencies_exits_failed_for_a_download_gap(conn, monkeypatch, tmp_path, capsys):
    url = BASE + "FileStream.ashx?DocumentId=7234"
    request = httpx.Request("GET", url)
    error = httpx.HTTPStatusError("404", request=request,
                                 response=httpx.Response(404, request=request))
    prepare_capture(conn, monkeypatch, tmp_path, {url: error})
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    assert cli.main(["enrich-agencies", "--only", "trca", "--fetch"]) == 1
    assert "FAILED  download:trca" in capsys.readouterr().err
