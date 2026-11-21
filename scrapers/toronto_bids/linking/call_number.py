"""Pre-Ariba City call number parsing (division-year-sequence and tender number-year)."""
import re

# Try division-year-sequence first; it contains a substring matching number-year.
_CALL_CORE = re.compile(r"\b(\d{4}-\d{2}-\d{4}|\d{1,4}-(?:19|20)\d{2})\b")
_DASHES = re.compile(r"[‐-―−]")
_SPACED_DASH = re.compile(r"[ \t]*-[ \t]*")


def normalize_call_number(raw: str | None) -> str | None:
    """Return the first canonical call number, ignoring prefixes and trailing contract numbers."""
    if raw is None:
        return None
    text = _SPACED_DASH.sub("-", _DASHES.sub("-", str(raw)))
    match = _CALL_CORE.search(text)
    return match.group(1) if match else None


def find_call_numbers(raw: str | None) -> set[str]:
    """All distinct printed calls, without matching a Tender substring inside an RFQ."""
    if not raw:
        return set()
    text = _SPACED_DASH.sub("-", _DASHES.sub("-", str(raw)))
    return {match.group(1) for match in _CALL_CORE.finditer(text)}
