import pathlib

from toronto_bids.sources.trca_board import escribe_document_urls

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _read(name):
    return (FIXTURES / name).read_text()


def test_escribe_document_urls_extracts_filestream_links():
    html = _read("trca_escribe_2023.html")
    urls = escribe_document_urls(html)
    assert urls, "expected at least one FileStream/Meeting link in the recorded page"
    assert all(u.startswith("https://pub-trca.escribemeetings.com/") for u in urls)


def test_download_skips_a_dead_url_without_aborting_the_run(tmp_path):
    import httpx

    from toronto_bids.sources.trca_board import _store_pending_pdfs
    from toronto_bids.store import db

    conn = db.connect(":memory:")
    db.init_db(conn)
    good = "https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=1"
    dead = "https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=2"
    for url in (good, dead):
        conn.execute(
            "INSERT INTO background_pdf (url, kind) VALUES (?, 'agency_board')", (url,)
        )
    conn.commit()

    def _raise_404(url):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError(
            "404", request=req, response=httpx.Response(404, request=req)
        )

    class _FakeHttp:
        def get_bytes(self, url, **_kw):
            return b"%PDF-1.7\ntrailer\n" if url == good else _raise_404(url)

    n = _store_pending_pdfs(
        conn, _FakeHttp(), tmp_path, "%escribemeetings%", lambda _m: None, "trca"
    )
    assert n == 1
    held = dict(
        conn.execute("SELECT url, sha256 IS NOT NULL FROM background_pdf").fetchall()
    )
    assert held[good] is True or held[good] == 1
    assert held[dead] in (False, 0, None)
    conn.close()


def test_meeting_detail_urls_from_calendar_json():
    import json

    from toronto_bids.sources.trca_board import meeting_detail_urls

    cal = json.loads(_read("trca_getcalendarmeetings_2023q1.json"))
    urls = meeting_detail_urls(cal)
    assert len(urls) == 5
    assert all("Meeting.aspx?Id=" in u for u in urls)
    assert all(u.startswith("https://pub-trca.escribemeetings.com/") for u in urls)
    assert any("82fa331c-e7cb-4e9a-87e2-093a1a51899f" in u for u in urls)


def test_a_stylesheet_and_logo_served_through_filestream_are_not_documents():
    from toronto_bids.sources.trca_board import escribe_asset_urls

    html = _read("trca_escribe_meeting_assets.html")

    docs = escribe_document_urls(html)
    assert [u.rsplit("=", 1)[-1] for u in docs] == ["3216", "3235", "3236"]
    assert not any("3253" in u or "3251" in u for u in docs)

    assert sorted(u.rsplit("=", 1)[-1] for u in escribe_asset_urls(html)) == [
        "3251",
        "3253",
    ]


def test_href_is_not_always_the_first_attribute_of_a_document_anchor():
    urls = escribe_document_urls(
        "<a class='Link' tabindex='15' href='filestream.ashx?DocumentId=99'>r.pdf</a>"
    )
    assert urls == [
        "https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=99"
    ]


def test_prune_unqueues_page_assets_but_never_a_document_or_held_bytes(tmp_path):
    from toronto_bids.sources.trca_board import _prune_page_assets
    from toronto_bids.store import db

    base = "https://pub-trca.escribemeetings.com/"
    asset = base + "FileStream.ashx?DocumentId=3253"
    dead = base + "FileStream.ashx?DocumentId=5558"
    held_asset = (
        base + "FileStream.ashx?DocumentId=7777"
    )

    conn = db.connect(":memory:")
    db.init_db(conn)
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
    conn.close()


def test_prune_does_nothing_when_no_page_loaded(tmp_path):
    from toronto_bids.sources.trca_board import _prune_page_assets
    from toronto_bids.store import db

    conn = db.connect(":memory:")
    db.init_db(conn)
    conn.execute(
        "INSERT INTO background_pdf (url, kind) VALUES "
        "('https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=1', "
        "'agency_board')"
    )
    conn.commit()
    assert _prune_page_assets(conn, set(), lambda _m: None) == 0
    assert conn.execute("SELECT COUNT(*) FROM background_pdf").fetchone()[0] == 1
    conn.close()


def test_a_non_pdf_response_is_logged_rather_than_dropped_in_silence(tmp_path):
    from toronto_bids.sources.trca_board import _store_pending_pdfs
    from toronto_bids.store import db

    conn = db.connect(":memory:")
    db.init_db(conn)
    url = "https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=3253"
    conn.execute(
        "INSERT INTO background_pdf (url, kind) VALUES (?, 'agency_board')", (url,)
    )
    conn.commit()

    class _FakeHttp:
        def get_bytes(self, _url, **_kw):
            return b"@media Print{...}"

    lines = []
    assert (
        _store_pending_pdfs(
            conn, _FakeHttp(), tmp_path, "%escribemeetings%", lines.append, "trca"
        )
        == 0
    )
    assert any("not a PDF" in line and url in line for line in lines)
    assert (
        conn.execute(
            "SELECT sha256 FROM background_pdf WHERE url=?", (url,)
        ).fetchone()[0]
        is None
    )
    conn.close()


def test_escribe_document_urls_decodes_html_entities():
    from toronto_bids.sources.trca_board import escribe_document_urls

    page = (
        '<a href="https&#58;//pub-trca.escribemeetings.com/'
        'FileStream.ashx?DocumentId=10661&amp;lang=en">report</a>'
    )
    assert escribe_document_urls(page) == [
        "https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=10661&lang=en"
    ]
