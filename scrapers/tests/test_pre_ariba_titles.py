"""#77: naming pre-Ariba solicitations by (supplier, award value) rather than identifier.

Toronto adopted Ariba ~2019. A 2017 agenda identifies its award by Call Number and our spine
is keyed on a 10-digit Ariba number backfilled later, so there is no identifier in common.
The item names its winner and its value, and `award` holds both — that is the join.
"""
import pathlib

from toronto_bids.models import Award, Solicitation
from toronto_bids.sources.bid_award_panel import match_pre_ariba_titles, parse_pre_ariba_awards
from toronto_bids.store import db

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "bid_award_panel"


def _fixture(name):
    return (FIXTURES / f"{name}.html").read_text()


def test_pulls_the_winner_and_value_off_a_real_pre_ariba_item():
    items = parse_pre_ariba_awards(_fixture("2017.BA1"))
    assert len(items) == 1
    item = items[0]
    assert item["winner_raw"] == "MeteoGroup Weather Services Canada Inc."
    assert item["award_value"] == 646356.0
    assert "Call Number 6032-16-3114" in item["title"]


def test_takes_the_net_of_taxes_figure_not_the_other_two():
    """Council publishes three amounts. Calibrated against 980 Ariba-era items where the
    document number gives ground truth: 'net of all applicable taxes' matched award_amount
    820 times, 'net of HST recoveries' 4, 'including HST' 0.

    2017.BA1.2 publishes $646,356 net of taxes / $730,382 including HST / $657,732 net of
    HST recoveries. Picking the wrong one would silently never match.
    """
    assert parse_pre_ariba_awards(_fixture("2017.BA1"))[0]["award_value"] == 646356.0


def test_items_naming_a_document_number_are_left_alone():
    """2019+ items join on the identifier; guessing there would be strictly worse."""
    assert parse_pre_ariba_awards(_fixture("2022.BA189")) == []


def test_matches_a_title_less_award_and_names_it(conn):
    db.upsert_row(conn, Solicitation("1234567890", title=None, source="odata"), overwrite=True)
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="MeteoGroup Weather Services Canada Inc.",
                              award_amount="646356.00", source="odata"), overwrite=True)
    conn.commit()
    assert match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")}) == 1
    row = conn.execute("SELECT title, source FROM solicitation").fetchone()
    assert "Call Number 6032-16-3114" in row["title"]
    assert row["source"] == "council_pre_ariba"


def test_an_ambiguous_match_is_dropped_not_guessed(conn):
    """21 of 5,443 title-less awards share a (supplier, amount) with a different document.
    A wrong title is worse than none."""
    for doc in ("1234567890", "9876543210"):
        db.upsert_row(conn, Solicitation(doc, title=None, source="odata"), overwrite=True)
        db.upsert_row(conn, Award(doc, supplier_name_raw="MeteoGroup Weather Services Canada Inc.",
                                  award_amount="646356.00", source="odata"), overwrite=True)
    conn.commit()
    assert match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")}) == 0
    assert conn.execute("SELECT COUNT(*) FROM solicitation WHERE title IS NULL").fetchone()[0] == 2


def test_a_wrong_amount_does_not_match(conn):
    db.upsert_row(conn, Solicitation("1234567890", title=None, source="odata"), overwrite=True)
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="MeteoGroup Weather Services Canada Inc.",
                              award_amount="999999.00", source="odata"), overwrite=True)
    conn.commit()
    assert match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")}) == 0


def test_never_overrides_a_title_the_city_published(conn):
    db.upsert_row(conn, Solicitation("1234567890", title="Urban Forestry Supplies",
                                     source="odata"), overwrite=True)
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="MeteoGroup Weather Services Canada Inc.",
                              award_amount="646356.00", source="odata"), overwrite=True)
    conn.commit()
    match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")})
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] == "Urban Forestry Supplies"


def test_ariba_era_agendas_are_skipped_entirely(conn):
    db.upsert_row(conn, Solicitation("3234668279", title=None, source="odata"), overwrite=True)
    conn.commit()
    assert match_pre_ariba_titles(conn, {"2022.BA189": _fixture("2022.BA189")}) == 0


def test_is_idempotent(conn):
    db.upsert_row(conn, Solicitation("1234567890", title=None, source="odata"), overwrite=True)
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="MeteoGroup Weather Services Canada Inc.",
                              award_amount="646356.00", source="odata"), overwrite=True)
    conn.commit()
    assert match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")}) == 1
    assert match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")}) == 0
