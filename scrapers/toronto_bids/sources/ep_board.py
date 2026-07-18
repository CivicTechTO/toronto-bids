"""Exhibition Place Board of Governors capture (#130): the EP committee on TMMIS.

Same infrastructure as the Zoo's ZB series (#135): TMMIS agendas need the headed browser
(Akamai-gated), report PDFs are plain-HTTP legdocs (/legdocs/mmis/YYYY/ep/bgrd/...). Reuses
the bid_award_panel prober; the EP reference format is YYYY.EP<meeting>.<item> (confirmed
live). Most EP board reports are NOT procurement awards, so the parsers (added next) refuse
non-awards.
"""
import re

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.agency_report import AMOUNT_RE, CONFIDENTIAL_RE, amount_or_none
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


# The EP solicitation ref: RFT No. EP###-YYYY (primary) or a Contract No. token. Match the
# shape, not the label vocabulary.
_EP_RFT = re.compile(r"RF[TPQ]\s*No\.?\s*(EP\d+-\d{4})", re.I)
_EP_CONTRACT = re.compile(r"Contract\s+No\.?\s*([0-9][0-9A-Za-z\-]{3,})", re.I)
# The competitive award clause: "award of ... to WINNER for/at the <project> ..." — WINNER is
# bounded and STOPS at " for "/" at "/a comma/the amount phrase (EP puts the project between the
# winner and the amount, so the shared Zoo "to WINNER <phrase> $" over-captures). The winner
# class ALLOWS internal whitespace (`\s` + re.S) because pdftotext wraps long firm names across
# lines ("Westbury National\nShow System Ltd."); _squash collapses it. A trailing location
# qualifier (" of Cambridge, Ontario") is stripped afterwards by _strip_location.
_EP_AWARD = re.compile(
    r"award\s+of\s+(?:the\s+)?(?:Contract|RF[TPQ]|Tender)\b[^$]{0,120}?\bto\s+"
    r"([A-Z][A-Za-z0-9&.,'’()\-\s]{2,60}?)\s+(?:for\b|at\b|,|in\s+the\s+amount)", re.I | re.S)
# End-anchored location qualifier to strip from a winner ("Firm Ltd. of Cambridge, Ontario").
# Narrow (a space-delimited "of <Place>, <Province>") so real names like "…& Sons" survive.
_LOCATION = re.compile(r"\s+of\s+[A-Z][A-Za-z. ]+,\s*(?:Ontario|Canada|Quebec|Alberta|B\.?C\.?)\.?$", re.I)


def _strip_location(name: str) -> str:
    return _LOCATION.sub("", name).strip()


_WS = re.compile(r"\s+")


def _winner(raw: str | None) -> str | None:
    """Collapse a line-wrapped winner and strip a trailing location qualifier."""
    if raw is None:
        return None
    return _strip_location(_WS.sub(" ", raw).strip()) or None


# A confidential agreement's counterparty, when publicly named ("agreement with Coca-Cola …").
# Capital-anchored, so a redacted "a Consumer Show Client" is correctly skipped.
_EP_AGREEMENT = re.compile(
    r"agreement\s+with\s+([A-Z][A-Za-z0-9&.,'’ \-]{2,45}?)(?:\s+(?:for|to|on|,)\b|\s*\()", re.S)
_EP_SUBJECT = re.compile(r"^([A-Z].*(?:Tender|Contract|Award|Agreement|RF[TPQ]).*)$", re.M)


def _ep_ref(text: str, fallback_ref: str) -> str:
    m = _EP_RFT.search(text)
    if m:
        return m.group(1).upper()
    m = _EP_CONTRACT.search(text)
    return m.group(1) if m else fallback_ref


def parse_ep_report(text: str, fallback_ref: str, report_url: str | None = None) -> dict | None:
    """Map one EP board report to an award, or None. Most EP reports are NOT procurement awards
    (WSIB safety, status updates, governance) — refuse those. Keeps a confidential award (value
    withheld) when it names a real counterparty OR is an explicit procurement agreement."""
    confidential = 1 if CONFIDENTIAL_RE.search(text) else 0
    aw = _EP_AWARD.search(text)
    winner = _winner(aw.group(1)) if aw else None
    amount = None if confidential else amount_or_none(text, AMOUNT_RE.search(text)) if aw else None

    if not aw:
        # No competitive award clause. Keep ONLY a confidential procurement agreement.
        if confidential and re.search(r"\bagreement\b|\baward\b|\bcontract\b", text, re.I):
            ag = _EP_AGREEMENT.search(text)
            winner = _winner(ag.group(1)) if ag else None
        else:
            return None                              # not a procurement award — refuse

    ref_m = _EP_SUBJECT.search(text)
    return {
        "native_ref": _ep_ref(text, fallback_ref),
        "title": re.sub(r"\s+", " ", ref_m.group(1)).strip() if ref_m else None,
        "winner": winner,
        "amount": amount,
        "confidential": confidential,
        "report_url": report_url,
    }
