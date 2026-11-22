"""TTC Board of Directors TMMIS capture (#340): the TTC committee on TMMIS (2025+).

As of January 20, 2025, TTC Board and Committee materials moved from ttc.ca to the
City's Toronto Meeting Management Information System (TMMIS). Same infrastructure as
the Zoo (ZB, #135), Exhibition Place (EP, #130), and Toronto Parking Authority (PA,
#335): TMMIS agendas need the headed browser (Akamai-gated), while report PDFs are
plain-HTTP legdocs (/legdocs/mmis/YYYY/ttc/bgrd/...).

Kept as a separate ``ttc_tmmis`` corpus (``source='ttc_tmmis_board'``) so the
reviewed 2000-2024 ``ttc_board`` archive from ttc.ca remains untouched.
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

# Verified against City of Toronto Open Data Council/Committee meeting schedule
# (2022-2026): Toronto Transit Commission MTG #1 = 2025-01-27 (18 Board meetings
# scheduled in 2022-2026 through 2026-10-07).
TTC_TERM_STARTS = [
    ("TTC", 2025, "2022-2026", 1),
]


def scrape_ttc_agendas(virtual_display: bool = False, log=lambda _m: None) -> dict:
    return scrape_agendas(
        config.TTC_TMMIS_AGENDAS_DIR,
        virtual_display=virtual_display,
        log=log,
        term_starts=TTC_TERM_STARTS,
        auto_terms=True,
    )


def cached_ttc_agendas() -> dict:
    return cached_agendas(config.TTC_TMMIS_AGENDAS_DIR)


def download_ttc_tmmis_reports(
    conn, http, agendas: dict, log=lambda _m: None, *, failures=None
) -> int:
    """Index every legdocs PDF the TTC agendas link, then fetch the /legdocs/mmis/.../ttc/ ones not yet held."""
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
        conn,
        http,
        config.TTC_TMMIS_REPORTS_DIR,
        "%/legdocs/mmis/%/ttc/%",
        log,
        "ttc_tmmis",
        failures=failures,
    )


def store_ttc_tmmis_reports(conn, log=lambda _m: None, failures=None) -> dict:
    """Extract and backfill agency_* rows from cached LLM extractions (#205, #340)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "ttc_tmmis", log=log, failures=failures)
    return {
        "solicitations": result["solicitations_written"],
        "awards": result["awards_written"],
        "bids": result["bids_written"],
    }
