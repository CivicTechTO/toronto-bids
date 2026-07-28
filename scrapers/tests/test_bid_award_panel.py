"""#65: mining solicitation titles from Bid Award Panel agendas.

Fixtures are real pages fetched from TMMIS, trimmed to <main>:
  2022.BA189 — Ariba era: every item carries a 10-digit document number
  2017.BA1   — pre-Ariba: awards identified by Call Number, no document number to join on
  2018.BA10  — a reference that does not exist (probing is how references are found)
"""
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
    """2017 identifies awards by Call Number (6032-16-3114), not a 10-digit Ariba number.

    The item is still returned — an empty document_numbers list is a fact about the City's
    history, not a parse failure, and #77 wants these titles via supplier+amount instead.
    """
    items = parse_agenda(_fixture("2017.BA1"), "2017.BA1")
    assert len(items) == 1
    assert items[0]["document_numbers"] == []
    assert "Call Number 6032-16-3114" in items[0]["title"]
    assert "Road Weather Information Systems" in items[0]["title"]


def test_panel_housekeeping_is_not_an_award():
    """2017.BA1.1 is 'Election of Vice Chair' — real item, not a procurement."""
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
    """A probe confirms it hit the right meeting by the date the page states."""
    assert agenda_date(_fixture(name)) == expected


def test_agenda_date_of_a_missing_page_is_none():
    assert agenda_date(_fixture("2018.BA10")) is None


# --- discovery walk (no browser: fetch is injected) -------------------------------------

_MISSING = "<html><body>This meeting is not available.</body></html>"


def _fake_site(pages):
    def fetch(ref):
        date = pages.get(ref)
        if date is None:
            return _MISSING
        return f"<html><body>Meeting Date: Wednesday, {date}</body></html>"
    return fetch


# The BA/BD term list used to be the in-repo default (TERM_STARTS); the Bid Award Panel is
# abolished and there is no default anymore, so these tests pass their own copy to exercise
# the same multi-term walk.
_BA_BD_TERM_STARTS = [
    ("BD", 2009, "2006-2010", 105),
    ("BD", 2011, "2010-2014", 1),
    ("BD", 2015, "2014-2018", 1),
    ("BA", 2017, "2014-2018", 1),
    ("BA", 2019, "2018-2022", 1),
    ("BA", 2023, "2022-2026", 1),
]


def test_discovery_advances_the_session_year_prefix():
    """The year prefix is a session year that rolls over in November, mid-term."""
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
    # It must not walk to 200 just because meeting 2 is absent. Six series/term pairs are
    # walked (BD 2009/2011/2015, BA 2017/2019/2023), each giving up after its own misses,
    # so the floor is ~6 * stop_after_misses * 2 candidate prefixes — nowhere near 6 * 200.
    assert len(calls) < 40


def test_discovery_handles_two_meetings_on_one_date():
    """2017.BA1 and 2017.BA2 are both 2017-01-04 — which is why references cannot be
    inferred from date order, and why probing verifies against the page instead."""
    found = discover_meetings(
        _fake_site({"2017.BA1": "2017-01-04", "2017.BA2": "2017-01-04"}),
        max_per_term=2, stop_after_misses=1, term_starts=[("BA", 2017, "2014-2018", 1)])
    assert set(found) == {"2017.BA1", "2017.BA2"}


# --- closed-term caching (#177) ----------------------------------------------------------

def test_a_closed_historical_term_skips_probing_past_its_known_end():
    """Every term but the LAST is historical by construction (a newer one already follows
    it), so once its miss boundary is known, re-probing it is pure waste — this was most of
    a nightly's cost for EP/Zoo (#177). Passing the boundary must not touch the network past
    it."""
    calls = []

    def fetch(ref):
        calls.append(ref)
        return _fake_site({"2019.EP1": "2019-01-09", "2019.EP2": "2019-02-06"})(ref)

    terms = [("EP", 2019, "2018-2022", 1), ("EP", 2023, "2022-2026", 1)]
    found = discover_meetings(fetch, max_per_term=200, stop_after_misses=4,
                              term_starts=terms, closed_terms={"EP2018-2022": 2})
    assert set(found) == {"2019.EP1", "2019.EP2"}
    # Only the 2 known-real meetings of the closed term (session years 2019/2020) were
    # probed — never the dead miss range past EP2's boundary. The open term (2023/2024)
    # walking into ITS OWN misses is separate and expected.
    closed_term_calls = [c for c in calls if c.startswith(("2019.", "2020."))]
    assert closed_term_calls == ["2019.EP1", "2019.EP2"]


