"""#114: the losing bidders, after the Bid Award Panel was abolished on 2025-10-01.

By-law 766-2025 eliminated the panel, so `bid_award_panel.py` stops dead at 2025.BA151 and
891 agendas is the final corpus. The bidders did not stop — they moved to an Award Summary
Form on the Toronto Bids Portal. Fixtures are the real pdftotext output of two real forms.
"""
import pathlib

import pytest

from toronto_bids.models import BackgroundPdf, Bid
from toronto_bids.sources.award_summary import (
    award_summary_files, parse_award_summary, store_award_summary_bids)
from toronto_bids.store import db

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "award_summary"
FORM = (FIXTURES / "Doc5616191850.txt").read_text()
RFP = (FIXTURES / "Doc5386487782-rfp.txt").read_text()


# --- the bid table, which is the whole point ---------------------------------------------

def test_reads_every_bidder_off_a_real_form():
    """Doc5616191850, awarded 2026-06-23. Five bidders, one of whom won — the other four are
    what spec §2.5.2 calls unrecoverable and what the City stopped publishing via TMMIS."""
    p = parse_award_summary(FORM)
    assert [b["bidder_name_raw"] for b in p["bids"]] == [
        "2489960 ONTARIO INC., O/A Kore Infrastructure Group",
        "CRCE Construction Ltd.",
        "GIO CRETE CONSTRUCTION LTD.",
        "Aecon Utilities Inc.",
        "GIP Paving Inc.",
    ]
    assert [b["bid_price"] for b in p["bids"]][:2] == ["$7,710,000.00", "$8,624,203.50"]


def test_joins_the_spine_on_the_document_number():
    """No council reference exists for these — the panel that issued them is gone. The Ariba
    document number is the join, and it is the spine's own primary key."""
    assert parse_award_summary(FORM)["document_number"] == "5616191850"


def test_the_price_header_carries_the_hst_basis():
    """Load-bearing, exactly as on the agendas (#94): a price whose basis is unknown cannot be
    compared with one whose basis is known."""
    p = parse_award_summary(FORM)
    assert p["price_header"] == "Bid Price (Excluding HST)"
    assert p["hst_basis"] == "excluding"


def test_a_dollar_sign_far_from_its_digits_still_parses():
    """The City right-aligns the number and -layout preserves the column, so the '$' can sit
    ~30 spaces from its own digits: '1. ClaimsPro LP    $        30,460,650.00'."""
    p = parse_award_summary(RFP)
    claims = next(b for b in p["bids"] if b["bidder_name_raw"].startswith("ClaimsPro"))
    assert claims["bid_price"] == "$ 30,460,650.00"


# --- the shapes that broke it -------------------------------------------------------------

def test_an_rfp_lists_proponents_with_no_price_at_all():
    """'NOTE: Not applicable for RFP'. Requiring a price dropped two of these three bidders
    and left a one-bid parse of a three-bid award. #84 already stores priceless bids."""
    p = parse_award_summary(RFP)
    assert p["declared_bids"] == 3
    assert [b["bidder_name_raw"] for b in p["bids"]] == [
        "ClaimsPro LP", "Sedgwick Canada Inc.", "DSB Claims Solutions Inc."]
    assert [b["bid_price"] for b in p["bids"]][1:] == [None, None]


def test_a_numbered_ontario_corporation_is_a_real_bidder():
    """The #87 lesson, re-learned the hard way here. '2489960 Ontario Inc.' won an $8.4M
    watermain contract. A guard that treats a long digit run as a leaked price eats it — and
    took 26 forms' bid tables down with it before this was caught."""
    p = parse_award_summary(FORM)
    assert p["bids"][0]["bidder_name_raw"].startswith("2489960 ONTARIO INC.")


def test_the_bidders_own_numbering_does_not_end_the_section():
    """Section 5 is the last one, so it runs to the end. Bounding it at the next '<digit>. X'
    heading truncated the table at '2. CRCE Construction Ltd.' — a one-bid parse of a
    five-bid award, silently."""
    assert len(parse_award_summary(FORM)["bids"]) == 5


def test_a_form_with_no_bid_section_yields_none():
    assert parse_award_summary("Ariba Document No. Doc1234567890\n1. Award Details\n") is None
    assert parse_award_summary("") is None


