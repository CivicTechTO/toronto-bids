import pathlib
from contextlib import contextmanager

import pytest

from toronto_bids.sources.bid_award_panel import (
    agenda_date, agenda_is_missing, discover_meetings, parse_agenda)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "bid_award_panel"


def _fixture(name):
    return (FIXTURES / f"{name}.html").read_text()


def test_parses_every_award_item_on_a_real_agenda():
    items = parse_agenda(_fixture("2022.BA189"), "2022.BA189")
    assert len(items) == 7
    first = items[0]
    assert first["reference"] == "2022.BA189.1"
    assert first["document_numbers"] == ["3234668279"]
    assert first["title"].startswith("Award of Ariba Document Number 3234668279 to GHD Limited")
    assert "Aeration Blower System Upgrades" in first["title"]


def test_every_item_on_an_ariba_era_agenda_yields_a_document_number():
    items = parse_agenda(_fixture("2022.BA189"), "2022.BA189")
    assert all(i["document_numbers"] for i in items)


def test_pre_ariba_agenda_has_titles_but_nothing_to_join_on():
    items = parse_agenda(_fixture("2017.BA1"), "2017.BA1")
    assert len(items) == 1
    assert items[0]["document_numbers"] == []
    assert "Call Number 6032-16-3114" in items[0]["title"]
    assert "Road Weather Information Systems" in items[0]["title"]


def test_panel_housekeeping_is_not_an_award():
    refs = [i["reference"] for i in parse_agenda(_fixture("2017.BA1"), "2017.BA1")]
    assert "2017.BA1.1" not in refs
    assert refs == ["2017.BA1.2"]


def test_missing_meeting_is_detected_not_parsed():
    html = _fixture("2018.BA10")
    assert agenda_is_missing(html)
    assert not agenda_is_missing(_fixture("2022.BA189"))


@pytest.mark.parametrize("name,expected", [
    ("2022.BA189", "2022-05-25"),
    ("2017.BA1", "2017-01-04"),
])
def test_agenda_reports_its_own_date(name, expected):
    assert agenda_date(_fixture(name)) == expected


def test_agenda_date_of_a_missing_page_is_none():
    assert agenda_date(_fixture("2018.BA10")) is None


_MISSING = "<html><body>This meeting is not available.</body></html>"


def _fake_site(pages):
    def fetch(ref):
        date = pages.get(ref)
        if date is None:
            return _MISSING
        return f"<html><body>Meeting Date: Wednesday, {date}</body></html>"
    return fetch


_BA_BD_TERM_STARTS = [
    ("BD", 2009, "2006-2010", 105),
    ("BD", 2011, "2010-2014", 1),
    ("BD", 2015, "2014-2018", 1),
    ("BA", 2017, "2014-2018", 1),
    ("BA", 2019, "2018-2022", 1),
    ("BA", 2023, "2022-2026", 1),
]


def test_discovery_advances_the_session_year_prefix():
    found = discover_meetings(
        _fake_site({"2019.BA1": "2018-12-05", "2019.BA2": "2019-01-09"}),
        max_per_term=2, stop_after_misses=1, term_starts=[("BA", 2019, "2018-2022", 1)])
    assert set(found) == {"2019.BA1", "2019.BA2"}


def test_discovery_stops_after_consecutive_misses():
    calls = []
    pages = {"2017.BA1": "2017-01-04"}

    def fetch(ref):
        calls.append(ref)
        return _fake_site(pages)(ref)

    discover_meetings(fetch, max_per_term=200, stop_after_misses=2,
                      term_starts=_BA_BD_TERM_STARTS)
    assert len(calls) < 40


def test_discovery_handles_two_meetings_on_one_date():
    found = discover_meetings(
        _fake_site({"2017.BA1": "2017-01-04", "2017.BA2": "2017-01-04"}),
        max_per_term=2, stop_after_misses=1, term_starts=[("BA", 2017, "2014-2018", 1)])
    assert set(found) == {"2017.BA1", "2017.BA2"}


def test_a_closed_historical_term_skips_probing_past_its_known_end():
    calls = []

    def fetch(ref):
        calls.append(ref)
        return _fake_site({"2019.EP1": "2019-01-09", "2019.EP2": "2019-02-06"})(ref)

    terms = [("EP", 2019, "2018-2022", 1), ("EP", 2023, "2022-2026", 1)]
    found = discover_meetings(fetch, max_per_term=200, stop_after_misses=4,
                              term_starts=terms, closed_terms={"EP2018-2022": 2})
    assert set(found) == {"2019.EP1", "2019.EP2"}
    closed_term_calls = [c for c in calls if c.startswith(("2019.", "2020."))]
    assert closed_term_calls == ["2019.EP1", "2019.EP2"]


def test_a_historical_terms_boundary_is_reported_exactly_once():
    closures = []

    def fetch(ref):
        return _fake_site({"2019.EP1": "2019-01-09"})(ref)

    terms = [("EP", 2019, "2018-2022", 1), ("EP", 2023, "2022-2026", 1)]
    discover_meetings(fetch, max_per_term=10, stop_after_misses=2,
                      term_starts=terms, on_term_closed=lambda k, n: closures.append((k, n)))
    assert closures == [("EP2018-2022", 1)]


