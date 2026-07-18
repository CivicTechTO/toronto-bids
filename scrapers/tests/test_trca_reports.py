import pathlib

from toronto_bids.buyers import seed_buyers
from toronto_bids.sources.trca_board import (
    escribe_document_urls,
    parse_trca_report,
    store_trca_reports,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _read(name):
    return (FIXTURES / name).read_text()


def test_armour_stone_refs_and_title():
    items = parse_trca_report(_read("trca_armour_stone_2023.txt"))
    refs = {i["native_ref"] for i in items}
    assert refs == {"10039751", "10039753"}   # one row per ref (multi-ref item)
    assert all("ARMOUR" in i["title"].upper() for i in items)


def test_armour_stone_winners_and_amounts():
    # RECOMMENDATION and RATIONALE both carry an award clause for the same award
    # (same amount, sometimes a different name string) — must dedupe to one winner
    # per ref, not one row per name variant (#73-class double-count).
    items = {i["native_ref"]: i for i in parse_trca_report(_read("trca_armour_stone_2023.txt"))}
    assert items["10039751"]["winners"] == [
        ('1035477 Ontario Ltd. ("Glenn Windrem Trucking")', "$1,193,040"),
    ]
    assert len(items["10039751"]["winners"]) == 1
    assert items["10039753"]["winners"] == [
        ("Gott Natural Stone '99 Inc.", "$567,648"),
    ]
    assert len(items["10039753"]["winners"]) == 1


def test_armour_stone_bidder_list_is_clean_bullets():
    items = parse_trca_report(_read("trca_armour_stone_2023.txt"))
    bidders = items[0]["bidders"]
    assert len(bidders) == 4
    assert "H.R. Doornekamp Construction Ltd." in bidders
    assert "Metric Contracting Services Corporation" in bidders
    # The fused results table must NOT be mined: no bidder is a mangled wrap fragment.
    assert all(len(b) > 5 and not b.startswith("$") for b in bidders)


def test_vor_report_names_both_winners_without_amounts():
    items = parse_trca_report(_read("trca_vor_appraisal_2021.txt"))
    assert len(items) == 1 and items[0]["native_ref"] == "10036307"
    names = [w[0] for w in items[0]["winners"]]
    assert any("D. Bottero" in n for n in names)
    assert any("Newmark Knight Frank" in n for n in names)
    assert items[0]["bidders"] == [
        "D. Bottero and Associates Limited",
        "Newmark Knight Frank Canada Limited",
    ]


def test_escribe_document_urls_extracts_filestream_links():
    html = _read("trca_escribe_2023.html")
    urls = escribe_document_urls(html)
    assert urls, "expected at least one FileStream/Meeting link in the recorded page"
    assert all(u.startswith("https://pub-trca.escribemeetings.com/") for u in urls)


def test_store_trca_reports_lands_rows(conn):
    ids = seed_buyers(conn)
    text = _read("trca_armour_stone_2023.txt")
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) VALUES (?, 'agency_board', 'x', ?)",
        ("https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=14809", text))
    got = store_trca_reports(conn, ids["trca"])
    assert got["solicitations"] == 2         # 10039751 + 10039753
    assert got["awards"] == 2                # one winner each, with amounts (no dupes)
    assert got["bids"] == 8                  # 4 bidders x 2 refs
    row = conn.execute("SELECT award_amount_numeric FROM agency_award "
                       "WHERE native_ref='10039751'").fetchone()
    assert row[0] == 1193040.0


def test_download_skips_a_dead_url_without_aborting_the_run(tmp_path):
    """A single 404 among many report URLs must not kill the whole download (#135).

    Found live: legdocs 404s are routine across hundreds of URLs, and get_bytes
    re-raises 4xx, so an unguarded loop stored 1 of 859 reports then aborted the body.
    """
    import httpx

    from toronto_bids.sources.trca_board import _store_pending_pdfs
    from toronto_bids.store import db

    conn = db.connect(":memory:")
    db.init_db(conn)
    good = "https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=1"
    dead = "https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=2"
    for url in (good, dead):
        conn.execute("INSERT INTO background_pdf (url, kind) VALUES (?, 'agency_board')", (url,))
    conn.commit()

    def _raise_404(url):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("404", request=req,
                                    response=httpx.Response(404, request=req))

    class _FakeHttp:
        def get_bytes(self, url, **_kw):
            return b"%PDF-1.7\ntrailer\n" if url == good else _raise_404(url)

    n = _store_pending_pdfs(conn, _FakeHttp(), tmp_path, "%escribemeetings%",
                            lambda _m: None, "trca")
    assert n == 1                                            # the good one stored
    held = dict(conn.execute(
        "SELECT url, sha256 IS NOT NULL FROM background_pdf").fetchall())
    assert held[good] is True or held[good] == 1            # fetched + stored
    assert held[dead] in (False, 0, None)                  # left queued, not stored
    conn.close()


def test_meeting_detail_urls_from_calendar_json():
    """#137: the eSCRIBE calendar is client-rendered from the GetCalendarMeetings
    page-method, so meeting IDs come from its JSON, not static year-page anchors."""
    import json

    from toronto_bids.sources.trca_board import meeting_detail_urls
    cal = json.loads(_read("trca_getcalendarmeetings_2023q1.json"))
    urls = meeting_detail_urls(cal)
    assert len(urls) == 5                                    # every agenda'd meeting in Q1 2023
    assert all("Meeting.aspx?Id=" in u for u in urls)
    assert all(u.startswith("https://pub-trca.escribemeetings.com/") for u in urls)
    # a real GUID from the fixture, so the URL actually resolves to a detail page
    assert any("82fa331c-e7cb-4e9a-87e2-093a1a51899f" in u for u in urls)


def test_escribe_document_urls_decodes_html_entities():
    """Some detail-page hrefs encode the colon as &#58; (and & as &amp;). Decode both,
    or the URL is a malformed scheme that crashes the fetch (#137, found live)."""
    from toronto_bids.sources.trca_board import escribe_document_urls
    page = ('<a href="https&#58;//pub-trca.escribemeetings.com/'
            'FileStream.ashx?DocumentId=10661&amp;lang=en">report</a>')
    assert escribe_document_urls(page) == [
        "https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=10661&lang=en"]
