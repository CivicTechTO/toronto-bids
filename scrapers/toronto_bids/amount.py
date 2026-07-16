"""Parse the City's published amount strings into numbers (#64).

The feeds publish amounts as free text, and the store keeps that text verbatim — it is what
the City actually said, and some of it has no numeric form at all ("Metal Items at 109.11000
Percentage of the AMM published price"). This module is the other half: the number, where
there plainly is one, so aggregates stop being nonsense.

Deliberately strict. Anything that is not unambiguously an amount returns None rather than a
guess, because the raw string is retained either way and a wrong number is worse than a
missing one. Of 13,559 award values, 77 fail this parser; every one of them is genuinely not
a single CAD amount:

  * concatenated amounts — '1071956.001099084.001049084.00' is three awards mashed together
    upstream. Any parse of this invents a number.
  * malformed decimals — '942467.', '3501.872.63'
  * rates, not totals — '31.65/MT'
  * a typo'd currency symbol — 'S2,035,000.00' is plainly $2,035,000.00 to a human, but
    accepting a stray leading letter means accepting anything.
  * non-CAD — '$1,311,936.00 USD'. We have no award-date exchange rate; converting invents
    precision and summing it as CAD is exactly the bug this module exists to kill.
  * junk — 'kj', 'j'

A NULL numeric beside a non-NULL raw string is therefore meaningful: it marks a value a human
should look at. `WHERE award_amount IS NOT NULL AND award_amount_numeric IS NULL` lists them.
"""
import re

# $1,234,567.89 CAD | 500000.00 | 0 — at most one decimal point, optional $, optional
# currency code. Comma-grouped and plain forms are spelled separately on purpose: a single
# \d+(?:,\d{3})* would also accept '1,23' and other malformed grouping.
_AMOUNT = re.compile(
    r"""^\s*
        \$?\s*
        (?P<num>\d{1,3}(?:,\d{3})+|\d+)
        (?P<frac>\.\d+)?
        \s*
        (?P<currency>[A-Za-z]{3})?
        \s*$""",
    re.VERBOSE,
)

_CAD = "CAD"


def parse_amount(raw) -> float | None:
    """The numeric value of a City-published amount, or None if it is not plainly one.

    Accepts an int/float straight through (OData occasionally sends a number, not a string).
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    match = _AMOUNT.match(str(raw))
    if match is None:
        return None
    currency = match.group("currency")
    if currency is not None and currency.upper() != _CAD:
        return None
    return float(match.group("num").replace(",", "") + (match.group("frac") or ""))


# Bid tables mark prices with a footnote pointing at a note under the table:
# '$2,982,036.67*' ("includes contingency"), '$1,581,114.08 *', 'Smith and Long Ltd.**'.
# 26% of the corpus carries one.
_FOOTNOTE_MARKER = re.compile(r"[\s*^+\u2020\u2021\u00a7]+$")


def parse_bid_price(raw) -> float | None:
    """A bid price as a number, once its footnote marker is off (#84).

    parse_amount rightly refuses '$2,982,036.67*' — a stray trailing character is exactly the
    ambiguity it exists to reject. In a bid table that character is known scaffolding, not
    ambiguity, so strip it and let parse_amount judge the rest. Everything else still returns
    None: the City writes 'Non-Compliant', 'No bid' and 'N/A' in the price column, and those
    are outcomes rather than amounts — the raw string keeps them.
    """
    if raw is None or isinstance(raw, (int, float)):
        return parse_amount(raw)
    return parse_amount(_FOOTNOTE_MARKER.sub("", str(raw)))
