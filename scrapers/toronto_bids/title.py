"""Detect and normalize placeholder solicitation titles (e.g. 'Doc-3524228095') to None.

Mapping placeholders to NULL lets COALESCE upserts keep real titles and allows backfills to fill gaps.
"""
import re

_NOISE = re.compile(
    r"\b(doc|docs|summary|notice|ariba|no|nbr|number|tender|tenders|"
    r"rfq|rfp|nrfp|rfi|rfsq|noip|call)\b",
    re.I,
)
_DIGITS_AND_PUNCT = re.compile(r"[\d\W_]+")
_WS = re.compile(r"\s+")


def is_placeholder_title(title: str | None) -> bool:
    """Return True if title contains only reference scaffolding and no subject words."""
    if not title or not title.strip():
        return True
    # Strip digits/punctuation first so 'Doc2922336030' splits into 'Doc' for \b matching.
    text = _DIGITS_AND_PUNCT.sub(" ", title)
    text = _NOISE.sub(" ", text)
    return not any(len(word) >= 2 for word in text.split())


def clean_title(title: str | None) -> str | None:
    """Collapse whitespace in title, or return None if it is a placeholder."""
    if is_placeholder_title(title):
        return None
    return _WS.sub(" ", title).strip()


def clear_placeholder_titles(conn) -> int:
    """NULL existing placeholder titles and orphaned title_source stamps. Idempotent.

    Runs every sync because COALESCE preserves non-NULL placeholders on existing rows.
    """
    stale = [row["document_number"] for row in
             conn.execute("SELECT document_number, title FROM solicitation "
                          "WHERE title IS NOT NULL")
             if is_placeholder_title(row["title"])]
    conn.executemany("UPDATE solicitation SET title = NULL WHERE document_number = ?",
                     [(d,) for d in stale])
    orphans = conn.execute(
        "UPDATE solicitation SET title_source = NULL "
        "WHERE title IS NULL AND title_source IS NOT NULL").rowcount
    conn.commit()
    return len(stale) + orphans
