"""#94: the Bid Committee's bid tables, which 417 agendas yielded 36 bids from.

They are not missing. They are laid out so that both of the row-major parser's assumptions
fail: the heading is a LINE INSIDE A CELL rather than the table's header row, and lxml's
text_content() fuses the cell's <p> runs into one blob ("$ 224,156.52$ 231,817.47$ ...").

The fixture is a real 2013 agenda's markup.
"""
import pathlib

from toronto_bids.sources.bid_award_panel import _cell_lines, _hst_basis, parse_bid_tables

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "bid_award_panel"


def _fixture(name):
    return (FIXTURES / f"{name}.html").read_text()


def test_extracts_the_bid_table_a_real_bd_agenda_publishes():
    """2013.BD124.1: seven bidders in a single column cell. Under the row-major parser this
    agenda produced nothing at all."""
    bids = parse_bid_tables(_fixture("2013.BD124"), "2013.BD124")
    assert [b["bidder_name_raw"] for b in bids] == [
        "R.E. Cavanagh Electric Co. Ltd.", "Ozz Electric Inc.", "Kudlak-Baird (1982) Limited",
        "Stevens & Black Electrical Contractors Ltd.", "T.B.R. Mechanical/Electrical Inc.",
        "Electric Group Ltd.", "Ainsworth Inc.",
    ]
    assert bids[0]["bid_price"] == "$ 224,156.52"
    assert all(b["reference"] == "2013.BD124.1" for b in bids)


def test_pre_ariba_bd_bids_carry_no_document_number():
    """2013 predates Ariba, so these join to nothing. Kept regardless — #77 wants them."""
    assert all(b["document_number"] is None
               for b in parse_bid_tables(_fixture("2013.BD124"), "2013.BD124"))


# --- the two things that broke the row-major parser --------------------------------------

def test_cell_lines_recovers_values_text_content_would_fuse():
    """The bug under the bug. Each value is its own <p>, and text_content() concatenates them
    with no separator — a price column reads back as one unsplittable blob."""
    from lxml import html as _html

    cell = _html.fromstring(
        "<td><p>Bid Price (Incl. HST)</p><p>$ 224,156.52</p><p>$ 231,817.47</p></td>")
    assert cell.text_content() == "Bid Price (Incl. HST)$ 224,156.52$ 231,817.47"
    assert _cell_lines(cell) == ["Bid Price (Incl. HST)", "$ 224,156.52", "$ 231,817.47"]


def test_cell_lines_handles_br_separated_values_too():
    from lxml import html as _html

    cell = _html.fromstring("<td>Acme Inc.<br>Beta Ltd.<br/>Gamma Corp.</td>")
    assert _cell_lines(cell) == ["Acme Inc.", "Beta Ltd.", "Gamma Corp."]


def test_the_heading_is_a_cell_not_a_header_row():
    """The bidder heading sits in a cell beside a 'Number of Bids:' rowspan, so a parser that
    checks the table's first header cell never fires."""
    html = ("<html><body><h3>BD1.1 - Award of Call 123-2013 to Acme for paving</h3><table>"
            "<tr><td>Number of Bids:</td>"
            "<td><p>Firm Name</p><p>Acme Paving Inc.</p><p>Beta Construction Ltd.</p></td>"
            "<td><p>Bid Price (Incl. HST)</p><p>$100.00</p><p>$200.00</p></td></tr>"
            "</table></body></html>")
    bids = parse_bid_tables(html, "2013.BD1")
    assert [(b["bidder_name_raw"], b["bid_price"]) for b in bids] == [
        ("Acme Paving Inc.", "$100.00"), ("Beta Construction Ltd.", "$200.00")]


def test_bidders_in_the_rows_below_are_realigned_past_the_rowspan():
    """The same agendas also put bidders in following rows, where the 'Number of Bids' cell is
    absent and every remaining cell shifts left by exactly one."""
    html = ("<html><body><h3>BD2.1 - Award of Call 456-2013 to Acme for work</h3><table>"
            "<tr><td>Number of Bids:</td><td>Bidder Name</td><td>Bid Price (Incl. HST)</td></tr>"
            "<tr><td>Acme Paving Inc.</td><td>$100.00</td></tr>"
            "<tr><td>Beta Construction Ltd.</td><td>$200.00</td></tr>"
            "</table></body></html>")
    bids = parse_bid_tables(html, "2013.BD2")
    assert [(b["bidder_name_raw"], b["bid_price"]) for b in bids] == [
        ("Acme Paving Inc.", "$100.00"), ("Beta Construction Ltd.", "$200.00")]


# --- refusing rather than guessing --------------------------------------------------------

def test_unequal_columns_are_refused_not_paired():
    """Names and prices are positional. One stray line — a footnote, a wrapped name — and
    every pairing after it attributes a bid to the wrong firm. A misattributed bid is worse
    than a missing one, and 133 tables in the corpus are unequal."""
    html = ("<html><body><h3>BD3.1 - Award of Call 789-2013 to Acme for work</h3><table>"
            "<tr><td>Number of Bids:</td>"
            "<td><p>Firm Name</p><p>Acme Paving Inc.</p></td>"
            "<td><p>Bid Price (Incl. HST)</p><p>$100.00</p><p>$200.00</p></td></tr>"
            "</table></body></html>")
    assert parse_bid_tables(html, "2013.BD3") == []


