"""Toronto Zoo Board of Management (#135): the ZB committee on TMMIS.

Same infrastructure as the Bid Award Panel (#68): TMMIS agendas need the headed browser,
report PDFs are plain-HTTP legdocs (e.g. /legdocs/mmis/2025/zb/bgrd/backgroundfile-N.pdf).
Reuses bid_award_panel's prober — references cannot be derived, so probe-and-confirm.
"""
import hashlib

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
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
