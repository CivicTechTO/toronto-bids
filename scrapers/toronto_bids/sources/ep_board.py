"""Exhibition Place Board of Governors capture (#130): the EP committee on TMMIS.

Same infrastructure as the Zoo's ZB series (#135): TMMIS agendas need the headed browser
(Akamai-gated), report PDFs are plain-HTTP legdocs (/legdocs/mmis/YYYY/ep/bgrd/...). Reuses
the bid_award_panel prober; the EP reference format is YYYY.EP<meeting>.<item> (confirmed
live). Most EP board reports are NOT procurement awards, so the parsers (added next) refuse
non-awards.
"""
import re

from toronto_bids import config
from toronto_bids.models import AgencyAward, AgencyBid, AgencySolicitation, BackgroundPdf
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
    """Collapse a line-wrapped winner and strip a trailing location qualifier. A trailing comma
    is a boundary artifact, not part of the name: when the winner is immediately followed by a
    comma with no space ("...Limited, for the replacement"), the required `\\s+` before the
    boundary alternation forces the comma itself into the capture (#130 live measurement)."""
    if raw is None:
        return None
    return _strip_location(_WS.sub(" ", raw).strip().rstrip(",")).strip() or None


# A confidential agreement's counterparty, when publicly named ("agreement with Coca-Cola …").
# Capital-anchored, so a redacted "a Consumer Show Client" is correctly skipped. Each token in
# the name must itself start capitalized (or be a bare connector: and/of/&) — otherwise a
# lowercase run past the name ("BCC is set to expire", "LiUNA Local 506 applies to") gets
# swept into the capture, since the old class allowed any case up to the next for/to/on/comma.
_NAME_TOKEN = r"(?:[A-Z][A-Za-z0-9&.,'’\-]*|and|of|&)"
_EP_AGREEMENT = re.compile(
    r"agreement\s+with\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,6})"
    r"(?:\s+(?:for|to|on|,)\b|\s*\()", re.S)
_EP_SUBJECT = re.compile(r"^([A-Z].*(?:Tender|Contract|Award|Agreement|RF[TPQ]).*)$", re.M)
# MFIPPA closed-meeting reasons that are NOT procurement (labour negotiations, personal matters,
# property security, litigation/privilege) — City confidential reports cite one of these
# explicitly, and only the financial/commercial and acquisition-disposition reasons cover real
# agreements. Live measurement (#130) found 23 such reports wrongly kept as bare/garbled awards
# (a Collective Agreement report captured "LiUNA Local 506 applies" as a winner) — refuse them.
_EP_NON_PROCUREMENT_REASON = re.compile(
    r"labour relations|employee negotiat|collective agreement|personal matters about|"
    r"security of (?:the )?property|position,?\s*plan|plan to be applied to (?:any )?negotiat|"
    r"solicitor-client|litigation", re.I)


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
        # No competitive award clause. Keep ONLY a confidential procurement agreement — never
        # a labour/personal/security/litigation matter that merely happens to say "agreement".
        if (confidential and not _EP_NON_PROCUREMENT_REASON.search(text)
                and re.search(r"\bagreement\b|\baward\b|\bcontract\b", text, re.I)):
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


# A Table 1 row: a bidder name (letters/&/./,/spaces) followed by its first $ price. The winner
# row carries a second $ (recommended contract price); take the FIRST. Column-header lines
# ("Base Bid Price", "Received", "Recommended Contract Price") have no leading firm name + price
# on the same run and are skipped by requiring a name that ends in a company word OR a name-then-$
# on one line. Refuse a line that isn't a clean name+price (the #94 rule).
_EP_BID_ROW = re.compile(
    r"^\s*([A-Z][A-Za-z0-9&.,'’ \-]{3,60}?)\s+(\$\s?\d{1,3}(?:,\d{3})*\.\d{2})", re.M)
_EP_TABLE_HEAD = re.compile(r"Table\s+\d[^\n]*Tender\s+Price\s+Submission", re.I)


def parse_ep_bid_table(text: str) -> list[tuple[str, str]]:
    """Every (bidder, base-bid-price) in an EP 'Table 1: Tender Price Submission'. Empty if the
    report has no such table."""
    head = _EP_TABLE_HEAD.search(text)
    if not head:
        return []
    # Scope to the region after the header up to a blank-line gap / the next section.
    tail = text[head.end():head.end() + 1500]
    rows = []
    for m in _EP_BID_ROW.finditer(tail):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        price = m.group(2).replace(" ", "")
        # Skip a column-header fragment that slipped through (no company suffix and generic words).
        if name.lower() in {"base bid price", "recommended contract price", "received"}:
            continue
        rows.append((name, price))
    return rows


def store_ep_reports(conn, buyer_id: int) -> dict:
    """Parse held EP reports into agency rows. One AgencySolicitation + AgencyAward per award
    report (confidential ones keep the winner, NULL amount), and one AgencyBid per Table 1 row.
    Non-award reports are refused by parse_ep_report and contribute nothing."""
    counts = {"solicitations": 0, "awards": 0, "bids": 0}
    for row in conn.execute(
            "SELECT reference, url, text FROM background_pdf WHERE kind='agency_board' "
            "AND url LIKE '%/ep/%' AND text IS NOT NULL ORDER BY url").fetchall():
        got = parse_ep_report(row["text"], fallback_ref=row["reference"] or row["url"],
                              report_url=row["url"])
        if got is None:
            continue
        db.upsert_row(conn, AgencySolicitation(
            buyer_id=buyer_id, native_ref=got["native_ref"], title=got["title"],
            status="awarded", posted_date=None, closing_date=None, portal_url=None,
            source="ep_board"), overwrite=False)
        counts["solicitations"] += 1
        db.upsert_row(conn, AgencyAward(
            buyer_id=buyer_id, native_ref=got["native_ref"], supplier_name_raw=got["winner"],
            award_amount=got["amount"], value_confidential=got["confidential"], award_date=None,
            report_url=got["report_url"], source="ep_board"), overwrite=True)
        counts["awards"] += 1
        for bidder, price in parse_ep_bid_table(row["text"]):
            db.upsert_row(conn, AgencyBid(
                buyer_id=buyer_id, native_ref=got["native_ref"], bidder_name_raw=bidder,
                bid_price=price, report_url=row["url"], source="ep_board"), overwrite=True)
            counts["bids"] += 1
    conn.commit()
    return counts
