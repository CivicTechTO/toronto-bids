"""TRCA board/executive report parsing (#135). Pure over pdftotext -layout text.

The RECOMMENDATION block is the reliable structure: 'RFT No. <ref> ... be awarded to
<winner> at a total cost not to exceed $<amount>'. The results TABLE is fused multi-line
pdftotext output (names wrap beside prices — the #83 trap) and is never mined; the
bidder LIST comes from the clean '•' bullets after 'received from the following
Proponent(s)'.
"""
import re

# 'RFP No. 10036307' / 'RFT No. 10039751, 10039753' — match the ref shape (8 digits),
# never the label vocabulary (call_number lesson: labels vary, shapes don't).
_REFS = re.compile(r"\bR[FQ][TPQ]\s*No\.?\s*((?:\d{8}(?:\s*,\s*)?)+)")
_REF = re.compile(r"\d{8}")
_TITLE = re.compile(r"^RE:\s*(.+?)(?=^\S|\Z)", re.M | re.S)
# One award clause: ref ... awarded to WINNER at a total cost not to exceed $AMOUNT.
_AWARD = re.compile(
    r"(?:RFT|RFP|RFQ|Contract)\s*No\.?\s*(\d{8})[^$]*?be\s+awarded\s+to\s+"
    r"(.+?)\s+at\s+a\s+total\s+cost\s+not\s+to\s+exceed\s+"
    r"(\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?)",
    re.S)
# VOR shape: 'establish a Vendor of Record (VOR) arrangement with A and B for ...'
_VOR = re.compile(r"arrangement\s+with\s+(.+?)\s+for\s+the\s+supply", re.S)
_BULLETS_HEAD = re.compile(r"received\s+from\s+the\s+following\s+(?:Proponent|vendor)", re.I)
_BULLET = re.compile(r"^\s*[•]\s*(.+?)\s*$", re.M)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _fix_quotes(name: str) -> str:
    return name.replace("“", '"').replace("”", '"').replace("’", "'")


def _bullet_names(text: str) -> list[str]:
    m = _BULLETS_HEAD.search(text)
    if not m:
        return []
    names, tail = [], text[m.end():m.end() + 2000]
    lines = tail.splitlines()
    current = None
    for line in lines:
        b = _BULLET.match(line)
        if b:
            if current:
                names.append(_fix_quotes(_squash(current)))
            current = b.group(1)
        elif current is not None:
            cont = line.strip()
            # A wrapped bullet is an indented continuation; anything else ends the list.
            if cont and line.startswith((" ", "\t")):
                current += " " + cont
            else:
                break
    if current:
        names.append(_fix_quotes(_squash(current)))
    return names


def parse_trca_report(text: str, report_url: str | None = None) -> list[dict]:
    refs_m = _REFS.search(text)
    if not refs_m:
        return []
    refs = _REF.findall(refs_m.group(1))
    title_m = _TITLE.search(text)
    title = _squash(title_m.group(1)) if title_m else None
    bidders = _bullet_names(text)

    winners_by_ref: dict[str, list] = {r: [] for r in refs}
    for ref, winner, amount in _AWARD.findall(text):
        if ref in winners_by_ref:
            entry = (_fix_quotes(_squash(winner)), amount)
            if entry not in winners_by_ref[ref]:
                winners_by_ref[ref].append(entry)

    # VOR shape: several winners joined by 'and', no per-winner amounts.
    if not any(winners_by_ref.values()):
        vor = _VOR.search(text)
        if vor:
            names = re.split(r"\s+and\s+", _squash(vor.group(1)))
            for ref in refs:
                winners_by_ref[ref] = [(n.strip(), None) for n in names if n.strip()]

    return [{"native_ref": ref, "title": title, "winners": winners_by_ref[ref],
             "bidders": bidders, "report_url": report_url} for ref in refs]
