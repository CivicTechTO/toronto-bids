"""#96: expanding the archive backwards past the City's feed.

The 2009-2012 composite reports hold 799 awards for years the feed publishes 13 for. They
carry a Call Number and no document_number, so they live in their own keyspace — see the
composite_award comment in schema.sql.

Every test here pins a failure the corpus actually produced, not a hypothetical.
"""
import pathlib

from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.bid_award_panel import (
    parse_composite_appendices, store_composite_awards)
from toronto_bids.store import db

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "bid_award_panel"
REPORT = (FIXTURES / "2012.BD100.report.txt").read_text()


def _seed_report(conn, reference="2012.BD100.1", text=REPORT):
    db.upsert_row(conn, BackgroundPdf(
        url=f"https://www.toronto.ca/legdocs/mmis/2012/bd/bgrd/backgroundfile-52208.pdf",
        reference=reference, kind="bgrd", text=text), overwrite=True)
    conn.commit()


# --- the value, which is the whole point of ingesting these -----------------------------

def test_the_award_value_is_numeric_not_just_stored(conn):
    """The bug this nearly shipped with: award_value held the whole matched phrase
    ("$420,000.00 net of all applicable taxes"), amount.py:parse_amount is strict and
    refuses it, and award_value_numeric came out NULL on all 1,030 rows — every SUM
    silently $0. A NULL numeric beside a non-NULL raw string is invisible until someone
    aggregates it."""
    _seed_report(conn)
    store_composite_awards(conn)
    rows = conn.execute("SELECT award_value, award_value_numeric FROM composite_award "
                        "ORDER BY award_value_numeric").fetchall()
    assert [r["award_value_numeric"] for r in rows] == [284752.25, 420000.00]
    assert all(r["award_value"] is not None for r in rows)
    assert conn.execute("SELECT COUNT(*) FROM composite_award "
                        "WHERE award_value_numeric IS NULL").fetchone()[0] == 0


def test_takes_the_initial_term_not_the_option_years():
    """Measured, not chosen: on the 139 appendices whose award the City's feed also
    published, the FIRST net-of-taxes figure equals the feed's award_amount 137 times
    (98.6%). The option-year and 'total potential' figures beside it can be twice as large,
    so taking the wrong one would overstate the archive by billions."""
    text = ("Call No:\nRFQ 3920-10-0133\nDescription:\nAggregates.\n\n"
            "Recommended Bidder:\nCDR Youngs Aggregates Inc.\n\nContract Award Value:\n"
            "January 1, 2011 to June 30, 2012\n"
            "$2,589,782.50 net of all applicable taxes and charges;\n"
            "Option Period 1 - July 1, 2012 to January 30, 2014\n"
            "$2,690,020.80 net of all applicable taxes and charges;\n"
            "The total potential contract award is\n"
            "$5,279,803.30 net of all applicable taxes and charges.\n")
    assert [i["award_value"] for i in parse_composite_appendices(text)] == [2589782.50]


# --- the field parser, which the corpus broke in five distinct ways ----------------------

def test_a_label_with_punctuation_does_not_swallow_the_value_block():
    """'Contract Award Value*:' (84 blocks) and 'Contract Award Values*:' (19) carry an
    asterisk, so a [A-Za-z ]+ lookahead runs straight past the label and the supplier
    swallows the amounts behind it."""
    text = ("Call No:\nTender Call 1-2011\nDescription:\nPaving.\n"
            "Recommended Bidder:\nGio Contracting Inc.\n"
            "Contract Award Value*:\n$3,000,420.00 net of all applicable taxes and charges\n")
    items = parse_composite_appendices(text)
    assert [i["winner_raw"] for i in items] == ["Gio Contracting Inc."]
    assert items[0]["award_value"] == 3000420.00


def test_a_section_heading_that_lost_its_colon_still_ends_the_field():
    """pdftotext sometimes drops the colon, which is invisible to a label matcher. 24 supplier
    names ran past their firm into the value block, one to 735 characters."""
    text = ("Call No:\nTender Call 2-2012\nDescription:\nDisposal.\n"
            "Recommended Bidder:\n1612372 Ontario Inc. o/a Mini Millennium\n"
            "Contract Award Value\nDate of award to December 31, 2011\n"
            "$596,475.00 net of all applicable taxes and charges\n")
    assert parse_composite_appendices(text)[0]["winner_raw"] == \
        "1612372 Ontario Inc. o/a Mini Millennium"


