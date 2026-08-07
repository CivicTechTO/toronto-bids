"""#114: Award Summary Form infrastructure tests.

Parser tests were removed in #205 (LLM extraction replaces per-source parsers).
These test the download/indexing infrastructure that remains.
"""

from toronto_bids.sources.award_summary import award_summary_files


def test_finds_the_award_summary_form_on_a_record():
    rec = {
        "uploadedFilesStaff": [
            {
                "bin_id": "kSj1PnNq2nX0FApSenhvCA",
                "name": "Doc5616191850 Award Summary Form.pdf",
            }
        ]
    }
    assert award_summary_files(rec) == [
        (
            "https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/kSj1PnNq2nX0FApSenhvCA",
            "Doc5616191850 Award Summary Form.pdf",
        )
    ]


def test_an_award_under_500k_carries_no_form():
    """The form only exists over $500,000 — the panel had no such floor, so the bid record
    thins permanently for small awards. An empty list is the normal case, not an error."""
    assert award_summary_files({"uploadedFilesStaff": []}) == []
    assert award_summary_files({}) == []


def test_other_attachments_are_not_mistaken_for_the_form():
    rec = {"uploadedFilesStaff": [{"bin_id": "x", "name": "Doc123 Addendum 1.pdf"}]}
    assert award_summary_files(rec) == []
