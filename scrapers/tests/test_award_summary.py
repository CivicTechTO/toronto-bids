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


def _blank_valid_pdf() -> bytes:
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(buf))
        buf.extend(obj)
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode())
    buf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(buf)


def test_download_award_summaries_rejects_html_recaptures_invalid_and_keeps_scanned_pdf(
    conn, tmp_path
):
    import hashlib
    import shutil
    from pathlib import Path

    import pytest
    from toronto_bids.models import BackgroundPdf
    from toronto_bids.sources import award_summary as mod
    from toronto_bids.store import db

    if shutil.which("pdftotext") is None:
        pytest.skip("needs pdftotext")

    valid_pdf = (Path(__file__).parent / "fixtures" / "tiny.pdf").read_bytes()
    scanned_pdf = _blank_valid_pdf()
    html_page = b"<html><body>Scheduled maintenance</body></html>"

    # 1) Valid scanned PDF (text=None) already on disk must NOT be re-downloaded.
    scanned_path = tmp_path / "scanned001"
    scanned_path.write_bytes(scanned_pdf)
    db.upsert_row(
        conn,
        BackgroundPdf(
            url=mod._UPLOAD + "scanned001",
            kind="award_summary",
            document_number="1000000001",
            local_path=str(scanned_path),
            sha256=hashlib.sha256(scanned_pdf).hexdigest(),
            text=None,
        ),
        overwrite=True,
    )

    # 2) Existing invalid cached form (maintenance HTML saved previously) must be recaptured.
    stale_html_path = tmp_path / "stalehtml"
    stale_html_path.write_bytes(html_page)
    db.upsert_row(
        conn,
        BackgroundPdf(
            url=mod._UPLOAD + "stalehtml",
            kind="award_summary",
            document_number="1000000002",
            local_path=str(stale_html_path),
            sha256=hashlib.sha256(html_page).hexdigest(),
            text=None,
        ),
        overwrite=True,
    )
    conn.commit()

    records = [
        {
            "Solicitation_Document_Number": "Doc1000000001",
            "uploadedFilesStaff": [{"bin_id": "scanned001", "name": "Award Summary Form.pdf"}],
        },
        {
            "Solicitation_Document_Number": "Doc1000000002",
            "uploadedFilesStaff": [{"bin_id": "stalehtml", "name": "Award Summary Form.pdf"}],
        },
        {
            "Solicitation_Document_Number": "Doc1000000003",
            "uploadedFilesStaff": [{"bin_id": "maintpage", "name": "Award Summary Form.pdf"}],
        },
        {
            "Solicitation_Document_Number": "Doc1000000004",
            "uploadedFilesStaff": [{"bin_id": "freshgood", "name": "Award Summary Form.pdf"}],
        },
    ]

    responses = {
        "stalehtml": valid_pdf,
        "maintpage": html_page,
        "freshgood": valid_pdf,
    }
    fetched = []

    class FakeHttp:
        def get_json(self, _url):
            return {"value": records, "@odata.count": len(records)}

        def get_bytes(self, url):
            bid = url.rsplit("/", 1)[-1]
            fetched.append(bid)
            return responses[bid]

    failures = []
    stored = mod.download_award_summaries(conn, FakeHttp(), dest_dir=tmp_path, failures=failures)
    assert stored == 2
    assert fetched == ["stalehtml", "maintpage", "freshgood"]
    assert len(failures) == 1 and failures[0][0] == "award_summary_download:maintpage"

    rows = {r["url"]: dict(r) for r in conn.execute("SELECT * FROM background_pdf")}
    assert mod._UPLOAD + "maintpage" not in rows
    assert rows[mod._UPLOAD + "stalehtml"]["sha256"] == hashlib.sha256(valid_pdf).hexdigest()
    assert "HELLO PDF" in rows[mod._UPLOAD + "stalehtml"]["text"]
    assert rows[mod._UPLOAD + "freshgood"]["sha256"] == hashlib.sha256(valid_pdf).hexdigest()

    # On retry, once the maintenance page clears, maintpage is downloaded and stored.
    responses["maintpage"] = valid_pdf
    fetched.clear()
    retry_failures = []
    stored_retry = mod.download_award_summaries(
        conn, FakeHttp(), dest_dir=tmp_path, failures=retry_failures
    )
    assert stored_retry == 1
    assert fetched == ["maintpage"]
    assert retry_failures == []
    row = conn.execute(
        "SELECT sha256, text FROM background_pdf WHERE url=?", (mod._UPLOAD + "maintpage",)
    ).fetchone()
    assert row["sha256"] == hashlib.sha256(valid_pdf).hexdigest() and "HELLO PDF" in row["text"]


def test_enrich_awards_standalone_exits_nonzero_on_rejected_download(
    conn, tmp_path, monkeypatch, capsys
):
    import shutil
    from pathlib import Path

    import pytest
    from toronto_bids import cli, config
    from toronto_bids.sources import award_summary as mod

    if shutil.which("pdftotext") is None:
        pytest.skip("needs pdftotext")

    valid_pdf = (Path(__file__).parent / "fixtures" / "tiny.pdf").read_bytes()
    records = [
        {
            "Solicitation_Document_Number": "Doc1000000010",
            "uploadedFilesStaff": [{"bin_id": "badhtml", "name": "Award Summary Form.pdf"}],
        },
        {
            "Solicitation_Document_Number": "Doc1000000011",
            "uploadedFilesStaff": [{"bin_id": "goodpdf", "name": "Award Summary Form.pdf"}],
        },
    ]

    class FakeHttp:
        def get_json(self, _url):
            return {"value": records, "@odata.count": len(records)}

        def get_bytes(self, url):
            return b"<html>maintenance</html>" if url.endswith("badhtml") else valid_pdf

        def close(self):
            pass

    class KeepOpen:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(config, "AWARD_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(cli, "_open_db", lambda: KeepOpen(conn))
    monkeypatch.setattr(cli, "HttpClient", FakeHttp)
    monkeypatch.setattr(mod, "store_award_summary_bids", lambda *a, **k: 0)

    rc = cli.main(["enrich-awards", "--download"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "award_summary_download:badhtml" in err
    # Valid sibling form was still archived.
    urls = {r[0] for r in conn.execute("SELECT url FROM background_pdf")}
    assert urls == {mod._UPLOAD + "goodpdf"}
