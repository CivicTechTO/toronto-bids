import re

_SUBMITTED_BY = re.compile(r"\(\s*submitted by:.*?\)", re.IGNORECASE)
_NON_KEY = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def supplier_key(raw: str | None) -> str:
    """Deterministic grouping key for a raw supplier name.

    Drops a trailing "(Submitted by: …)" note, lowercases, removes every character
    that is not [a-z0-9 ], and collapses whitespace. Legal suffixes (Inc, Ltd, …) are
    intentionally kept so genuinely different entities are not merged. Returns "" for
    blank/garbage input (caller skips those).
    """
    if raw is None:
        return ""
    text = _SUBMITTED_BY.sub(" ", str(raw))
    text = _NON_KEY.sub(" ", text.lower())
    return _WS.sub(" ", text).strip()
