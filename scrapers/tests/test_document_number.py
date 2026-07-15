import pytest

from toronto_bids.linking.document_number import normalize_document_number, bridge_document_number


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3303123110", "3303123110"),            # already clean
        ("Doc5725384704", "5725384704"),         # Ariba Doc-prefixed
        ("3303-12-3110", "3303123110"),          # hyphenated in free text
        ("Doc5581608073 - Request for Quotations", "5581608073"),  # embedded in title
        ("﻿3674586673", "3674586673"),      # leading BOM
        ("4147794028﻿", "4147794028"),      # trailing BOM
        ("2821040966 )", "2821040966"),          # trailing junk
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
        "xxxxxxxx",                 # placeholder
        "xxxxxxx",
        "390513test",               # <10 digits after strip
        "Notice913418",
        "Summary67141",
        "No. 22436",
        "1111111111",               # denylisted test row
        "0000000000",               # denylisted test row
        "1234567890",               # denylisted test row
        "3.77E+1100",               # Excel scientific-notation corruption
        "3710106+0111",             # digits present but 11 after strip -> reject
        "123",                      # too short
        "123456789012",             # too long (12 digits)
    ],
)
def test_invalid_document_numbers_return_none(raw):
    assert normalize_document_number(raw) is None


def test_bridge_uses_external_rfx_id():
    # "Doc5672751291" -> strip -> "5672751291"
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
