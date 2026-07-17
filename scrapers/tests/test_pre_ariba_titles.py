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


# --- how loose the supplier check is, and why it is safe --------------------------------

def test_supplier_tokens_absorbs_the_variance_actually_seen():
    """Measured misses under the strict key: Ltd/Limited, &/and, a leading 'The'."""
    from toronto_bids.sources.bid_award_panel import supplier_tokens

    def matches(a, b):
        return bool(supplier_tokens(a) & supplier_tokens(b))

    assert matches("Sanscon Construction Limited", "Sanscon Construction Ltd.")
    assert matches("Liftsafe Engineering & Service Group",
                   "Liftsafe Engineering and Service Group")
    assert matches("The Municipal Infrastructure Group,", "Municipal Infrastructure Group Ltd")
    assert matches("J&J Trailers Manufacturers and Sales", "J J Trailers Manufacturers Sales")


def test_supplier_tokens_drops_legal_form_so_it_cannot_carry_a_match_alone():
    """'Inc' and 'Ltd' must not be the shared token — every firm has one."""
    from toronto_bids.sources.bid_award_panel import supplier_tokens

    assert supplier_tokens("Acme Inc.") == {"acme"}
    assert not (supplier_tokens("Acme Inc.") & supplier_tokens("Beta Ltd."))


def test_two_different_firms_at_the_same_value_are_dropped(conn):
    """The value carries the match, so the supplier check is what stops a coincidence."""
    for doc, supplier in (("1234567890", "MeteoGroup Weather Services Canada Inc."),
                          ("9876543210", "Totally Different Paving Corp.")):
        db.upsert_row(conn, Solicitation(doc, title=None, source="odata"), overwrite=True)
        db.upsert_row(conn, Award(doc, supplier_name_raw=supplier,
                                  award_amount="646356.00", source="odata"), overwrite=True)
    conn.commit()
    # Only MeteoGroup shares a token with the agenda's winner, so the match stays unique.
    assert match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")}) == 1
    named = conn.execute("SELECT document_number FROM solicitation "
                         "WHERE source='council_pre_ariba'").fetchone()[0]
    assert named == "1234567890"


def test_the_looser_key_still_matches_the_real_2017_agenda(conn):
    """'MeteoGroup Weather Services Canada Inc.' — 'Canada' and 'Inc' are legal noise, so the
    match must ride on 'meteogroup' / 'weather' / 'services'."""
    db.upsert_row(conn, Solicitation("1234567890", title=None, source="odata"), overwrite=True)
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Meteogroup Weather Services Ltd",
                              award_amount="646356.00", source="odata"), overwrite=True)
    conn.commit()
    assert match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")}) == 1
