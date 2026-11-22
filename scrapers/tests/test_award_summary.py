"""#114: Award Summary Form infrastructure tests.

Parser tests were removed in #205 (LLM extraction replaces per-source parsers).
These test the download/indexing infrastructure that remains.
"""

from toronto_bids.sources.award_summary import award_summary_files


def test_finds_the_award_summary_form_on_a_record():
    rec = {
        "uploadedFilesStaff": [
            {
                "bin_id": "kSj1PnNq2nX0FApSenhvCA",
                "name": "Doc5616191850 Award Summary Form.pdf",
            }
        ]
    }
    assert award_summary_files(rec) == [
        (
            "https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/kSj1PnNq2nX0FApSenhvCA",
            "Doc5616191850 Award Summary Form.pdf",
        )
    ]


def test_an_award_under_500k_carries_no_form():
    """The form only exists over $500,000 — the panel had no such floor, so the bid record
    thins permanently for small awards. An empty list is the normal case, not an error."""
    assert award_summary_files({"uploadedFilesStaff": []}) == []
    assert award_summary_files({}) == []


def test_other_attachments_are_not_mistaken_for_the_form():
    rec = {"uploadedFilesStaff": [{"bin_id": "x", "name": "Doc123 Addendum 1.pdf"}]}
    assert award_summary_files(rec) == []


def test_fetch_awarded_records_paginates_and_stops_at_odata_count():
    from toronto_bids.sources.award_summary import fetch_awarded_records

    urls = []
    pages = [
        {"value": [{"id": 1}], "@odata.count": 501},
        {"value": [{"id": 2}], "@odata.count": 501},
    ]

    class FakeHttp:
        def get_json(self, url):
            urls.append(url)
            return pages[len(urls) - 1]

    rows = fetch_awarded_records(FakeHttp())
    assert rows == [{"id": 1}, {"id": 2}]
    assert len(urls) == 2
    assert "$skip=0&$top=500" in urls[0]
    assert "$skip=500&$top=500" in urls[1]


def test_fetch_awarded_records_stops_on_empty_page():
    from toronto_bids.sources.award_summary import fetch_awarded_records

    calls = 0

    class FakeHttp:
        def get_json(self, _url):
            nonlocal calls
            calls += 1
            return {"value": [], "@odata.count": 1000}

    assert fetch_awarded_records(FakeHttp()) == []
    assert calls == 1


def test_download_award_summaries_skips_held_stores_normalized_and_isolates_failures(
    conn, tmp_path, monkeypatch
):
    from toronto_bids.models import BackgroundPdf
    from toronto_bids.sources import award_summary as mod
    from toronto_bids.sources import council
    from toronto_bids.store import db

    held_url = mod._UPLOAD + "held123"
    db.upsert_row(
        conn,
        BackgroundPdf(url=held_url, kind="award_summary", sha256="existing_sha"),
        overwrite=True,
    )
    conn.commit()

    records = [
        {
            "Solicitation_Document_Number": "Doc5616191849",
            "uploadedFilesStaff": [{"bin_id": "held123", "name": "Award Summary Form.pdf"}],
        },
        {
            "Solicitation_Document_Number": "Doc5616191851",
            "uploadedFilesStaff": [{"bin_id": "bad456", "name": "Award Summary Form.pdf"}],
        },
        {
            "Solicitation_Document_Number": "Doc5616191850",
            "uploadedFilesStaff": [{"bin_id": "good789", "name": "Award Summary Form.pdf"}],
        },
    ]

    class FakeHttp:
        def get_json(self, _url):
            return {"value": records, "@odata.count": len(records)}

    downloaded = []

    def fake_download(_http, url, dest_dir, *, layout=False):
        downloaded.append((url, dest_dir, layout))
        if url.endswith("bad456"):
            raise RuntimeError("network timeout")
        return {
            "local_path": str(tmp_path / "good789.pdf"),
            "sha256": "good_sha256",
            "text": "Extracted award summary text",
        }

    monkeypatch.setattr(council, "download_pdf", fake_download)
    logs = []
    stored = mod.download_award_summaries(conn, FakeHttp(), dest_dir=tmp_path, log=logs.append)

    assert stored == 1
    assert [u for u, _, _ in downloaded] == [mod._UPLOAD + "bad456", mod._UPLOAD + "good789"]
    assert all(layout is True and d == tmp_path for _, d, layout in downloaded)
    assert any("skipped bad456" in m for m in logs)

    rows = {r["url"]: dict(r) for r in conn.execute("SELECT * FROM background_pdf")}
    assert set(rows) == {held_url, mod._UPLOAD + "good789"}
    good = rows[mod._UPLOAD + "good789"]
    assert good["kind"] == "award_summary"
    assert good["document_number"] == "5616191850"
    assert good["sha256"] == "good_sha256"
    assert good["text"] == "Extracted award summary text"


def test_store_award_summary_bids_returns_bids_written(conn, monkeypatch):
    from toronto_bids import extraction
    from toronto_bids.sources.award_summary import store_award_summary_bids

    seen = {}

    def fake_extract(c, corpus, *, log, failures):
        seen.update(conn=c, corpus=corpus, log=log, failures=failures)
        return {"bids_written": 7}

    monkeypatch.setattr(extraction, "extract_and_backfill", fake_extract)
    failures = []
    assert store_award_summary_bids(conn, failures=failures) == 7
    assert seen["conn"] is conn
    assert seen["corpus"] == "award_summary"
    assert seen["failures"] is failures
