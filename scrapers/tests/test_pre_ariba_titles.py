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
    row = conn.execute("SELECT title, title_source FROM solicitation").fetchone()
    assert "Call Number 6032-16-3114" in row["title"]
    assert row["title_source"] == "council_pre_ariba"


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
                         "WHERE title_source='council_pre_ariba'").fetchone()[0]
    assert named == "1234567890"


def test_the_looser_key_still_matches_the_real_2017_agenda(conn):
    """'MeteoGroup Weather Services Canada Inc.' — 'Canada' and 'Inc' are legal noise, so the
    match must ride on 'meteogroup' / 'weather' / 'services'."""
    db.upsert_row(conn, Solicitation("1234567890", title=None, source="odata"), overwrite=True)
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Meteogroup Weather Services Ltd",
                              award_amount="646356.00", source="odata"), overwrite=True)
    conn.commit()
    assert match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")}) == 1


def test_a_sync_cannot_clobber_a_recovered_title_or_its_provenance(conn):
    """The bug this column exists for.

    `source` records which source last wrote the ROW, and the OData spine owns it: every sync
    re-upserts every spine row with overwrite=True. Title provenance stored there was silently
    reset to 'odata' on the next sync — 890 recovered titles ended up claiming to come from
    the City's feed, while the titles themselves survived because COALESCE only guards NULL.
    """
    db.upsert_row(conn, Solicitation("1234567890", title=None, source="odata"), overwrite=True)
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="MeteoGroup Weather Services Canada Inc.",
                              award_amount="646356.00", source="odata"), overwrite=True)
    conn.commit()
    assert match_pre_ariba_titles(conn, {"2017.BA1": _fixture("2017.BA1")}) == 1

    # The next `tb sync`: the spine re-upserts the row, still with no title of its own.
    db.upsert_row(conn, Solicitation("1234567890", title=None, source="odata"), overwrite=True)
    conn.commit()

    row = conn.execute("SELECT title, source, title_source FROM solicitation").fetchone()
    assert "Call Number 6032-16-3114" in row["title"]      # the title survives
    assert row["source"] == "odata"                        # the spine owns `source`...
    assert row["title_source"] == "council_pre_ariba"      # ...but not the provenance


# --- composite awards (#216): title_source records a REAL title, never a NULL one ----------

def _composite(conn, title, supplier="Builder Co.", value=420000.0, call="3905-10-0097"):
    conn.execute(
        "INSERT INTO composite_award (call_number, title, supplier_name_raw, award_value, "
        "award_value_numeric, source) VALUES (?, ?, ?, ?, ?, 'composite')",
        (call, title, supplier, f"${value:,.2f}", value),
    )


def _titleless_award(conn, doc="1234567890", supplier="Builder Co. Ltd", amount="420000.00"):
    db.upsert_row(conn, Solicitation(doc, title=None, source="odata"), overwrite=True)
    db.upsert_row(conn, Award(doc, supplier_name_raw=supplier, award_amount=amount,
                              source="odata"), overwrite=True)


def test_a_title_less_composite_award_stamps_nothing_and_is_not_counted(conn):
    """A composite row can reach the matcher with title NULL (the model returned none; before
    #216 the backfill wrote none at all). It used to write title=NULL,
    title_source='council_composite' and count the row as filled — provenance for a title that
    does not exist."""
    from toronto_bids.sources.bid_award_panel import match_composite_titles

    _titleless_award(conn)
    _composite(conn, None)
    _composite(conn, "   ", call="3905-10-0098")   # blank is no title either
    conn.commit()
    assert match_composite_titles(conn) == 0
    row = conn.execute("SELECT title, title_source FROM solicitation").fetchone()
    assert row["title"] is None
    assert row["title_source"] is None


def test_a_titled_composite_award_still_fills(conn):
    from toronto_bids.sources.bid_award_panel import match_composite_titles

    _titleless_award(conn)
    _composite(conn, "Supply and Delivery of Road Salt")
    conn.commit()
    assert match_composite_titles(conn) == 1
    row = conn.execute("SELECT title, title_source FROM solicitation").fetchone()
    assert row["title"] == "Supply and Delivery of Road Salt"
    assert row["title_source"] == "council_composite"


def test_a_title_less_item_cannot_shadow_a_real_one_for_the_same_document(conn):
    """The first match per document wins, so a NULL-title item seen first must not claim it."""
    from toronto_bids.sources.bid_award_panel import match_on_supplier_and_value

    _titleless_award(conn)
    conn.commit()
    items = [
        {"title": None, "winner_raw": "Builder Co.", "award_value": 420000.0},
        {"title": "Road Salt", "winner_raw": "Builder Co.", "award_value": 420000.0},
    ]
    assert match_on_supplier_and_value(conn, items, "council_composite") == 1
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] == "Road Salt"


def test_composite_titles_flow_from_the_backfill_to_the_matcher(conn):
    """#216 end to end: the backfill now writes the contract's title under a normalized Call
    Number, and the matcher (which keys on value + supplier, not the call number) uses it."""
    import json

    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction
    from toronto_bids.sources.bid_award_panel import match_composite_titles
    from toronto_bids.store.db import mark_extracted

    _titleless_award(conn)
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, reference) "
        "VALUES ('https://example.com/c.pdf', 'bgrd', 'cmp', 'text', '2011.BD5.1')"
    )
    conn.commit()
    mark_extracted(conn, "cmp", EXTRACTOR_VERSION, result_json=json.dumps({"contracts": [{
        "reference": "Request for Quotation No. 3905\u201310\u20130097",
        "title": "Supply and Delivery of Road Salt",
        "awards": [{"supplier_name": "Builder Co.", "amount_raw": "$420,000.00"}],
    }]}))

    backfill_from_extraction(conn, "composite")
    assert conn.execute("SELECT call_number FROM composite_award").fetchone()[0] == "3905-10-0097"
    assert match_composite_titles(conn) == 1
    row = conn.execute("SELECT title, title_source FROM solicitation").fetchone()
    assert row["title"] == "Supply and Delivery of Road Salt"
    assert row["title_source"] == "council_composite"
