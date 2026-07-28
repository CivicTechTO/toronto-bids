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


# --- #175: a document is linked, page furniture is loaded ---------------------

def test_a_stylesheet_and_logo_served_through_filestream_are_not_documents():
    """eSCRIBE serves the meeting page's own print stylesheet and header logo through the
    same FileStream.ashx?DocumentId= handler as its PDFs. Matching href|src on any element
    indexed both as documents — 2 per meeting page, 460 across the corpus, re-fetched every
    night forever because they can never satisfy the %PDF check (#175). Fetched live
    2026-07-28: DocumentId=3253 is text/css, 3251 is image/jpeg."""
    from toronto_bids.sources.trca_board import escribe_asset_urls
    html = _read("trca_escribe_meeting_assets.html")

    docs = escribe_document_urls(html)
    assert [u.rsplit("=", 1)[-1] for u in docs] == ["3216", "3235", "3236"]
    assert not any("3253" in u or "3251" in u for u in docs)

    # The assets are still NAMED, not merely ignored — that is what unqueues the rows the
    # old regex already wrote, without guessing at which unheld rows were mistakes.
    assert sorted(u.rsplit("=", 1)[-1] for u in escribe_asset_urls(html)) == ["3251", "3253"]


def test_href_is_not_always_the_first_attribute_of_a_document_anchor():
    """Real markup: `<a class='Link' tabindex='15' href=...>`. An anchor pattern that
    demanded href immediately after `<a` would drop nearly every document on the page."""
    urls = escribe_document_urls(
        "<a class='Link' tabindex='15' href='filestream.ashx?DocumentId=99'>r.pdf</a>")
    assert urls == ["https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=99"]


def test_prune_unqueues_page_assets_but_never_a_document_or_held_bytes(tmp_path):
    """The prune is scoped to the exact mistake: unheld AND still shown as an asset by a
    page we just loaded. A dead document link (TRCA's own 404s — all 11 are minutes links,
    still linked from TRCA's pages) stays queued so a repaired link is picked up."""
    from toronto_bids.sources.trca_board import _prune_page_assets
    from toronto_bids.store import db

    base = "https://pub-trca.escribemeetings.com/"
    asset = base + "FileStream.ashx?DocumentId=3253"      # the stylesheet
    dead = base + "FileStream.ashx?DocumentId=5558"       # a real link that 404s at source
    held_asset = base + "FileStream.ashx?DocumentId=7777"  # bytes on disk — never deletable

    conn = db.connect(":memory:")
    db.init_db(conn)
    for url in (asset, dead):
        conn.execute("INSERT INTO background_pdf (url, kind) VALUES (?, 'agency_board')",
                     (url,))
    conn.execute("INSERT INTO background_pdf (url, kind, sha256) VALUES (?, 'agency_board', 'x')",
                 (held_asset,))
    conn.commit()

    assert _prune_page_assets(conn, {asset, held_asset}, lambda _m: None) == 1
    left = {r[0] for r in conn.execute("SELECT url FROM background_pdf").fetchall()}
    assert left == {dead, held_asset}
    conn.close()


def test_prune_does_nothing_when_no_page_loaded(tmp_path):
    """An empty asset set means the walk saw no page — never a licence to delete."""
    from toronto_bids.sources.trca_board import _prune_page_assets
    from toronto_bids.store import db

    conn = db.connect(":memory:")
    db.init_db(conn)
    conn.execute("INSERT INTO background_pdf (url, kind) VALUES "
                 "('https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=1', "
                 "'agency_board')")
    conn.commit()
    assert _prune_page_assets(conn, set(), lambda _m: None) == 0
    assert conn.execute("SELECT COUNT(*) FROM background_pdf").fetchone()[0] == 1
    conn.close()


def test_a_non_pdf_response_is_logged_rather_than_dropped_in_silence(tmp_path):
    """This branch was silent, so 460 wasted fetches a night hid behind an 11-line 404
    list (#175). The row still stays queued — only the silence is the bug."""
    from toronto_bids.sources.trca_board import _store_pending_pdfs
    from toronto_bids.store import db

    conn = db.connect(":memory:")
    db.init_db(conn)
    url = "https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=3253"
    conn.execute("INSERT INTO background_pdf (url, kind) VALUES (?, 'agency_board')", (url,))
    conn.commit()

    class _FakeHttp:
        def get_bytes(self, _url, **_kw):
            return b"@media Print{...}"

    lines = []
    assert _store_pending_pdfs(conn, _FakeHttp(), tmp_path, "%escribemeetings%",
                               lines.append, "trca") == 0
    assert any("not a PDF" in line and url in line for line in lines)
    assert conn.execute(
        "SELECT sha256 FROM background_pdf WHERE url=?", (url,)).fetchone()[0] is None
    conn.close()


def test_escribe_document_urls_decodes_html_entities():
    """Some detail-page hrefs encode the colon as &#58; (and & as &amp;). Decode both,
    or the URL is a malformed scheme that crashes the fetch (#137, found live)."""
    from toronto_bids.sources.trca_board import escribe_document_urls
    page = ('<a href="https&#58;//pub-trca.escribemeetings.com/'
            'FileStream.ashx?DocumentId=10661&amp;lang=en">report</a>')
    assert escribe_document_urls(page) == [
        "https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=10661&lang=en"]


# --- #138: precision + recall against the real corpus -----------------------

def _winners(name):
    items = parse_trca_report(_read(name + ".txt"))
    return items, [w for it in items for w in it["winners"]]


def test_recall_spelled_out_request_for_quotation_label():
    items, winners = _winners("trca_rfq_spelled_out_2019")
    assert items, "report with a spelled-out 'Request for Quotation No.' label must not be dropped"
    assert any("CDR Young" in (w[0] or "") for w in winners)


def test_recall_contract_hash_label():
    items, winners = _winners("trca_contract_label_2019")
    assert items, "report labelled 'Contract #NNNNNNNN' must not be dropped"
    assert any(w[0] == "Hawkins Contracting Services Ltd." for w in winners)


def test_multiline_winner_name_is_whole_not_truncated():
    _items, winners = _winners("trca_multiline_winner_2021")
    assert any(w[0] == "W.F. Baird & Associates Coastal Engineers Ltd." for w in winners)


def test_overcapture_report_now_yields_clean_names():
    items, winners = _winners("trca_overcapture_2021")
    assert winners, "the over-capture report must still yield awards"
    assert any("Wood Environment" in (w[0] or "") for w in winners)
    assert all(len(w[0]) <= 80 for w in winners)   # was 268757 chars before the fix


def test_no_trca_winner_ever_runs_on_across_all_fixtures():
    import pathlib as _pl
    for path in sorted(FIXTURES.glob("trca_*.txt")):
        for it in parse_trca_report(path.read_text()):
            for name, _amt in it["winners"]:
                assert name is None or (
                    len(name) <= 80 and "$" not in name
                    and " at a " not in name.lower() and "background" not in name.lower()
                ), f"{path.name}: run-on winner {name!r}"
