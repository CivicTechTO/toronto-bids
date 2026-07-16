import re

from lxml import html as _html

from toronto_bids.models import CouncilItem

_LEGDOCS = "/legdocs/mmis/"


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed or None


def parse_agenda_item(html: str, reference: str):
    """Parse a TMMIS agenda-item page into a CouncilItem + a deduped list of PDF links.

    Returns (CouncilItem, [{"url": str, "kind": "bgrd"|"comm"}, ...]).
    """
    root = _html.fromstring(html)

    title = root.xpath("//title/text()")
    title = _clean(title[0]) if title else None

    # Decision text: everything after the "City Council Decision" heading until the next heading.
    decision = None
    heads = root.xpath("//*[self::h1 or self::h2 or self::h3][contains(translate(text(),"
                       "'CITY COUNCIL DECISION','city council decision'),'city council decision')]")
    if heads:
        parts = []
        for sib in heads[0].itersiblings():
            if sib.tag in ("h1", "h2", "h3"):
                break
            parts.append(sib.text_content())
        decision = _clean(" ".join(parts))

    seen = set()
    pdfs = []
    for a in root.xpath("//a[contains(@href, '%s')]" % _LEGDOCS):
        url = a.get("href")
        if not url or not url.lower().endswith(".pdf") or url in seen:
            continue
        seen.add(url)
        kind = "bgrd" if "/bgrd/" in url else ("comm" if "/comm/" in url else "other")
        pdfs.append({"url": url, "kind": kind})

    return CouncilItem(reference=reference, title=title, decision_text=decision), pdfs
