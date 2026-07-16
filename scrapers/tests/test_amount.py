import pytest

from toronto_bids.amount import parse_amount


@pytest.mark.parametrize("raw,expected", [
    # the overwhelming majority: plain and comma-grouped, with or without $
    ("199994.13", 199994.13),
    ("500000.00", 500000.0),
    ("3052800", 3052800.0),
    ("1,317,169.92", 1317169.92),
    ("$129,348.42", 129348.42),
    ("$ 2,396,696.24", 2396696.24),
    ("  87000.00  ", 87000.0),
    ("2340468.5", 2340468.5),
    # a real published zero is a real number, not a missing one
    ("$0.00", 0.0),
    ("0", 0.0),
    # explicit CAD is still CAD
    ("$1,317,169.92 CAD", 1317169.92),
    ("82,500.00 CAD", 82500.0),
    ("$330,000 CAD", 330000.0),
])
def test_parses_real_amounts(raw, expected):
    assert parse_amount(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [
    None, "", "   ",
    # junk
    "kj", "j",
    "Metal Items at 109.11000 Percentage of the AMM published price",
    # a rate, not a total
    "31.65/MT",
    # malformed decimals
    "942467.", "3501.872.63", "960128.38.25.", "635.41010.",
    # concatenated amounts — upstream corruption; parsing invents a number
    "1071956.001099084.001049084.00",
    "560250.00154975.00318300.00",
    "76500.0025.95625.00",
    "18322.19253664.44",
    "950319421379831.75560173.76",
    # a typo'd currency symbol is a guess we decline to make
    "S2,035,000.00",
    # space inside the number
    "$982, 900",
])
def test_rejects_anything_that_is_not_plainly_an_amount(raw):
    assert parse_amount(raw) is None


def test_rejects_non_cad_currency():
    # We have no award-date exchange rate, so converting would invent precision and
    # summing it as CAD would be wrong. The raw string keeps the record.
    assert parse_amount("$1,311,936.00 USD") is None
    assert parse_amount("1,000.00 EUR") is None


def test_accepts_float_and_int_input():
    # OData sometimes hands back a number rather than a string
    assert parse_amount(1234.5) == pytest.approx(1234.5)
    assert parse_amount(1234) == pytest.approx(1234.0)