def test_page_furniture_is_never_mistaken_for_a_supplier():
    """An appendix spanning a page break drops the running header and page number into the
    middle of a field. One was captured verbatim as the supplier name."""
    text = ("Call No:\nTender Call 3-2011\nDescription:\nWork.\n"
            "Recommended Bidder:\nAcme Paving Inc.\n"
            "Contract Awards – Bid Committee Composite Report – January 26, 2011\n"
            "4\n"
            "APPENDIX #2\n"
            "Contract Award Value:\n$100,000.00 net of all applicable taxes\n")
    assert parse_composite_appendices(text)[0]["winner_raw"] == "Acme Paving Inc."


def test_a_footnote_is_not_part_of_the_supplier_name():
    """'*Tender price corrected for mathematical errors...' belongs to no field."""
    text = ("Call No:\nTender Call 4-2011\nDescription:\nWork.\n"
            "Recommended Bidder:\nOJCR Construction Ltd.\n"
            "*Tender price corrected for mathematical errors. Purchasing has verified.\n"
            "Contract Award Value:\n$100,000.00 net of all applicable taxes\n")
    assert parse_composite_appendices(text)[0]["winner_raw"] == "OJCR Construction Ltd."


def test_a_wrapped_description_survives_a_blank_line():
    """Council wraps long descriptions across blank lines, so a blank must not end a field —
    only a new label may."""
    text = ("Call No:\nRFQ 3905-10-0097\nDescription:\nFor the supply of asphalt\n\n"
            "to various locations within the City.\n"
            "Recommended Bidder:\nLafarge Paving\n"
            "Contract Award Value:\n$100.00 net of all applicable taxes\n")
    assert "various locations" in parse_composite_appendices(text)[0]["description"]


# --- split awards: real, and refused rather than fused -----------------------------------

def test_an_enumerated_split_award_is_flagged_not_fused():
    """One call, three winners, each with its own value section. Recording the fused name as
    one firm would invent a supplier and hand it the first winner's money."""
    text = ("Call No:\nRFQ 3920-10-0133\nDescription:\nAggregates.\n"
            "Recommended Bidders:\n1. CDR Youngs Aggregates Inc.\n2. Lafarge Aggregates\n"
            "3. Vicdom Sand & Gravel (Ontario) Limited\n"
            "Contract Award Value:\n$100.00 net of all applicable taxes\n")
    assert parse_composite_appendices(text)[0]["split_award"] is True


def test_a_segmented_split_award_is_flagged():
    """The other way council writes a split: by naming the segments."""
    for name in ('Area "A" – A&F DiCarlo Construction Inc. Area "B" – Pave-Tar Ltd.',
                 'Part "A" and Part "C" – SCI Interiors Part "B" – POI Business Interiors',
                 'Project 1 - GENIVAR Inc. Project 2 – Stantec Consulting Ltd.'):
        text = (f"Call No:\nTender Call 5-2011\nDescription:\nWork.\nRecommended Bidders:\n"
                f"{name}\nContract Award Value:\n$100.00 net of all applicable taxes\n")
        assert parse_composite_appendices(text)[0]["split_award"] is True, name


def test_a_single_firm_with_a_long_alias_is_not_a_split():
    """Real firms run to 71 characters via aliases. Counting legal-form tokens instead of
    length looks smarter and is not: it flags 81 rows, nearly all single firms carrying two
    ('Canadian Tire Corporation, Limited', 'A.J. Stone Co. Ltd.')."""
    for name in ("St. Marys Cement Inc. (Canada) d.b.a. Canada Building Materials Company",
                 "Corporate Express, Canada Inc. operating as Staples Advantage Canada",
                 "Holcim (Canada) Inc. C.O.B. as Dufferin Construction Company",
                 "Canadian Tire Corporation, Limited",
                 "A.J. Stone Co. Ltd."):
        text = (f"Call No:\nTender Call 6-2011\nDescription:\nWork.\nRecommended Bidder:\n"
                f"{name}\nContract Award Value:\n$100.00 net of all applicable taxes\n")
        assert parse_composite_appendices(text)[0]["split_award"] is False, name


