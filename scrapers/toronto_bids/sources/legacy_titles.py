"""Recover solicitation titles from the archived Ariba posting pages (#65).

The legacy rescue pulled 1,666 `Doc<number>/` folders off the City's old Azure file share,
and 1,576 of them contain the solicitation's own Ariba Discovery posting page. Those pages
carry the real title in `<title>`:

    <title>RFQ for Non-OEM Preventative Vehicle Maintenance and Repairs</title>

This outranks a Bid Award Panel heading (sources/bid_award_panel.py), which describes the
*award* rather than naming the solicitation:

    BA     : 'Award of Ariba Document Number 3524228095 to Various Suppliers for the Non-...'
    legacy : 'RFQ for Non-OEM Preventative Vehicle Maintenance and Repairs'

Both are City-authored, but the posting page is the solicitation's own title, so it wins.
The precedence is enforced explicitly rather than by call order — see fill_titles_from_legacy.

Pure and offline: the bytes are already on disk under TB_DATA_DIR/legacy/, verified by
SHA-256 in manifest.jsonl. No network, no credentials, no browser.
"""
import html as _htmlmod
import pathlib
import re

from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.title import clean_title

_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def titles_from_archive(ariba_data_dir) -> dict[str, str]:
    """{document_number: title} for every archived posting page that names its subject."""
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
            # unescape first: 140 of these carry entities ('Parks &amp; Recreation',
            # 'OTP - &nbsp;Legacy...'), and storing them raw would publish the markup.
            raw = _htmlmod.unescape(match.group(1)).replace("\xa0", " ")
            # Reuse #70's rule so an archived placeholder is rejected the same way the
            # feed's is, rather than sneaking a 'Doc-3524228095' back in through this door.
            title = clean_title(raw)
            if title:
                found[doc] = title
            break
    return found


def fill_titles_from_legacy(conn, ariba_data_dir) -> int:
    """Name title-less solicitations from the archived posting pages. Idempotent.

    Fills NULLs, and also replaces a title sourced from `bid_award_panel`: both are City
    words, but a posting page names the solicitation while a council heading describes the
    award, so the posting page is the better title for the same row. Encoding that here
    rather than relying on which pass runs first means the outcome does not depend on order.

    Never touches a title the City published in the feed itself — that always wins.
    """
    titles = titles_from_archive(ariba_data_dir)
    if not titles:
        return 0
    targets = {r["document_number"] for r in conn.execute(
        "SELECT document_number FROM solicitation "
        "WHERE title IS NULL OR source = 'bid_award_panel'")}
    pending = [(t, d) for d, t in titles.items() if d in targets]
    conn.executemany(
        "UPDATE solicitation SET title = ?, source = 'legacy_ariba_html' "
        "WHERE document_number = ? AND (title IS NULL OR source = 'bid_award_panel')",
        pending)
    conn.commit()
    return len(pending)
