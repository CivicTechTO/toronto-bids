"""#164: Committee/Council award infrastructure tests.

Parser tests were removed in #205 (LLM extraction replaces per-source parsers).
These test the discovery/download infrastructure that remains.
"""

from pathlib import Path

from toronto_bids.sources.committee_awards import (
    award_doc_number,
    award_items_from_voting_record,
    report_url_from_item_html,
)

FIX = Path(__file__).parent / "fixtures" / "committee"


def test_award_doc_number_reads_the_title_vocabulary():
    assert award_doc_number("Award of Doc4553928310 to GFL") == "4553928310"
    assert (
        award_doc_number(
            "Process for Award of Negotiable RFP Document Number 4053424337 for x"
        )
        == "4053424337"
    )
    assert (
        award_doc_number("Award of Ariba Document Number 3448368603 to CH2M")
        == "3448368603"
    )
    assert award_doc_number("Election of the Speaker") is None


def test_award_items_from_voting_record_dedups_and_skips_non_awards():
    items = award_items_from_voting_record(
        (FIX / "voting_record_sample.csv").read_text()
    )
    by_doc = {i["document_number"]: i for i in items}
    assert set(by_doc) == {"4553928310", "4053424337", "3448368603"}
    assert by_doc["4553928310"]["reference"] == "2024.GG16.12"
    assert by_doc["4553928310"]["committee"] == "City Council"


def test_report_url_from_item_html_finds_the_award_report():
    html = (FIX / "item_2024_GG16_12.html").read_text()
    url = report_url_from_item_html(html, "4553928310")
    assert (
        url
        == "https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-248375.pdf"
    )


def test_report_url_from_item_html_returns_none_without_a_matching_link():
    assert (
        report_url_from_item_html(
            "<html><body>No reports here.</body></html>", "4553928310"
        )
        is None
    )
    html = (
        "<div><span>Award of Doc1111111111</span>"
        '<a href="https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-1.pdf">x</a></div>'
        "<div><span>Award of Doc2222222222</span>"
        '<a href="https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-2.pdf">x</a></div>'
    )
    assert report_url_from_item_html(html, "4553928310") is None
