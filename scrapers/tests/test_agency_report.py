import re

from toronto_bids.sources.agency_report import (
    AMOUNT_RE, CONFIDENTIAL_RE, amount_or_none,
)


def test_amount_re_reads_in_the_amount_of():
    m = AMOUNT_RE.search("awarded in the amount of $410,563.00 to the firm")
    assert m and m.group(1) == "$410,563.00"


def test_amount_or_none_refuses_european_million_shorthand():
    # "$1,25 million" captures only "$1" under thousands-grouping — refuse it.
    m = AMOUNT_RE.search("in the amount of $1,25 million")
    assert amount_or_none("in the amount of $1,25 million", m) is None


def test_confidential_re_detects_attachment():
    assert CONFIDENTIAL_RE.search("This report has a CONFIDENTIAL ATTACHMENT with value")
    assert not CONFIDENTIAL_RE.search("no such thing here")
