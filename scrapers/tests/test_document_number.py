import pytest

from toronto_bids.linking.document_number import normalize_document_number, bridge_document_number


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3303123110", "3303123110"),
        ("Doc5725384704", "5725384704"),
        ("3303-12-3110", "3303123110"),
        ("Doc5581608073 - Request for Quotations", "5581608073"),
        ("﻿3674586673", "3674586673"),  # leading BOM
        ("4147794028﻿", "4147794028"),  # trailing BOM
        ("2821040966 )", "2821040966"),
    ],
)
def test_valid_document_numbers_normalize_to_ten_digits(raw, expected):
    assert normalize_document_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "xxxxxxxx",
        "xxxxxxx",
        "390513test",
        "Notice913418",
        "Summary67141",
        "No. 22436",
        "1111111111",
        "0000000000",
        "1234567890",
        "3.77E+1100",
        "3710106+0111",
        "123",
        "123456789012",
    ],
)
def test_invalid_document_numbers_return_none(raw):
    assert normalize_document_number(raw) is None


def test_bridge_uses_external_rfx_id():
    assert bridge_document_number("Doc5672751291", "some title") == "5672751291"


def test_bridge_falls_back_to_title_embedded_doc():
    title = "Doc5581608073 - Request for Quotations for the non-exclusive supply"
    assert bridge_document_number(None, title) == "5581608073"


def test_bridge_prefers_external_rfx_id_over_title():
    assert bridge_document_number("Doc5672751291", "Doc9999999999 - other") == "5672751291"


def test_bridge_returns_none_when_neither_resolves():
    assert bridge_document_number(None, "Request for Tenders for Road Resurfacing") is None
    assert bridge_document_number("", "") is None
    assert bridge_document_number(None, None) is None


def test_bridge_rejects_title_doc_with_more_than_ten_digits():
    assert bridge_document_number(None, "Doc56727512911 - eleven digit run") is None
