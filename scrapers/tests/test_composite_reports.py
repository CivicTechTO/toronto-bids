"""#93: the 2009-2012 composite reports, where the agenda describes nothing.

    BD100.1 - Contract Awards - November 21 - Composite Report

One item, many awards, and a body that only says the details are "set out in the appendices
of this report". No amount, so #77's join has nothing to stand on, and no identifier either.
The appendices of the linked staff-report PDF carry both. The fixture is that real PDF's
pdftotext output (2012.BD100.1, backgroundfile-52208).
"""
import pathlib

from toronto_bids.models import Award, BackgroundPdf, Solicitation
from toronto_bids.sources.bid_award_panel import (
    match_composite_titles, parse_composite_appendices)
from toronto_bids.store import db

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "bid_award_panel"
REPORT = (FIXTURES / "2012.BD100.report.txt").read_text()


def test_pulls_each_appendix_off_a_real_composite_report():
    """The one item carries many awards — reading only the first would lose the rest."""
    items = parse_composite_appendices(REPORT)
    assert len(items) == 2
    assert [i["winner_raw"] for i in items] == [
        "Guillevin International Co.", "Accrue Contracting Ltd."]


def test_takes_the_net_of_taxes_figure_not_the_other_two():
    """Same three-figure block #77 calibrated against 980 Ariba-era items: award_amount is
    the 'net of all applicable taxes' one. 2012.BD100 appendix 2 publishes $420,000.00 net /
    $474,600.00 including / $427,392.00 net of HST recoveries."""
    accrue = parse_composite_appendices(REPORT)[1]
    assert accrue["award_value"] == 420000.00


def test_the_title_leads_with_the_call_number():
    """The call number is the only identifier the appendix carries and the thing a human
    would search for. A plain re.split ate it once — the label delimits the block."""
    accrue = parse_composite_appendices(REPORT)[1]
    assert accrue["title"].startswith("Request for Quotation 3917-12-7226 - ")
    assert "Concrete Cutting Services" in accrue["title"]


def _block(value_line):
    return ("Call No:\nTender Call 123-2012\nDescription:\nFor paving.\n\n"
            "Recommended Bidder:\nAcme Paving Inc.\n\nContract Award Value:\n" + value_line)


def test_reads_the_net_figure_however_the_year_words_it():
    """Ontario had no HST until July 2010, so 2009 publishes '(Net of GST)' and nothing else.
    Matching only 2012's 'net of all applicable taxes' silently yields zero for all of 2009
    (243 awards) and undercounts 2011 by 47."""
    for line, year in (
            ("$100,000.00 (Net of GST)\n", "2009"),
            ("$100,000.00 net of all taxes and charges\n", "2011"),
            ("$100,000.00 net of all applicable taxes and charges\n", "2012"),
            ("$100,000.00 net of applicable taxes and charges\n", "2012 variant"),
    ):
        items = parse_composite_appendices(_block(line))
        assert [i["award_value"] for i in items] == [100000.00], year


def test_never_takes_the_hst_recoveries_figure():
    """The third figure in the block. #77 measured it against 980 ground-truth items: it is
    award_amount 4 times out of 980. Taking it would match almost nothing, silently."""
    assert parse_composite_appendices(_block("$427,392.00 net of HST recoveries\n")) == []


def test_an_rfp_says_proponent_where_a_tender_says_bidder():
    """'Recommended Proponent' (15 blocks) and the plural 'Recommended Bidders' (8) are the
    same field. Accepting only the singular 'Recommended Bidder' drops 23 of 280."""
    for label in ("Recommended Proponent", "Recommended Bidders", "Recommended Proponents"):
        text = (f"Call No:\nTender Call 123-2012\nDescription:\nFor paving.\n\n"
                f"{label}:\nAcme Paving Inc.\n\nContract Award Value:\n"
                f"$100,000.00 net of all applicable taxes and charges\n")
        items = parse_composite_appendices(text)
        assert [i["winner_raw"] for i in items] == ["Acme Paving Inc."], label


