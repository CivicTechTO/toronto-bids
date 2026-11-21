import re

_DENYLIST = {"1111111111", "0000000000", "1234567890"}

_NON_DIGIT = re.compile(r"[^0-9]")


def normalize_document_number(raw: str | None) -> str | None:
    """Strip non-digits and return a 10-digit document number, or None."""
    if raw is None:
        return None
    digits = _NON_DIGIT.sub("", str(raw))
    if len(digits) != 10:
        return None
    if digits in _DENYLIST:
        return None
    return digits


_TITLE_DOC = re.compile(r"Doc(\d{10})(?!\d)")


def bridge_document_number(external_rfx_id: str | None, title: str | None) -> str | None:
    """Extract a 10-digit document_number from external_rfx_id, falling back to title."""
    doc = normalize_document_number(external_rfx_id)
    if doc is not None:
        return doc
    if title:
        match = _TITLE_DOC.search(title)
        if match:
            return normalize_document_number(match.group(1))
    return None
