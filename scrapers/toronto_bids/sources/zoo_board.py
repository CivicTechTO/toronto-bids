"""Toronto Zoo Board of Management (#135): the ZB committee on TMMIS.

Same infrastructure as the Bid Award Panel (#68): TMMIS agendas need the headed browser,
report PDFs are plain-HTTP legdocs (e.g. /legdocs/mmis/2025/zb/bgrd/backgroundfile-N.pdf).
Reuses bid_award_panel's prober — references cannot be derived, so probe-and-confirm.
"""
import re

from toronto_bids import config
from toronto_bids.models import AgencyAward, AgencySolicitation, BackgroundPdf
from toronto_bids.sources.agency_report import (
    AMOUNT_PHRASE as _AMOUNT_PHRASE, AMOUNT_RE as _ZOO_AMOUNT,
    CONFIDENTIAL_RE as _CONFIDENTIAL, MONEY as _MONEY, amount_or_none as _amount_or_none,
)
from toronto_bids.sources.bid_award_panel import (cached_agendas, parse_agenda_pdfs,
                                                  scrape_agendas)
from toronto_bids.store import db

# The Zoo board's TMMIS record is evidenced from 2019 (ZB1.06, 2019-01-28); the 2014-2018
# probe is cheap insurance (4 misses) in case earlier meetings exist.
ZB_TERM_STARTS = [
    ("ZB", 2015, "2014-2018", 1),
    ("ZB", 2019, "2018-2022", 1),
    ("ZB", 2023, "2022-2026", 1),
]


def scrape_zb_agendas(virtual_display: bool = False, log=lambda _m: None) -> dict:
    return scrape_agendas(config.ZOO_AGENDAS_DIR, virtual_display=virtual_display,
                          log=log, term_starts=ZB_TERM_STARTS)


def cached_zb_agendas() -> dict:
    return cached_agendas(config.ZOO_AGENDAS_DIR)


def download_zoo_reports(conn, http, agendas: dict, log=lambda _m: None) -> int:
    """Index every bgrd PDF the ZB agendas link, then fetch the ones not yet held.

    Plain HTTP (legdocs is not Akamai-gated). Queue on sha256 IS NULL (#96).
    """
    # Shared resilient fetch loop (skips a dead URL rather than aborting the body, #135).
    from toronto_bids.sources.trca_board import _store_pending_pdfs
    for meeting, html in agendas.items():
        for pdf in parse_agenda_pdfs(html, meeting):
            db.upsert_row(conn, BackgroundPdf(url=pdf["url"], reference=pdf["reference"],
                                              kind="agency_board"), overwrite=False)
    conn.commit()
    return _store_pending_pdfs(conn, http, config.ZOO_REPORTS_DIR, "%/zb/%", log, "zoo")


# ---------------------------------------------------------------------------
# Report parser (pure) + storage (#135 Task 6)
# ---------------------------------------------------------------------------

_ZOO_REF = re.compile(r"\b(R[FQ][TPQ][\s-]*\d{1,3}(?:\s*\(\d{4}-\d{2}\))?)")
_ZOO_WINNER = re.compile(
    r"award(?:ed)?\s+(?:of\s+)?(?:the\s+)?[\w\s–-]{0,80}?\s+to\s+"
    r"([A-Z][A-Za-z0-9&.,'’ \-]+?(?:Inc|Ltd|Limited|Corp|Corporation|Company)\.?)")
# Alternate phrasing: "execute an agreement with NAME ... for the award of ..." — the
# perimeter-fence report names its winner this way instead of "award ... to NAME".
_ZOO_WINNER_AGREEMENT = re.compile(
    r"agreement\s+with\s+([A-Z][A-Za-z0-9&.,'’ \-]+?(?:Inc|Ltd|Limited|Corp|Corporation|Company)\.?)"
    r"\s*(?:\([^)]*\))?\s+for\s+the\s+award", re.S)