# --- the count check ----------------------------------------------------------------------

def _seed(conn, text, doc="5616191850", url=None):
    db.upsert_row(conn, BackgroundPdf(
        url=url or f"https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/{doc}",
        kind="award_summary", document_number=doc, text=text), overwrite=True)
    conn.commit()


def test_stores_the_bids_a_real_form_publishes(conn):
    _seed(conn, FORM)
    assert store_award_summary_bids(conn) == 5
    rows = conn.execute("SELECT bidder_name_raw, bid_price_numeric, hst_basis, reference, "
                        "document_number, source FROM bid ORDER BY bid_price_numeric").fetchall()
    assert rows[0]["bid_price_numeric"] == pytest.approx(7710000.00)
    assert rows[0]["hst_basis"] == "excluding"
    assert rows[0]["reference"] is None            # no council item exists for these
    assert rows[0]["document_number"] == "5616191850"
    assert rows[0]["source"] == "award_summary"


def test_under_parsing_refuses_the_form_rather_than_storing_a_partial_table(conn):
    """The check the agenda corpus never offered: the form states its own bid count, so a
    partial parse is detectable rather than silent."""
    text = FORM.replace("2. CRCE Construction Ltd.", "").replace(
        "3. GIO CRETE CONSTRUCTION LTD.", "")
    _seed(conn, text)
    assert store_award_summary_bids(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0] == 0


def test_parsing_more_than_declared_is_kept_not_refused(conn):
    """'Number of Bids Received' sometimes counts only the COMPLIANT bids while the table
    lists everyone — doc 5247418372 declares 2 and tabulates 3, the third marked '*' at
    $0.00. The table is the record; the count is a summary of part of it."""
    text = FORM.replace("Number of Bids Received                                    Five (5)",
                        "Number of Bids Received                                    Four (4)")
    _seed(conn, text)
    assert store_award_summary_bids(conn) == 5


def test_is_idempotent(conn):
    _seed(conn, FORM)
    assert store_award_summary_bids(conn) == 5
    store_award_summary_bids(conn)
    assert conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0] == 5


def test_a_priceless_rfp_bid_does_not_duplicate_on_every_run(conn):
    """SQLite treats NULLs as distinct in a UNIQUE index — the #73/#84 trap. bid_key COALESCEs
    both identifiers AND the price, so these survive a re-run."""
    _seed(conn, RFP, doc="5386487782")
    first = store_award_summary_bids(conn)
    store_award_summary_bids(conn)
    assert conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0] == first


def test_a_bid_award_panel_bid_and_an_award_summary_bid_coexist(conn):
    """Both write `bid`, and neither identifier is required: a panel bid has a council item
    and (pre-2019) no document number; an award-summary bid is the reverse."""
    db.upsert_row(conn, Bid(reference="2022.BA189.1", bidder_name_raw="Hatch Ltd.",
                            source="bid_award_panel"), overwrite=True)
    _seed(conn, FORM)
    store_award_summary_bids(conn)
    n = conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0]
    assert n == 6
    assert conn.execute("SELECT COUNT(*) FROM bid WHERE reference IS NULL").fetchone()[0] == 5


# --- the attachment --------------------------------------------------------------------

def test_finds_the_award_summary_form_on_a_record():
    rec = {"uploadedFilesStaff": [
        {"bin_id": "kSj1PnNq2nX0FApSenhvCA", "name": "Doc5616191850 Award Summary Form.pdf"}]}
    assert award_summary_files(rec) == [
        ("https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/kSj1PnNq2nX0FApSenhvCA",
         "Doc5616191850 Award Summary Form.pdf")]


def test_an_award_under_500k_carries_no_form():
    """The form only exists over $500,000 — the panel had no such floor, so the bid record
    thins permanently for small awards. An empty list is the normal case, not an error."""
    assert award_summary_files({"uploadedFilesStaff": []}) == []
    assert award_summary_files({}) == []


def test_other_attachments_are_not_mistaken_for_the_form():
    rec = {"uploadedFilesStaff": [{"bin_id": "x", "name": "Doc123 Addendum 1.pdf"}]}
    assert award_summary_files(rec) == []
