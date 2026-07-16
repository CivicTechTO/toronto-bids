"""#65: mining solicitation titles from Bid Award Panel agendas.

Fixtures are real pages fetched from TMMIS, trimmed to <main>:
  2022.BA189 — Ariba era: every item carries a 10-digit document number
  2017.BA1   — pre-Ariba: awards identified by Call Number, no document number to join on
  2018.BA10  — a reference that does not exist (probing is how references are found)
"""
import pathlib

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


def test_discovery_advances_the_session_year_prefix():
    """The year prefix is a session year that rolls over in November, mid-term."""
    found = discover_meetings(
        _fake_site({"2019.BA1": "2018-12-05", "2019.BA2": "2019-01-09"}),
        max_per_term=2, stop_after_misses=1)
    assert set(found) == {"2019.BA1", "2019.BA2"}


def test_discovery_stops_after_consecutive_misses():
    calls = []
    pages = {"2017.BA1": "2017-01-04"}

    def fetch(ref):
        calls.append(ref)
        return _fake_site(pages)(ref)

    discover_meetings(fetch, max_per_term=200, stop_after_misses=2)
    # It must not walk to 200 just because meeting 2 is absent.
    assert len(calls) < 20


def test_discovery_handles_two_meetings_on_one_date():
    """2017.BA1 and 2017.BA2 are both 2017-01-04 — which is why references cannot be
    inferred from date order, and why probing verifies against the page instead."""
    found = discover_meetings(
        _fake_site({"2017.BA1": "2017-01-04", "2017.BA2": "2017-01-04"}),
        max_per_term=2, stop_after_misses=1)
    assert set(found) == {"2017.BA1", "2017.BA2"}
