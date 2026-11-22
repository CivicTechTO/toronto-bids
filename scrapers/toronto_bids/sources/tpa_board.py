"""Toronto Parking Authority Board of Directors capture (#335): the PA committee on TMMIS.

Same infrastructure as the Zoo (ZB, #135) and Exhibition Place (EP, #130): TMMIS agendas
need the headed browser (Akamai-gated), while report PDFs are plain-HTTP legdocs
(/legdocs/mmis/YYYY/pa/bgrd/...). Reuses bid_award_panel's prober with automatic Council
term rollover; PA references follow YYYY.PA<meeting>.<item>. Parsing uses the shared LLM
extraction pipeline (#205).
"""

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.bid_award_panel import (
    cached_agendas,
    parse_agenda_pdfs,
    scrape_agendas,
)
from toronto_bids.sources.trca_board import _store_pending_pdfs
from toronto_bids.store import db

# Verified against City of Toronto Open Data Council/Committee meeting schedules:
# 2014-2018 term PA1 = 2017-07-10, 2018-2022 term PA1 = 2018-12-19, 2022-2026 term PA1 = 2022-12-12.
PA_TERM_STARTS = [
    ("PA", 2017, "2014-2018", 1),
    ("PA", 2018, "2018-2022", 1),
    ("PA", 2022, "2022-2026", 1),
]


def scrape_pa_agendas(virtual_display: bool = False, log=lambda _m: None) -> dict:
    return scrape_agendas(
        config.TPA_AGENDAS_DIR,
        virtual_display=virtual_display,
        log=log,
        term_starts=PA_TERM_STARTS,
        auto_terms=True,
    )


def cached_pa_agendas() -> dict:
    return cached_agendas(config.TPA_AGENDAS_DIR)


def download_tpa_reports(
    conn, http, agendas: dict, log=lambda _m: None, *, failures=None
) -> int:
    """Index every legdocs PDF the PA agendas link, then fetch the /pa/ ones not yet held."""
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
    return _store_pending_pdfs(
        conn, http, config.TPA_REPORTS_DIR, "%/pa/%", log, "tpa", failures=failures
    )


def store_tpa_reports(conn, log=lambda _m: None, failures=None) -> dict:
    """Extract and backfill agency_* rows from cached LLM extractions (#205, #335)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "tpa", log=log, failures=failures)
    return {
        "solicitations": result["solicitations_written"],
        "awards": result["awards_written"],
        "bids": result["bids_written"],
    }
