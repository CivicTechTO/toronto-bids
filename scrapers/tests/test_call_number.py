import pytest

from toronto_bids.linking.call_number import normalize_call_number


@pytest.mark.parametrize("raw,expected", [
    ("Request for Quotation 3905-10-0097", "3905-10-0097"),
    ("Request for Quotation No. 3905-10-0097", "3905-10-0097"),
    ("Request For Quotation 3905-10-0097", "3905-10-0097"),
    ("RFQ 3907-08-5236", "3907-08-5236"),
    ("Request for Proposal (RFP) 9117-10-7226", "9117-10-7226"),
    ("Tender Call 213-2008", "213-2008"),
    ("Tender Call No. 317-2010", "317-2010"),
    ("Tender No. 300-2010", "300-2010"),
])
def test_reads_the_call_number_whatever_the_prefix_says(raw, expected):
    assert normalize_call_number(raw) == expected


def test_a_trailing_contract_number_is_not_the_call_number():
    assert normalize_call_number(
        "Tender Call No. 317-2010, Contract No. 10TE-17WS") == "317-2010"
    assert normalize_call_number(
        "Tender Call 213-2008, Contract No. TWDO-TW-TWOS-MCP-06-00001") == "213-2008"


def test_the_division_form_wins_over_the_year_form():
    assert normalize_call_number("3905-10-0097") == "3905-10-0097"


@pytest.mark.parametrize("raw,expected", [
    ("Request for Proposal (RFP) No. 9130–10–7291", "9130-10-7291"),
    ("Tender Call No. 120 -2011, Contract No. 10EY-10RD", "120-2011"),
    ("Tender Call 134 -2012", "134-2012"),
])
def test_dash_typography_is_not_data_loss(raw, expected):
    assert normalize_call_number(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "Request for Quotation", "n/a", "Appendix #2"])
def test_a_string_carrying_no_call_number_yields_none(raw):
    assert normalize_call_number(raw) is None


def test_a_year_alone_is_not_a_call_number():
    assert normalize_call_number("for the period 2010 to 2012") is None
