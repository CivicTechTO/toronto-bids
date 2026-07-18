"""Exhibition Place Board of Governors capture (#130): the EP committee on TMMIS.

Same infrastructure as the Zoo's ZB series (#135): TMMIS agendas need the headed browser
(Akamai-gated), report PDFs are plain-HTTP legdocs (/legdocs/mmis/YYYY/ep/bgrd/...). Reuses
the bid_award_panel prober; the EP reference format is YYYY.EP<meeting>.<item> (confirmed
live). Most EP board reports are NOT procurement awards, so the parsers (added next) refuse
non-awards.
"""
from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.bid_award_panel import (cached_agendas, parse_agenda_pdfs,
                                                  scrape_agendas)
from toronto_bids.sources.trca_board import _store_pending_pdfs
from toronto_bids.store import db

# EP meetings reset numbering per council term, like ZB. Confirmed live: the 2022-2026 term
# runs EP1..EP23 (as of 2026-06). The 2018-2022 term is probed too (2022.EP25 seen).
EP_TERM_STARTS = [
    ("EP", 2019, "2018-2022", 1),
    ("EP", 2023, "2022-2026", 1),
]


def scrape_ep_agendas(virtual_display: bool = False, log=lambda _m: None) -> dict:
    return scrape_agendas(config.EP_AGENDAS_DIR, virtual_display=virtual_display,
                          log=log, term_starts=EP_TERM_STARTS)


def cached_ep_agendas() -> dict:
    return cached_agendas(config.EP_AGENDAS_DIR)


def download_ep_reports(conn, http, agendas: dict, log=lambda _m: None) -> int:
    """Index every bgrd PDF the EP agendas link, then fetch the ones not yet held. Plain HTTP,
    resilient (a dead URL is skipped), sha256-queued. EP reports live under /legdocs/.../ep/."""
    for meeting, html in agendas.items():
        for pdf in parse_agenda_pdfs(html, meeting):
            db.upsert_row(conn, BackgroundPdf(url=pdf["url"], reference=pdf["reference"],
                                              kind="agency_board"), overwrite=False)
    conn.commit()
    return _store_pending_pdfs(conn, http, config.EP_REPORTS_DIR, "%/ep/%", log, "ep")