def test_a_financial_table_is_not_a_bid_table():
    """BD items carry cost-centre and funding tables in the same markup shape."""
    html = ("<html><body><h3>BD4.1 - Award of Call 111-2013 to Acme for work</h3><table>"
            "<tr><td>Period</td><td>Cost Centres</td><td>Total (net of HST Recoveries)</td></tr>"
            "<tr><td>January 1 to December 31, 2015</td><td>$774,691.62</td><td></td></tr>"
            "</table></body></html>")
    assert parse_bid_tables(html, "2013.BD4") == []


# --- hst_basis, which a bid price is meaningless without ----------------------------------

def test_the_abbreviated_hst_header_is_read():
    """'Bid Price (Incl. HST)' is the Bid Committee's single most common price header — 587 of
    them — and `includ\\w*` matches none of it. Left unread, 4,058 bids would store no basis,
    and a price whose basis is unknown cannot be compared with one whose basis is known."""
    for header, expected in (
            ("Bid Price (Incl. HST)", "including"),
            ("Bid Price (incl. HST)", "including"),
            ("Bid Total Price (incl. HST)", "including"),
            ("Bid Price ( Incl. HST)", "including"),
            ("Bid Price (including HST)", "including"),
            ("Bid Price (excluding H.S.T.)", "excluding"),
            ("Bid Price (Excl. HST)", "excluding"),
            ("Bid Price", None),
    ):
        assert _hst_basis(header) == expected, header


def test_the_real_bd_fixture_records_its_basis():
    bids = parse_bid_tables(_fixture("2013.BD124"), "2013.BD124")
    assert all(b["hst_basis"] == "including" for b in bids)
    assert bids[0]["price_header"] == "Bid Price (Incl. HST)"


# --- additive: BA must not change ---------------------------------------------------------

def test_a_ba_agenda_is_untouched_by_the_bd_path():
    """12,733 BA bids parse today. The BD path runs only where the row-major path declined,
    and these agendas nest a bid table inside the item's outer table — scoping the BD path to
    descendant rows re-read five of BA189.3's bids a second time."""
    bids = parse_bid_tables(_fixture("2022.BA189"), "2022.BA189")
    seen = [(b["reference"], b["bidder_name_raw"], b["bid_price"]) for b in bids]
    assert len(seen) == len(set(seen)), "a bid was parsed twice"
    assert len(bids) == 25


def test_real_bd_header_continuation_and_footer_preserve_every_relation():
    """BD25.1 had eight shifted pairs despite equal column lengths after one header."""
    bids = parse_bid_tables(_fixture('2015.BD25.1'), '2015.BD25')
    assert [(b['bidder_name_raw'], b['bid_price']) for b in bids] == [
        ('Bond Paving and Construction Inc.', '$ 286,536.36'),
        ('Serve Construction. Ltd.', '$359,463.32'),
        ('D. L. Bros (2014) Ltd.', '$456,747.13+'),
        ('Tony Battista Paving Inc.', '$485,388.00'),
        ('Mopal Construction Ltd.', '$584,181.75'),
        ('Four Seasons Site Development Ltd.', '$598,227.65'),
        ('CSBI Group Ltd.', '$787,719.61'),
        ('Dig-Con International Ltd.', '$995,617.27'),
    ]
    assert all(b['hst_basis'] == 'including' for b in bids)
    assert all(b['price_header'] == 'Bid Price (including HST)' for b in bids)


def _column_fixture(names, prices):
    return ('<html><h3>BD5.1 - Award of Tender 5-2015</h3><table><tr>'
            '<td>Number of Bids:</td><td>'
            + ''.join('<p>'+line+'</p>' for line in ['Firm Name', *names])
            + '</td><td>'
            + ''.join('<p>'+line+'</p>' for line in ['Bid Price', *prices])
            + '</td></tr></table></html>')


def test_unknown_price_header_line_cannot_shift_pairs_with_balancing_footer():
    page = _column_fixture(['Acme Inc.', 'Beta Ltd.', '+ Bidder was found non-compliant.'],
                           ['unknown price heading', '$100.00+', '$200.00'])
    assert parse_bid_tables(page, '2015.BD5') == []


def test_only_a_referenced_marked_trailing_footnote_can_be_separated():
    page = _column_fixture(['Acme Inc.', 'Beta Ltd.', '+ Bidder was found non-compliant.'],
                           ['(including HST)', '$100.00*', '$200.00'])
    assert parse_bid_tables(page, '2015.BD5') == []


def test_unpaired_wrapped_supplier_line_remains_refused():
    page = _column_fixture(['Acme Inc.', 'Beta', 'Construction Ltd.'],
                           ['(including HST)', '$100.00', '$200.00'])
    assert parse_bid_tables(page, '2015.BD5') == []


def test_a_marked_footnote_in_the_middle_is_not_silently_removed():
    page = _column_fixture(['Acme Inc.', '+ Bidder was found non-compliant.', 'Beta Ltd.'],
                           ['(including HST)', '$100.00+', '$200.00'])
    assert parse_bid_tables(page, '2015.BD5') == []
