"""Recover solicitation titles from archived Ariba HTML pages."""
import html as _htmlmod
import pathlib
import re

from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.title import clean_title

_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def titles_from_archive(ariba_data_dir) -> dict[str, str]:
    """Return {document_number: title} parsed from archived HTML <title> tags."""
    root = pathlib.Path(ariba_data_dir)
    if not root.is_dir():
        return {}
    found = {}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        doc = normalize_document_number(folder.name)
        if not doc or doc in found:
            continue
        for page in sorted(folder.glob("*.html")):
            match = _TITLE_TAG.search(page.read_text(errors="replace"))
            if not match:
                continue
            raw = _htmlmod.unescape(match.group(1)).replace("\xa0", " ")
            title = clean_title(raw)
            if title:
                found[doc] = title
            break
    return found


def fill_titles_from_legacy(conn, ariba_data_dir) -> int:
    """Populate NULL or bid_award_panel solicitation titles from archived posting pages.

    Never overwrites feed-sourced titles. Idempotent.
    """
    titles = titles_from_archive(ariba_data_dir)
    if not titles:
        return 0
    targets = {r["document_number"] for r in conn.execute(
        "SELECT document_number FROM solicitation "
        "WHERE title IS NULL OR title_source = 'bid_award_panel'")}
    pending = [(t, d) for d, t in titles.items() if d in targets]
    conn.executemany(
        "UPDATE solicitation SET title = ?, title_source = 'legacy_ariba_html' "
        "WHERE document_number = ? AND (title IS NULL OR title_source = 'bid_award_panel')",
        pending)
    conn.commit()
    return len(pending)
