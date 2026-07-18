"""Toronto Zoo Board of Management (#135): the ZB committee on TMMIS.

Same infrastructure as the Bid Award Panel (#68): TMMIS agendas need the headed browser,
report PDFs are plain-HTTP legdocs (e.g. /legdocs/mmis/2025/zb/bgrd/backgroundfile-N.pdf).
Reuses bid_award_panel's prober — references cannot be derived, so probe-and-confirm.
"""
import hashlib
import re

from toronto_bids import config
from toronto_bids.models import AgencyAward, AgencySolicitation, BackgroundPdf
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
    from toronto_bids.sources.trca_board import _pdftotext   # same text extraction
    config.ZOO_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for meeting, html in agendas.items():
        for pdf in parse_agenda_pdfs(html, meeting):
            db.upsert_row(conn, BackgroundPdf(url=pdf["url"], reference=pdf["reference"],
                                              kind="agency_board"), overwrite=False)
    conn.commit()
    n = 0
    for row in conn.execute("SELECT id, url FROM background_pdf "
                            "WHERE kind='agency_board' AND url LIKE '%/zb/%' "
                            "AND sha256 IS NULL ORDER BY id").fetchall():
        blob = http.get_bytes(row["url"])
        if not blob.startswith(b"%PDF"):
            continue
        sha = hashlib.sha256(blob).hexdigest()
        path = config.ZOO_REPORTS_DIR / f"{sha}.pdf"
        path.write_bytes(blob)
        conn.execute("UPDATE background_pdf SET sha256=?, local_path=?, text=? WHERE id=?",
                     (sha, str(path), _pdftotext(path), row["id"]))
        conn.commit()
        n += 1
        log(f"  zoo report {n}: {row['url']}")
    return n


# ---------------------------------------------------------------------------
# Report parser (pure) + storage (#135 Task 6)
# ---------------------------------------------------------------------------

_ZOO_REF = re.compile(r"\b(R[FQ][TPQ][\s-]*\d{1,3}(?:\s*\(\d{4}-\d{2}\))?)")
_CONFIDENTIAL = re.compile(r"CONFIDENTIAL\s+ATTACHMENT", re.I)
_ZOO_WINNER = re.compile(
    r"award(?:ed)?\s+(?:of\s+)?(?:the\s+)?[\w\s–-]{0,80}?\s+to\s+"
    r"([A-Z][A-Za-z0-9&.,'’ \-]+?(?:Inc|Ltd|Limited|Corp|Corporation|Company)\.?)")
# Alternate phrasing: "execute an agreement with NAME ... for the award of ..." — the
# perimeter-fence report names its winner this way instead of "award ... to NAME".
_ZOO_WINNER_AGREEMENT = re.compile(
    r"agreement\s+with\s+([A-Z][A-Za-z0-9&.,'’ \-]+?(?:Inc|Ltd|Limited|Corp|Corporation|Company)\.?)"
    r"\s*(?:\([^)]*\))?\s+for\s+the\s+award", re.S)
_ZOO_AMOUNT = re.compile(r"total\s+cost\s+(?:not\s+to\s+exceed\s+)?(\$[\d,]+(?:\.\d{2})?)", re.I)
_SUBJECT = re.compile(r"^(?:Subject:|\s*)(.*(?:Tender|RFT|RFP|Award|Contract).*)$", re.M)


def parse_zoo_report(text: str, fallback_ref: str, report_url: str | None = None) -> dict | None:
    if "award" not in text.lower():
        return None                          # not an award report
    confidential = 1 if _CONFIDENTIAL.search(text) else 0
    ref_m = _ZOO_REF.search(text)
    native_ref = re.sub(r"\s+", " ", ref_m.group(1)).strip() if ref_m else fallback_ref
    winner_m = _ZOO_WINNER.search(text) or _ZOO_WINNER_AGREEMENT.search(text)
    amount_m = None if confidential else _ZOO_AMOUNT.search(text)
    title_m = _SUBJECT.search(text)
    return {
        "native_ref": native_ref,
        "title": re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else None,
        "winner": winner_m.group(1).strip() if winner_m else None,
        "amount": amount_m.group(1) if amount_m else None,
        "confidential": confidential,
        "report_url": report_url,
    }


def store_zoo_reports(conn, buyer_id: int) -> dict:
    """Parse held ZB reports into agency rows. A confidential award is recorded as an
    award row with NULL supplier/amount and value_confidential=1 — the award happened;
    the value is withheld, which is itself a fact worth archiving."""
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
