"""Strict parser for City-published CAD amount strings.

Returns None for ambiguous, malformed, rate, or non-CAD strings; raw text stays in the store so NULL numeric beside non-NULL raw flags rows for review.
"""
import re

# Comma-grouped and plain forms are separate so malformed grouping like '1,23' is rejected.
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
    """Parse a single CAD amount string (or numeric value from OData), else return None."""
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


# Trailing bid-table footnote markers (e.g. '$2,982,036.67*').
_FOOTNOTE_MARKER = re.compile(r"[\s*^+\u2020\u2021\u00a7]+$")


def parse_bid_price(raw) -> float | None:
    """Strip trailing bid-table footnote markers before delegating to parse_amount."""
    if raw is None or isinstance(raw, (int, float)):
        return parse_amount(raw)
    return parse_amount(_FOOTNOTE_MARKER.sub("", str(raw)))
