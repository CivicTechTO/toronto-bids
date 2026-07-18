"""Pure regex primitives shared by the agency board-report parsers (Zoo, Exhibition Place, …).

Amounts are written many ways and "in the amount of" dominates the corpus; a truncated
"$1,25 million" shorthand (comma decimal + scale word) captures a bogus "$1" and is refused.
"""
import re

AMOUNT_PHRASE = (
    r"(?:in\s+the\s+amount\s+of|at\s+a\s+(?:total\s+)?cost(?:\s+not\s+to\s+exceed)?(?:\s+of)?"
    r"|for\s+the\s+(?:total\s+)?(?:sum|amount)\s+of|in\s+an\s+amount\s+not\s+to\s+exceed"
    r"|total\s+cost\s+(?:not\s+to\s+exceed\s+)?(?:of\s+)?)")
MONEY = r"(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)"
AMOUNT_RE = re.compile(r"(?i:" + AMOUNT_PHRASE + r")\s*" + MONEY)
CONFIDENTIAL_RE = re.compile(r"CONFIDENTIAL\s+ATTACHMENT", re.I)
_TRUNCATED_AMOUNT = re.compile(r"\s*(?:[.,]\d|million|billion)", re.I)


def amount_or_none(text: str, m) -> str | None:
    """The matched money string with spaces stripped, or None if the match is a truncated
    "$X,YY million" shorthand (a bogus tiny figure) — refuse rather than store $1."""
    if m is None or _TRUNCATED_AMOUNT.match(text, m.end()):
        return None
    return m.group(m.lastindex).replace(" ", "")
