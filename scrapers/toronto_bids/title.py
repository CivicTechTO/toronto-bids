"""Tell a solicitation title from a placeholder wearing one's clothes (#70).

For most awarded solicitations the City publishes the document number *as* the title —
`Doc-3524228095`, `Doc-Doc2922336030 (1062021)`, `Doc-Ariba Doc No. 2243638006 RFP NO.
9118205024`. These name no subject. They are the absence of a title, not a title.

Storing them verbatim costs us twice, and both are #70:

  * `db._upsert_keyed` COALESCEs, which guards against a NULL wiping a value but not against
    a *worse* non-NULL one. 144 documents have more than one feed record, feed order is
    arbitrary, and a placeholder is non-NULL — so a placeholder record silently overwrote a
    real title on 10 documents.
  * `overwrite=False` fills only NULLs, and `'Doc-3524228095'` is not NULL — so a backfill
    source could never supply a title the City never published.

Normalising them to None fixes both with no new machinery: COALESCE already does the right
thing in each direction once the placeholder is spelled NULL. `title IS NULL` then honestly
means "we do not know", replacing the `title LIKE 'Doc-%'` idiom.

Nothing is lost. A pure placeholder is reconstructible from `document_number`, the ~95 that
carry a secondary tender reference are keeping it in the wrong field anyway, and the OData
feed is re-read on every sync — so if that reference is ever wanted, it wants its own column.
"""
import re

# Tokens that are reference scaffolding, never subject matter.
_NOISE = re.compile(
    r"\b(doc|docs|summary|notice|ariba|no|nbr|number|tender|tenders|"
    r"rfq|rfp|nrfp|rfi|rfsq|noip|call)\b",
    re.I,
)
_DIGITS_AND_PUNCT = re.compile(r"[\d\W_]+")
_WS = re.compile(r"\s+")


def is_placeholder_title(title: str | None) -> bool:
    """True when nothing but reference scaffolding remains — i.e. it names no subject.

    Keys on words, not length: 'Sewer' and 'Bed Frames' are real titles, while the
    47-character 'Doc-Ariba Doc No. 2243638006 RFP NO. 9118205024' is not.
    """
    if not title or not title.strip():
        return True
    # Digits and punctuation go FIRST so 'Doc2922336030' decomposes into a bare 'Doc'.
    # Stripping noise words first would leave that 'Doc' glued to its digits, where \b
    # cannot see it, and the leftover would read as a word — silently sparing the whole
    # 'Doc-Doc##########' family (~564 rows).
    text = _DIGITS_AND_PUNCT.sub(" ", title)
    text = _NOISE.sub(" ", text)
    return not any(len(word) >= 2 for word in text.split())


def clean_title(title: str | None) -> str | None:
    """The title as the City published it, or None when it published a placeholder.

    Whitespace is collapsed; the title is otherwise verbatim.
    """
    if is_placeholder_title(title):
        return None
    return _WS.sub(" ", title).strip()


def clear_placeholder_titles(conn) -> int:
    """NULL any placeholder title already in the store. Idempotent. Returns rows cleared.

    Needed because COALESCE keeps the existing value when the incoming one is NULL: rows
    written before `clean_title` existed would keep their placeholder forever. Runs every
    sync rather than as a one-shot migration — it is a no-op once clean, and it re-applies
    itself for free if the rule above ever improves.
    """
    stale = [row["document_number"] for row in
             conn.execute("SELECT document_number, title FROM solicitation "
                          "WHERE title IS NOT NULL")
             if is_placeholder_title(row["title"])]
    conn.executemany("UPDATE solicitation SET title = NULL WHERE document_number = ?",
                     [(d,) for d in stale])
    conn.commit()
    return len(stale)
