import re

# Placeholder / junk values that survive digit-stripping but are not real doc numbers.
_DENYLIST = {"1111111111", "0000000000", "1234567890"}

_NON_DIGIT = re.compile(r"[^0-9]")


def normalize_document_number(raw: str | None) -> str | None:
    """Return the canonical 10-digit document number, or None if not derivable.

    Rule (see spec §3.3): strip all non-digits, require exactly 10 digits,
    reject a placeholder denylist. Excel scientific-notation corruption
    (e.g. "3.77E+1100") is unrecoverable and rejected because it does not
    strip to exactly 10 digits.
    """
    if raw is None:
        return None
    digits = _NON_DIGIT.sub("", str(raw))
    if len(digits) != 10:
        return None
    if digits in _DENYLIST:
        return None
    return digits