# The primary award pattern: "... to WINNER <amount-phrase> $AMOUNT". The winner is bounded
# and carries NO legal-suffix requirement — the corpus is full of suffix-less firms
# ("Tri-Unite Systems", "Precise ParkLink", "Provincial Roofing") that the suffix-anchored
# regex dropped entirely. The amount phrase on the right anchors the winner's end, the same
# way the TRCA parser bounds its winner (#138). Case-sensitive leading capital (the winner is
# a proper noun); the amount phrase is case-insensitive via an inline group.
_ZOO_AWARD = re.compile(
    r"\bto\s+([A-Z][A-Za-z0-9&.,'’()/\- ]{2,60}?)\s+(?i:" + _AMOUNT_PHRASE + r")\s*" + _MONEY)
_SUBJECT = re.compile(r"^(?:Subject:|\s*)(.*(?:Tender|RFT|RFP|Award|Contract).*)$", re.M)


def parse_zoo_report(text: str, fallback_ref: str, report_url: str | None = None) -> dict | None:
    if "award" not in text.lower():
        return None                          # not an award report
    confidential = 1 if _CONFIDENTIAL.search(text) else 0
    ref_m = _ZOO_REF.search(text)
    native_ref = re.sub(r"\s+", " ", ref_m.group(1)).strip() if ref_m else fallback_ref
    # Primary: winner and amount together ("to WINNER in the amount of $X"), no suffix needed.
    winner = amount = None
    combined = None if confidential else _ZOO_AWARD.search(text)
    if combined:
        winner, amount = combined.group(1).strip(), _amount_or_none(text, combined)
    else:
        # Fallbacks: a suffix-anchored winner (handles confidential reports that name a
        # winner but withhold the value), and a standalone amount search.
        winner_m = _ZOO_WINNER.search(text) or _ZOO_WINNER_AGREEMENT.search(text)
        winner = winner_m.group(1).strip() if winner_m else None
        amount = None if confidential else _amount_or_none(text, _ZOO_AMOUNT.search(text))
    # "award" appears in plenty of reports that award nothing (updates, minutes, info
    # items). Store a row only when something concrete was extracted — a named winner, an
    # amount, or a confidential-attachment award. Otherwise refuse: a contentless award
    # row keyed on a meeting reference is worse than none (the archive's guiding rule, #135).
    if not (winner or amount or confidential):
        return None
    title_m = _SUBJECT.search(text)
    return {
        "native_ref": native_ref,
        "title": re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else None,
        "winner": winner,
        "amount": amount,
        "confidential": confidential,
        "report_url": report_url,
    }


def store_zoo_reports(conn, buyer_id: int) -> dict:
    """Parse held ZB reports into agency rows. A confidential award is recorded with
    value_confidential=1 and a NULL amount; the publicly-named winner is preserved when
    the report names one — the flag records the value is withheld, not that the winner is unknown."""
    counts = {"solicitations": 0, "awards": 0}
    for row in conn.execute(
            "SELECT reference, url, text FROM background_pdf WHERE kind='agency_board' "
            "AND url LIKE '%/zb/%' AND text IS NOT NULL ORDER BY url").fetchall():
        got = parse_zoo_report(row["text"], fallback_ref=row["reference"] or row["url"],
                               report_url=row["url"])
        if got is None:
            continue
        db.upsert_row(conn, AgencySolicitation(
            buyer_id=buyer_id, native_ref=got["native_ref"], title=got["title"],
            status="awarded", posted_date=None, closing_date=None, portal_url=None,
            source="zoo_board"), overwrite=False)
        counts["solicitations"] += 1
        db.upsert_row(conn, AgencyAward(
            buyer_id=buyer_id, native_ref=got["native_ref"],
            supplier_name_raw=got["winner"],
            award_amount=got["amount"],
            value_confidential=got["confidential"], award_date=None,
            report_url=got["report_url"], source="zoo_board"), overwrite=True)
        counts["awards"] += 1
    conn.commit()
    return counts