def test_split_awards_are_skipped_rather_than_stored_wrong(conn):
    text = ("Call No:\nRFQ 3920-10-0133\nDescription:\nAggregates.\n"
            "Recommended Bidders:\n1. CDR Youngs Aggregates Inc.\n2. Lafarge Aggregates\n"
            "Contract Award Value:\n$100.00 net of all applicable taxes\n")
    _seed_report(conn, reference="2011.BD5.1", text=text)
    assert store_composite_awards(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM composite_award").fetchone()[0] == 0


# --- the keyspace ------------------------------------------------------------------------

def test_rows_are_keyed_on_the_call_number_and_carry_no_document_number(conn):
    """A third keyspace. Nothing joins these to `solicitation` and nothing can."""
    _seed_report(conn)
    store_composite_awards(conn)
    calls = [r[0] for r in conn.execute(
        "SELECT call_number FROM composite_award ORDER BY call_number")]
    assert calls == ["3917-12-7226", "6302-12-0219"]
    cols = {c[1] for c in conn.execute("PRAGMA table_info(composite_award)")}
    assert "document_number" not in cols


def test_the_raw_call_number_is_kept_beside_the_normalized_one(conn):
    _seed_report(conn)
    store_composite_awards(conn)
    row = conn.execute("SELECT call_number, call_number_raw FROM composite_award "
                       "WHERE call_number='3917-12-7226'").fetchone()
    assert row["call_number_raw"] == "Request for Quotation 3917-12-7226"


def test_is_idempotent(conn):
    """Archive semantics: re-running must not duplicate. SQLite treats NULLs as distinct in a
    UNIQUE index, which is why the key COALESCEs its nullable parts (#73)."""
    _seed_report(conn)
    assert store_composite_awards(conn) == 2
    store_composite_awards(conn)
    assert conn.execute("SELECT COUNT(*) FROM composite_award").fetchone()[0] == 2


def test_one_call_may_award_several_lines(conn):
    """Per award LINE, as `award` is — the key must not collapse distinct suppliers."""
    text = ("Call No:\nRFQ 1111-10-0001\nDescription:\nWork.\n"
            "Recommended Bidder:\nAcme Inc.\nContract Award Value:\n"
            "$100.00 net of all applicable taxes\n"
            "Call No:\nRFQ 1111-10-0001\nDescription:\nWork.\n"
            "Recommended Bidder:\nBeta Ltd.\nContract Award Value:\n"
            "$200.00 net of all applicable taxes\n")
    _seed_report(conn, reference="2010.BD1.1", text=text)
    assert store_composite_awards(conn) == 2
    assert conn.execute("SELECT COUNT(DISTINCT call_number) FROM composite_award").fetchone()[0] == 1


def test_reads_only_reports_already_downloaded(conn):
    """Offline: background_pdf.text is the input, so nothing triggers a download mid-pass."""
    _seed_report(conn, text=None)
    assert store_composite_awards(conn) == 0


def test_composite_awards_reach_the_export(conn):
    from toronto_bids.export.document import build_export_document

    _seed_report(conn)
    store_composite_awards(conn)
    doc = build_export_document(conn, generated_at="2026-07-16T00:00:00Z")
    assert len(doc["composite_awards"]) == 2
    assert doc["meta"]["counts"]["composite_award"] == 2
    accrue = next(a for a in doc["composite_awards"]
                  if a["supplier_name_raw"] == "Accrue Contracting Ltd.")
    assert accrue["call_number"] == "3917-12-7226"
    assert accrue["award_value_numeric"] == 420000.00


def test_the_supplier_dimension_sees_pre_ariba_winners(conn):
    """Firms that only ever won before Ariba would otherwise be absent from the dimension
    entirely — Bondfield Construction won $59.5M across 3 of these and appears nowhere else."""
    from toronto_bids.linking.supplier import build_supplier_dimension

    _seed_report(conn)
    store_composite_awards(conn)
    build_supplier_dimension(conn)
    rows = conn.execute("SELECT supplier_id FROM composite_award").fetchall()
    assert all(r["supplier_id"] is not None for r in rows)
    assert conn.execute(
        "SELECT COUNT(*) FROM supplier s JOIN composite_award c "
        "ON c.supplier_id = s.supplier_id WHERE s.display_name LIKE 'Accrue%'").fetchone()[0] == 1