def test_the_open_final_term_is_never_reported_as_closed():
    closures = []
    discover_meetings(_fake_site({}), max_per_term=5, stop_after_misses=2,
                      term_starts=[("EP", 2023, "2022-2026", 1)],
                      on_term_closed=lambda k, n: closures.append((k, n)))
    assert closures == []


def test_a_mistaken_closed_entry_for_the_open_term_is_ignored():
    calls = []

    def fetch(ref):
        calls.append(ref)
        return _fake_site({"2019.EP1": "2019-01-09", "2019.EP2": "2019-02-06",
                           "2019.EP3": "2019-03-06"})(ref)

    found = discover_meetings(fetch, max_per_term=10, stop_after_misses=2,
                              term_starts=[("EP", 2019, "2018-2022", 1)],
                              closed_terms={"EP2018-2022": 1})
    assert set(found) == {"2019.EP1", "2019.EP2", "2019.EP3"}


def test_passing_neither_hook_reproduces_the_old_always_probe_behaviour():
    found = discover_meetings(
        _fake_site({"2017.BA1": "2017-01-04", "2017.BA2": "2017-01-04"}),
        max_per_term=2, stop_after_misses=1, term_starts=[("BA", 2017, "2014-2018", 1)])
    assert set(found) == {"2017.BA1", "2017.BA2"}


@contextmanager
def _fake_agenda_fetcher(pages, calls):
    def fetch(meeting: str) -> str:
        calls.append(meeting)
        return _fake_site(pages)(meeting)
    yield fetch


def test_scrape_agendas_persists_a_closed_terms_boundary_to_disk(tmp_path, monkeypatch):
    from toronto_bids.sources import bid_award_panel as bap
    terms = [("EP", 2019, "2018-2022", 1), ("EP", 2023, "2022-2026", 1)]
    pages = {"2019.EP1": "2019-01-09", "2019.EP2": "2019-02-06"}

    calls1 = []
    monkeypatch.setattr(bap, "agenda_fetcher",
                        lambda **_kw: _fake_agenda_fetcher(pages, calls1))
    found1 = bap.scrape_agendas(tmp_path, term_starts=terms)
    assert set(found1) == {"2019.EP1", "2019.EP2"}
    assert (tmp_path / ".closed_terms.json").exists()
    assert bap._load_closed_terms(tmp_path) == {"EP2018-2022": 2}

    # Second run should make zero live calls for the closed 2018-2022 term.
    calls2 = []
    monkeypatch.setattr(bap, "agenda_fetcher",
                        lambda **_kw: _fake_agenda_fetcher(pages, calls2))
    found2 = bap.scrape_agendas(tmp_path, term_starts=terms)
    assert set(found2) == {"2019.EP1", "2019.EP2"}
    assert not any(c.endswith(("EP1", "EP2", "EP3", "EP4")) and c.startswith("201")
                  for c in calls2), calls2


def test_indexes_staff_report_pdfs_against_the_item_that_owns_them():
    from toronto_bids.sources.bid_award_panel import parse_agenda_pdfs

    pdfs = parse_agenda_pdfs(_fixture("2022.BA189"), "2022.BA189")
    assert len(pdfs) == 7
    assert pdfs[0]["reference"] == "2022.BA189.1"
    assert pdfs[0]["kind"] == "bgrd"
    assert pdfs[0]["url"].endswith("backgroundfile-226166.pdf")
    assert [p["reference"] for p in pdfs] == [f"2022.BA189.{i}" for i in range(1, 8)]


def test_a_pdf_linked_twice_on_one_page_is_indexed_once():
    from toronto_bids.sources.bid_award_panel import parse_agenda_pdfs

    html = ("<html><body><h3>BA1.1 - Award of Doc1234567890 to X for Y</h3>"
            "<a href='https://www.toronto.ca/legdocs/mmis/2022/ba/bgrd/backgroundfile-1.pdf'>a</a>"
            "<a href='https://www.toronto.ca/legdocs/mmis/2022/ba/bgrd/backgroundfile-1.pdf'>b</a>"
            "</body></html>")
    assert len(parse_agenda_pdfs(html, "2022.BA1")) == 1


def test_a_pdf_before_any_item_is_attributed_to_the_meeting_not_dropped():
    from toronto_bids.sources.bid_award_panel import parse_agenda_pdfs

    html = ("<html><body>"
            "<a href='https://www.toronto.ca/legdocs/mmis/2022/ba/bgrd/backgroundfile-9.pdf'>x</a>"
            "<h3>BA1.1 - Award of Doc1234567890 to X for Y</h3>"
            "<a href='https://www.toronto.ca/legdocs/mmis/2022/ba/bgrd/backgroundfile-1.pdf'>y</a>"
            "</body></html>")
    pdfs = parse_agenda_pdfs(html, "2022.BA1")
    assert [p["reference"] for p in pdfs] == ["2022.BA1", "2022.BA1.1"]