def test_a_historical_terms_boundary_is_reported_exactly_once():
    """`on_term_closed` is the hook a caller persists from — it must fire once, with the
    confirmed last real meeting, and never for the still-open final term."""
    closures = []

    def fetch(ref):
        return _fake_site({"2019.EP1": "2019-01-09"})(ref)

    terms = [("EP", 2019, "2018-2022", 1), ("EP", 2023, "2022-2026", 1)]
    discover_meetings(fetch, max_per_term=10, stop_after_misses=2,
                      term_starts=terms, on_term_closed=lambda k, n: closures.append((k, n)))
    assert closures == [("EP2018-2022", 1)]


def test_the_open_final_term_is_never_reported_as_closed():
    """A term that legitimately runs dry this run (e.g. no meetings posted yet) is not
    historical — it is the LAST entry, so it must go on being probed live every run."""
    closures = []
    discover_meetings(_fake_site({}), max_per_term=5, stop_after_misses=2,
                      term_starts=[("EP", 2023, "2022-2026", 1)],
                      on_term_closed=lambda k, n: closures.append((k, n)))
    assert closures == []


def test_a_mistaken_closed_entry_for_the_open_term_is_ignored():
    """closed_terms is a caller-provided cache; a bug that wrote an entry for the currently
    open term must never freeze it — the open term is always probed live."""
    calls = []

    def fetch(ref):
        calls.append(ref)
        return _fake_site({"2019.EP1": "2019-01-09", "2019.EP2": "2019-02-06",
                           "2019.EP3": "2019-03-06"})(ref)

    found = discover_meetings(fetch, max_per_term=10, stop_after_misses=2,
                              term_starts=[("EP", 2019, "2018-2022", 1)],
                              closed_terms={"EP2018-2022": 1})   # would wrongly stop at EP1
    assert set(found) == {"2019.EP1", "2019.EP2", "2019.EP3"}


def test_passing_neither_hook_reproduces_the_old_always_probe_behaviour():
    """closed_terms/on_term_closed are additive — a caller that passes neither (like the
    existing BA/BD tests) must see exactly the pre-#177 discovery."""
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
    """The first run confirms EP2018-2022 ends at meeting 2 and must write that down; a
    second run must read it back and never re-probe the dead range (#177)."""
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

    # Second run: EP1/EP2 are cached on disk (served without a live call at all), and the
    # persisted boundary means nothing past them is probed either — the closed term costs
    # ZERO live calls this run. The still-open 2022-2026 term is unaffected and keeps
    # probing its own miss range live, which is correct: only a closed term can be cached.
    calls2 = []
    monkeypatch.setattr(bap, "agenda_fetcher",
                        lambda **_kw: _fake_agenda_fetcher(pages, calls2))
    found2 = bap.scrape_agendas(tmp_path, term_starts=terms)
    assert set(found2) == {"2019.EP1", "2019.EP2"}
    assert not any(c.endswith(("EP1", "EP2", "EP3", "EP4")) and c.startswith("201")
                  for c in calls2), calls2


# --- staff-report PDF index (#68) --------------------------------------------------------

def test_indexes_staff_report_pdfs_against_the_item_that_owns_them():
    """Spec §2.3 says background-file PDFs have 'no index'. The agendas are that index."""
    from toronto_bids.sources.bid_award_panel import parse_agenda_pdfs

    pdfs = parse_agenda_pdfs(_fixture("2022.BA189"), "2022.BA189")
    assert len(pdfs) == 7
    assert pdfs[0]["reference"] == "2022.BA189.1"
    assert pdfs[0]["kind"] == "bgrd"
    assert pdfs[0]["url"].endswith("backgroundfile-226166.pdf")
    # every PDF belongs to a distinct item, in document order
    assert [p["reference"] for p in pdfs] == [f"2022.BA189.{i}" for i in range(1, 8)]


def test_a_pdf_linked_twice_on_one_page_is_indexed_once():
    """The City emits each link twice; the index must not double it."""
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
    assert row["local_path"] is None    # the index is the deliverable; bytes are a later pass
    assert row["text"] is None


def test_every_way_tmmis_says_no_is_treated_as_missing():
    """TMMIS has more than one error page, and missing one records it AS an agenda.
    'This meeting is not available.' -> 2018.BA10; 'The Published Report was not found.'
    -> 2007.BD1, which the first BD run cached as if it were real."""
    assert agenda_is_missing("<html><body>This meeting is not available.</body></html>")
    assert agenda_is_missing("<html><body>Error The Published Report was not found.</body></html>")
    assert not agenda_is_missing(_fixture("2022.BA189"))


def test_the_bid_committee_series_is_parsed_like_the_panel():
    """BD (2009-2016) is BA's predecessor and its agendas are structurally identical."""
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
    # two solicitations share (supplier, value) -> ambiguous -> no link recorded (a wrong merge is worse than none)
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