def test_a_block_with_no_winner_or_no_value_is_skipped_not_half_parsed():
    """36 of 280 blocks publish no winner or no 'Contract Award Value' label at all."""
    text = ("Call No:\nTender Call 1-2012\nDescription:\nCancelled call.\n\n"
            "Number of Bids:\nThree (3)\n")
    assert parse_composite_appendices(text) == []


def _seed(conn, doc, supplier, amount, title=None):
    db.upsert_row(conn, Solicitation(doc, title=title, source="odata"), overwrite=True)
    db.upsert_row(conn, Award(doc, supplier_name_raw=supplier, award_amount=amount,
                              source="odata"), overwrite=True)


def _seed_report(conn, reference="2012.BD100.1", text=REPORT, kind="bgrd"):
    db.upsert_row(conn, BackgroundPdf(
        url=f"https://www.toronto.ca/legdocs/mmis/2012/bd/bgrd/backgroundfile-52208.pdf",
        reference=reference, kind=kind, text=text), overwrite=True)


def test_names_a_title_less_award_from_a_downloaded_report(conn):
    _seed(conn, "1234567890", "Accrue Contracting Ltd.", "420000.00")
    _seed_report(conn)
    conn.commit()
    assert match_composite_titles(conn) == 1
    row = conn.execute("SELECT title, title_source FROM solicitation").fetchone()
    assert row["title"].startswith("Request for Quotation 3917-12-7226 - ")
    assert row["title_source"] == "council_composite"


def test_reads_only_reports_already_downloaded(conn):
    """Offline by default: the text column is the input, so a report nobody fetched
    contributes nothing rather than triggering a download mid-match."""
    _seed(conn, "1234567890", "Accrue Contracting Ltd.", "420000.00")
    _seed_report(conn, text=None)
    conn.commit()
    assert match_composite_titles(conn) == 0


def test_a_different_firm_at_the_same_value_is_not_named(conn):
    """Measured, not hypothetical: council's 'Furcon Environmental Inc.' and the archive's
    'Lea Consulting Ltd.' share an award value in 2012. The supplier check is the only thing
    standing between that coincidence and a wrong title."""
    _seed(conn, "1234567890", "Lea Consulting Ltd.", "420000.00")
    _seed_report(conn)
    conn.commit()
    assert match_composite_titles(conn) == 0
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] is None


def test_an_ambiguous_match_is_dropped_not_guessed(conn):
    for doc in ("1234567890", "9876543210"):
        _seed(conn, doc, "Accrue Contracting Ltd.", "420000.00")
    _seed_report(conn)
    conn.commit()
    assert match_composite_titles(conn) == 0


def test_never_overrides_a_title_the_city_published(conn):
    _seed(conn, "1234567890", "Accrue Contracting Ltd.", "420000.00",
          title="Concrete Cutting Services")
    _seed_report(conn)
    conn.commit()
    match_composite_titles(conn)
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] == "Concrete Cutting Services"


def test_is_idempotent(conn):
    _seed(conn, "1234567890", "Accrue Contracting Ltd.", "420000.00")
    _seed_report(conn)
    conn.commit()
    assert match_composite_titles(conn) == 1
    assert match_composite_titles(conn) == 0


def test_a_sync_cannot_clobber_the_recovered_provenance(conn):
    """Same trap #79 shipped: the spine re-upserts every row with overwrite=True, so title
    provenance stored in `source` gets reset to 'odata' on the next sync."""
    _seed(conn, "1234567890", "Accrue Contracting Ltd.", "420000.00")
    _seed_report(conn)
    conn.commit()
    assert match_composite_titles(conn) == 1
    db.upsert_row(conn, Solicitation("1234567890", title=None, source="odata"), overwrite=True)
    conn.commit()
    row = conn.execute("SELECT title, source, title_source FROM solicitation").fetchone()
    assert row["title"].startswith("Request for Quotation 3917-12-7226")
    assert row["source"] == "odata"
    assert row["title_source"] == "council_composite"