def test_non_pdf_and_non_legdocs_links_are_ignored():
    from toronto_bids.sources.bid_award_panel import parse_agenda_pdfs

    html = ("<html><body><h3>BA1.1 - Award of Doc1234567890 to X for Y</h3>"
            "<a href='https://twitter.com/share?url=x'>tweet</a>"
            "<a href='mailto:bdc@toronto.ca'>mail</a>"
            "<a href='https://www.toronto.ca/legdocs/mmis/2022/ba/index.html'>not a pdf</a>"
            "</body></html>")
    assert parse_agenda_pdfs(html, "2022.BA1") == []


def test_store_background_pdfs_is_idempotent(conn):
    from toronto_bids.sources.bid_award_panel import store_background_pdfs

    agendas = {"2022.BA189": _fixture("2022.BA189")}
    assert store_background_pdfs(conn, agendas) == 7
    store_background_pdfs(conn, agendas)
    assert conn.execute("SELECT COUNT(*) FROM background_pdf").fetchone()[0] == 7


def test_indexing_records_the_url_without_downloading_anything(conn, tmp_path):
    from toronto_bids.sources.bid_award_panel import store_background_pdfs

    store_background_pdfs(conn, {"2022.BA189": _fixture("2022.BA189")})
    row = conn.execute("SELECT url, local_path, text FROM background_pdf LIMIT 1").fetchone()
    assert row["url"].startswith("https://www.toronto.ca/legdocs/")
    assert row["local_path"] is None
    assert row["text"] is None


def test_every_way_tmmis_says_no_is_treated_as_missing():
    assert agenda_is_missing("<html><body>This meeting is not available.</body></html>")
    assert agenda_is_missing("<html><body>Error The Published Report was not found.</body></html>")
    assert not agenda_is_missing(_fixture("2022.BA189"))


def test_the_bid_committee_series_is_parsed_like_the_panel():
    html = ("<html><body><h3>BD106.1 - Award of Request for Proposal No. 9117-16-5060 to "
            "Morrison Hershfield Limited for Contract Administration Services</h3>"
            "</body></html>")
    items = parse_agenda(html, "2016.BD106")
    assert len(items) == 1
    assert items[0]["reference"] == "2016.BD106.1"
    assert "Morrison Hershfield Limited" in items[0]["title"]


def test_parse_pre_ariba_awards_carries_the_reference():
    from toronto_bids.sources.bid_award_panel import parse_pre_ariba_awards
    html = ("<html><body><h3>BD106.3 - Award of Request for Quotation No. 3917-12-7226 to "
            "Accrue Contracting Ltd. for concrete cutting services in the amount of "
            "$420,000.00 net of all applicable taxes</h3></body></html>")
    items = parse_pre_ariba_awards(html, meeting="2016.BD106")
    assert items and items[0]["reference"] == "2016.BD106.3"
    assert items[0]["winner_raw"] == "Accrue Contracting Ltd."
    assert round(items[0]["award_value"]) == 420000


def test_match_pre_ariba_solicitations_records_a_unique_match(conn):
    from toronto_bids.store import db
    from toronto_bids.models import Solicitation, Award
    from toronto_bids.sources.bid_award_panel import match_pre_ariba_solicitations
    db.upsert_row(conn, Solicitation(document_number="5672751291", source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5672751291", supplier_name_raw="Accrue Contracting Ltd.",
                              award_amount="420000",
                              award_date="2016-05-01", source="odata"), overwrite=True)
    conn.commit()
    html = ("<html><body><h3>BD106.3 - Award of RFQ 3917-12-7226 to Accrue Contracting Ltd. "
            "for concrete cutting in the amount of $420,000.00 net of all applicable taxes"
            "</h3></body></html>")
    n = match_pre_ariba_solicitations(conn, {"2016.BD106": html})
    assert n == 1
    row = conn.execute("SELECT * FROM solicitation_link WHERE reference='2016.BD106.3'").fetchone()
    assert row["document_number"] == "5672751291" and row["method"] == "council_pre_ariba"


def test_match_pre_ariba_solicitations_drops_a_non_unique_match(conn):
    from toronto_bids.store import db
    from toronto_bids.models import Solicitation, Award
    from toronto_bids.sources.bid_award_panel import match_pre_ariba_solicitations
    for doc in ("1111111111", "2222222222"):
        db.upsert_row(conn, Solicitation(document_number=doc, source="odata"), overwrite=True)
        db.upsert_row(conn, Award(document_number=doc, supplier_name_raw="Accrue Contracting Ltd.",
                                  award_amount="420000",
                                  award_date="2016-05-01", source="odata"), overwrite=True)
    conn.commit()
    html = ("<html><body><h3>BD106.3 - Award to Accrue Contracting Ltd. for x in the amount of "
            "$420,000.00 net of all applicable taxes</h3></body></html>")
    assert match_pre_ariba_solicitations(conn, {"2016.BD106": html}) == 0
    assert conn.execute("SELECT COUNT(*) FROM solicitation_link").fetchone()[0] == 0
