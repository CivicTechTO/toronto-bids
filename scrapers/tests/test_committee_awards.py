from pathlib import Path

from toronto_bids.sources.committee_awards import award_doc_number, award_items_from_voting_record

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
