from pathlib import Path

from toronto_bids.sources.committee_awards import (
    award_doc_number,
    award_items_from_voting_record,
    parse_committee_bids,
    store_committee_bids,
)

FIX = Path(__file__).parent / "fixtures" / "committee"


def test_award_doc_number_reads_the_title_vocabulary():
    assert award_doc_number("Award of Doc4553928310 to GFL") == "4553928310"
    assert award_doc_number("Process for Award of Negotiable RFP Document Number 4053424337 for x") == "4053424337"
    assert award_doc_number("Award of Ariba Document Number 3448368603 to CH2M") == "3448368603"
    assert award_doc_number("Election of the Speaker") is None


def test_award_items_from_voting_record_dedups_and_skips_non_awards():
    items = award_items_from_voting_record((FIX / "voting_record_sample.csv").read_text())
    by_doc = {i["document_number"]: i for i in items}
    assert set(by_doc) == {"4553928310", "4053424337", "3448368603"}   # non-award row dropped, dupes merged
    assert by_doc["4553928310"]["reference"] == "2024.GG16.12"
    assert by_doc["4553928310"]["committee"] == "City Council"


def test_parse_rfq_summary_of_bids_received():
    bids = parse_committee_bids((FIX / "district2_rfq_doc4553928310.txt").read_text())
    names = {b for b, _ in bids}
    assert "GFL Environmental Inc." in names
    assert any("Halton Recycling" in b for b, _ in bids)      # the losing bidder captured
    assert len(bids) == 2


def test_parse_rft_opened_the_following_bids():
    bids = parse_committee_bids((FIX / "ashbridges_tender_2010.txt").read_text())
    names = {b for b, _ in bids}
    assert any("Kenaidan" in b for b in names)
    assert any("Torbear" in b for b in names)
    assert any("Alberici" in b for b in names)
    assert len(bids) == 3


def test_parse_refuses_a_report_with_no_bid_table():
    assert parse_committee_bids("... the only supplier able to provide ... emergency ...") == []


def test_store_committee_bids_attaches_by_document_number(conn):
    from toronto_bids.store import db
    from toronto_bids.models import Solicitation, BackgroundPdf
    db.upsert_row(conn, Solicitation(document_number="4553928310", source="odata"), overwrite=True)
    db.upsert_row(conn, BackgroundPdf(url="https://x/backgroundfile-1.pdf", document_number="4553928310",
                  kind="committee_award", text=(FIX / "district2_rfq_doc4553928310.txt").read_text()),
                  overwrite=True)
    conn.commit()
    n = store_committee_bids(conn)
    assert n == 2
    rows = conn.execute("SELECT bidder_name_raw, document_number FROM bid WHERE source='committee_award'").fetchall()
    assert {r[0] for r in rows} >= {"GFL Environmental Inc."}
    assert all(r[1] == "4553928310" for r in rows)
