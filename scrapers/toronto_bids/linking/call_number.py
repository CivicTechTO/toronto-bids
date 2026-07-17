"""The pre-Ariba keyspace: Call Numbers (#96).

Toronto adopted Ariba around 2019 and the spine is keyed on the 10-digit document number it
issues. Awards from 2009-2012 predate it entirely and identify themselves by Call Number, so
they are a third keyspace alongside `document_number` and non-competitive's
`workspace_number` — with no join to either, exactly as CLAUDE.md describes for
non-competitive contracts.

The City writes the same number many ways ("Request for Quotation 3905-10-0097", "Request
for Quotation No. 3905-10-0097", "RFQ 3905-10-0097"), so the prefix carries no information
and is dropped. Two shapes account for 1,072 of the 1,076 call numbers in the corpus:

    3905-10-0097    division-year-sequence, used by RFQ/RFP           (516)
    317-2010        number-year, used by Tender Call                  (556)

Unlike document_number this cannot require a fixed digit count: the Tender Call form runs
from 1 to 4 leading digits ("1-2010" through "3175-2010"), so the year anchor is what makes
it a shape rather than "any two numbers with a dash".
"""
import re

# Order matters: the division-year-sequence form contains a substring that would otherwise
# satisfy the number-year form, so it must be tried first.
_CALL_CORE = re.compile(r"\b(\d{4}-\d{2}-\d{4}|\d{1,4}-(?:19|20)\d{2})\b")
# The separator is not always a hyphen and not always tight. Both variants are typography,
# not meaning, and both are silent data loss if unhandled: en-dashes ("9130–10–7291") and a
# spaced dash ("Tender Call No. 120 -2011") account for all 7 call numbers the shapes
# otherwise miss.
_DASHES = re.compile(r"[‐-―−]")
_SPACED_DASH = re.compile(r"[ \t]*-[ \t]*")


def normalize_call_number(raw: str | None) -> str | None:
    """Return the canonical call number, or None if the string carries none.

    Reads the first core matching a known shape and ignores everything around it — the
    prefix vocabulary varies freely, and a Tender Call cites a Contract No. after it
    ("Tender Call No. 317-2010, Contract No. 10TE-17WS") that is a different identifier and
    must not be mistaken for the call.
    """
    if raw is None:
        return None
    text = _SPACED_DASH.sub("-", _DASHES.sub("-", str(raw)))
    match = _CALL_CORE.search(text)
    return match.group(1) if match else None
