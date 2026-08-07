"""Toronto Zoo Board of Management (#135): the ZB committee on TMMIS.

Same infrastructure as the Bid Award Panel (#68): TMMIS agendas need the headed browser,
report PDFs are plain-HTTP legdocs (e.g. /legdocs/mmis/2025/zb/bgrd/backgroundfile-N.pdf).
Reuses bid_award_panel's prober — references cannot be derived, so probe-and-confirm.
Parsing is handled by LLM extraction (#205).
"""

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.bid_award_panel import (
    cached_agendas,
    parse_agenda_pdfs,
    scrape_agendas,
)
from toronto_bids.store import db

ZB_TERM_STARTS = [
    ("ZB", 2015, "2014-2018", 1),
    ("ZB", 2019, "2018-2022", 1),
    ("ZB", 2023, "2022-2026", 1),
]


def scrape_zb_agendas(virtual_display: bool = False, log=lambda _m: None) -> dict:
    return scrape_agendas(
        config.ZOO_AGENDAS_DIR,
        virtual_display=virtual_display,
        log=log,
        term_starts=ZB_TERM_STARTS,
    )


def cached_zb_agendas() -> dict:
    return cached_agendas(config.ZOO_AGENDAS_DIR)


def download_zoo_reports(conn, http, agendas: dict, log=lambda _m: None) -> int:
    """Index every bgrd PDF the ZB agendas link, then fetch the ones not yet held."""
    from toronto_bids.sources.trca_board import _store_pending_pdfs

    for meeting, html in agendas.items():
        for pdf in parse_agenda_pdfs(html, meeting):
            db.upsert_row(
                conn,
                BackgroundPdf(
                    url=pdf["url"], reference=pdf["reference"], kind="agency_board"
                ),
                overwrite=False,
            )
    conn.commit()
    return _store_pending_pdfs(conn, http, config.ZOO_REPORTS_DIR, "%/zb/%", log, "zoo")


def store_zoo_reports(conn, buyer_id: int) -> dict:
    """Extract and backfill agency_* rows from cached LLM extractions (#205)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "zoo")
    return {
        "solicitations": result["solicitations_written"],
        "awards": result["awards_written"],
    }
