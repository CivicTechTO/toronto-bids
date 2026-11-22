"""#164: Committee/Council award infrastructure tests.

Parser tests were removed in #205 (LLM extraction replaces per-source parsers).
These test the discovery/download infrastructure that remains.
"""

from pathlib import Path

from toronto_bids.sources.committee_awards import (
    award_doc_number,
    award_items_from_voting_record,
    report_url_from_item_html,
)

FIX = Path(__file__).parent / "fixtures" / "committee"


def test_award_doc_number_reads_the_title_vocabulary():
    assert award_doc_number("Award of Doc4553928310 to GFL") == "4553928310"
    assert (
        award_doc_number(
            "Process for Award of Negotiable RFP Document Number 4053424337 for x"
        )
        == "4053424337"
    )
    assert (
        award_doc_number("Award of Ariba Document Number 3448368603 to CH2M")
        == "3448368603"
    )
    assert award_doc_number("Election of the Speaker") is None


def test_award_items_from_voting_record_dedups_and_skips_non_awards():
    items = award_items_from_voting_record(
        (FIX / "voting_record_sample.csv").read_text()
    )
    by_doc = {i["document_number"]: i for i in items}
    assert set(by_doc) == {"4553928310", "4053424337", "3448368603"}
    assert by_doc["4553928310"]["reference"] == "2024.GG16.12"
    assert by_doc["4553928310"]["committee"] == "City Council"


def test_report_url_from_item_html_finds_the_award_report():
    html = (FIX / "item_2024_GG16_12.html").read_text()
    url = report_url_from_item_html(html, "4553928310")
    assert (
        url
        == "https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-248375.pdf"
    )


def test_report_url_from_item_html_returns_none_without_a_matching_link():
    assert (
        report_url_from_item_html(
            "<html><body>No reports here.</body></html>", "4553928310"
        )
        is None
    )
    html = (
        "<div><span>Award of Doc1111111111</span>"
        '<a href="https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-1.pdf">x</a></div>'
        "<div><span>Award of Doc2222222222</span>"
        '<a href="https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-2.pdf">x</a></div>'
    )
    assert report_url_from_item_html(html, "4553928310") is None


def test_fetch_voting_records_filters_ckan_csv_resources():
    from toronto_bids.sources.committee_awards import fetch_voting_records

    fetched_urls = []
    seen_params = {}

    class FakeHttp:
        def get_json(self, _url, *, params=None):
            seen_params.update(params or {})
            return {
                "result": {
                    "resources": [
                        {"format": "CSV", "name": "member-voting-record-2022-2026", "url": "https://example/r1.csv"},
                        {"format": "csv", "name": "member-voting-record-2018-2022", "url": "https://example/r2.csv"},
                        {"format": "JSON", "name": "member-voting-record-2022-2026", "url": "https://example/r3.json"},
                        {"format": "CSV", "name": "readme", "url": "https://example/readme.csv"},
                        {"format": "CSV", "name": "member-voting-record-1998", "url": "https://example/old.csv"},
                    ]
                }
            }

        def get_text(self, url):
            fetched_urls.append(url)
            return f"csv:{url}"

    csvs = fetch_voting_records(FakeHttp())
    assert seen_params == {"id": "members-of-toronto-city-council-voting-record"}
    assert fetched_urls == ["https://example/r1.csv", "https://example/r2.csv"]
    assert csvs == ["csv:https://example/r1.csv", "csv:https://example/r2.csv"]


def test_download_committee_reports_archives_valid_pdfs_and_skips_held_invalid_and_errors(
    conn, tmp_path, monkeypatch
):
    import hashlib
    from toronto_bids.models import BackgroundPdf
    from toronto_bids.sources import committee_awards as mod
    from toronto_bids.store import db

    reports_dir = tmp_path / "committee_award"
    monkeypatch.setattr(mod, "COMMITTEE_REPORTS_DIR", reports_dir)
    monkeypatch.setattr(mod, "_pdftotext", lambda _p: "staff report body")

    held_url = "https://example/held.pdf"
    db.upsert_row(
        conn,
        BackgroundPdf(url=held_url, kind="committee_award", sha256="already_held"),
        overwrite=True,
    )
    conn.commit()

    pdf_bytes = b"%PDF-1.4\ncommittee report bytes"
    requested = []

    class FakeHttp:
        def get_bytes(self, url):
            requested.append(url)
            if url.endswith("fail.pdf"):
                raise RuntimeError("503 upstream")
            if url.endswith("notpdf.pdf"):
                return b"<html>not a pdf</html>"
            return pdf_bytes

    url_map = {
        held_url: "4000000001",
        "https://example/fail.pdf": "4000000002",
        "https://example/notpdf.pdf": "4000000003",
        "https://example/good.pdf": "4553928310",
    }
    logs = []
    n = mod.download_committee_reports(conn, FakeHttp(), url_map, log=logs.append)

    assert n == 1
    assert held_url not in requested
    assert requested == [
        "https://example/fail.pdf",
        "https://example/notpdf.pdf",
        "https://example/good.pdf",
    ]
    assert any("committee skip" in line for line in logs)

    expected_sha = hashlib.sha256(pdf_bytes).hexdigest()
    expected_path = reports_dir / f"{expected_sha}.pdf"
    assert expected_path.read_bytes() == pdf_bytes

    rows = {r["url"]: dict(r) for r in conn.execute("SELECT * FROM background_pdf")}
    assert set(rows) == {held_url, "https://example/good.pdf"}
    good = rows["https://example/good.pdf"]
    assert good["kind"] == "committee_award"
    assert good["document_number"] == "4553928310"
    assert good["sha256"] == expected_sha
    assert good["local_path"] == str(expected_path)
    assert good["text"] == "staff report body"


def test_pdftotext_handles_missing_binary_timeout_and_output(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from toronto_bids.sources import committee_awards as mod

    pdf_path = tmp_path / "sample.pdf"
    for exc in (FileNotFoundError("pdftotext"), subprocess.TimeoutExpired(["pdftotext"], 120)):
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, _e=exc, **kw: (_ for _ in ()).throw(_e))
        assert mod._pdftotext(pdf_path) is None

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=b""))
    assert mod._pdftotext(pdf_path) is None

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=b"Award text"))
    assert mod._pdftotext(pdf_path) == "Award text"


def test_store_committee_bids_returns_bids_written(conn, monkeypatch):
    from toronto_bids import extraction
    from toronto_bids.sources.committee_awards import store_committee_bids

    monkeypatch.setattr(extraction, "extract_and_backfill", lambda _c, corpus, **_kw: {"bids_written": 4 if corpus == "committee" else 0})
    assert store_committee_bids(conn) == 4
