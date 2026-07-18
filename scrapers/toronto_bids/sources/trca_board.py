"""TRCA board/executive report parsing (#135). Pure over pdftotext -layout text.

The RECOMMENDATION block is the reliable structure: 'RFT No. <ref> ... be awarded to
<winner> at a total cost not to exceed $<amount>'. The results TABLE is fused multi-line
pdftotext output (names wrap beside prices — the #83 trap) and is never mined; the
bidder LIST comes from the clean '•' bullets after 'received from the following
Proponent(s)'.
"""
import hashlib
import pathlib
import re
import subprocess
from html import unescape

import httpx

from toronto_bids import config
from toronto_bids.models import AgencyAward, AgencyBid, AgencySolicitation, BackgroundPdf
from toronto_bids.store import db

# 'RFP No. 10036307' / 'RFT No. 10039751, 10039753' — match the ref shape (8 digits),
# never the label vocabulary (call_number lesson: labels vary, shapes don't).
# A TRCA reference is an 8-digit number, and the LABEL in front of it varies far more than
# the two abbreviations the first cut matched: reports spell out "Request for Quotation No.",
# write "Contract #10008808", or say "Tender No." — matching only RFT/RFP/RFQ dropped the
# report entirely (#138). Match the shape (8 digits) in any procurement-label context, never
# the vocabulary — the same lesson as the Ariba doc-number linking. `_REF_ONE` also absorbs a
# trailing ", NNNNNNNN and NNNNNNNN" list so a multi-ref call is captured whole.
_REF_LABEL = r"(?:RF[TPQ]|Request\s+for\s+(?:Proposal|Quotation|Tender)|Contract|Tender)"
_REF_ONE = re.compile(
    _REF_LABEL + r"s?\.?\s*(?:No\.?|#)?\s*(\d{8}(?:\s*(?:,|and|&)\s*\d{8})*)", re.I)
_REF = re.compile(r"\d{8}")
_TITLE = re.compile(r"^RE:\s*(.+?)(?=^\S|\Z)", re.M | re.S)
# One award clause: "awarded to WINNER at a [total][annual] cost/upset-limit ... $AMOUNT".
# The ref is NOT required inside the clause (it is associated afterwards by position), and the
# WINNER is bounded — no '$', at most 90 chars — so it can never run on: the first cut's
# unbounded `(.+?)` under DOTALL captured 268,757 chars when the nearest cost phrase wasn't the
# exact expected string (#138). The cost-phrase alternation is widened so the NEAREST one
# anchors the match. The amount subpattern is the strict grouping form (#73) so a trailing
# ", plus taxes" comma cannot leak into the number.
_AWARD = re.compile(
    r"awarded\s+to\s+([^$]{3,90}?)\s+(?:be\s+extended\s+)?"
    r"at\s+an?\s+(?:total\s+)?(?:annual\s+)?(?:cost|upset\s+limit|fee|price|value)"
    r"[^$]{0,50}?(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)",
    re.I | re.S)
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


def _ref_occurrences(text: str) -> list[tuple[str, int]]:
    """Every (8-digit ref, position) mention, in document order. A comma/'and' list under one
    label yields one entry per number, all sharing the label's position."""
    occ = []
    for m in _REF_ONE.finditer(text):
        for num in _REF.findall(m.group(1)):
            occ.append((num, m.start()))
    return occ


def parse_trca_report(text: str, report_url: str | None = None) -> list[dict]:
    occurrences = _ref_occurrences(text)
    if not occurrences:
        return []
    refs = list(dict.fromkeys(num for num, _pos in occurrences))  # ordered, unique
    title_m = _TITLE.search(text)
    title = _squash(title_m.group(1)) if title_m else None
    bidders = _bullet_names(text)

    winners_by_ref: dict[str, list] = {r: [] for r in refs}
    seen_amounts_by_ref: dict[str, set] = {r: set() for r in refs}
    for m in _AWARD.finditer(text):
        winner = _fix_quotes(_squash(m.group(1)))
        amount = m.group(2).replace(" ", "")            # "$ 527,000" -> "$527,000"
        # Attach the award to the nearest ref mentioned before it — how a multi-ref report
        # keys each award (armour stone restates "RFT No. 10039753" right before its clause).
        preceding = [num for num, pos in occurrences if pos <= m.start()]
        ref = preceding[-1] if preceding else refs[0]
        # RECOMMENDATION and RATIONALE both carry a clause for the same award, sometimes under
        # different name strings (legal vs. trade name) — dedupe by amount, keeping the first.
        if amount not in seen_amounts_by_ref[ref]:
            seen_amounts_by_ref[ref].add(amount)
            winners_by_ref[ref].append((winner, amount))

    # VOR shape: several winners joined by 'and', no per-winner amounts.
    if not any(winners_by_ref.values()):
        vor = _VOR.search(text)
        if vor:
            names = re.split(r"\s+and\s+", _squash(vor.group(1)))
            for ref in refs:
                winners_by_ref[ref] = [(n.strip(), None) for n in names if n.strip()]

    # Only refs that actually carry an award. Broadening ref recall also catches numbers that
    # are not awards — a losing bid's contract number in the results table (armour stone's
    # by-barge 10039750), an unrelated contract cited in passing — and a winnerless ref would
    # be a contentless solicitation row, the same noise the Zoo parser refuses (#135/#138).
    return [{"native_ref": ref, "title": title, "winners": winners_by_ref[ref],
             "bidders": bidders, "report_url": report_url}
            for ref in refs if winners_by_ref[ref]]


_FILESTREAM = re.compile(r"""(?:href|src)=["']([^"']*[Ff]ile[Ss]tream\.ashx\?DocumentId=\d+[^"']*)""")
_MEETING = re.compile(r"""href=["']([^"']*Meeting\.aspx\?[^"']+)""")


def _absolute(url: str) -> str:
    if url.startswith("http"):
        return url
    return config.TRCA_ESCRIBE_BASE.rstrip("/") + "/" + url.lstrip("/")


def escribe_document_urls(html: str) -> list[str]:
    """Every FileStream + Meeting link on a page, absolute, order-preserving, deduped.

    HTML-decode each href before use: some detail pages encode the colon as `&#58;`
    (and `&` as `&amp;`), so a plain `.replace('&amp;', '&')` leaves `https&#58;//…` — a
    malformed scheme that crashes the fetch. `unescape` handles every entity (#137).
    """
    seen, out = set(), []
    for m in (_FILESTREAM.findall(html) + _MEETING.findall(html)):
        u = _absolute(unescape(m))
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def meeting_detail_urls(calendar_json: dict) -> list[str]:
    """Meeting detail-page URLs from a GetCalendarMeetings JSON response (#137).

    The eSCRIBE calendar is rendered client-side from this page-method, so the meeting
    IDs live here, not in the year landing page's markup (which is why the old static
    -anchor walk found zero). Only agenda'd meetings are followed — a meeting with no
    agenda has no report PDFs to index.
    """
    urls = []
    for meeting in calendar_json.get("d", []):
        mid = meeting.get("ID")
        if mid and meeting.get("HasAgenda"):
            urls.append(f"{config.TRCA_ESCRIBE_BASE}Meeting.aspx?Id={mid}"
                        f"&Agenda=Agenda&lang=English")
    return urls


def download_reports(conn, http, log=lambda _m: None) -> int:
    """POST the calendar page-method per year -> meeting pages -> FileStream PDFs. Resumable.

    Queue keys on sha256 IS NULL (#96): the hash records that we hold the bytes; text
    records whether pdftotext could read them. Never re-download for unreadable text. A
    year whose calendar call fails, or a single meeting page that 404s, is logged and
    skipped rather than aborting the run.
    """
    config.TRCA_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    # 1. Index: discover FileStream URLs and upsert a background_pdf row per document.
    for year in config.TRCA_ESCRIBE_YEARS:
        try:
            calendar = http.post_json(config.TRCA_CALENDAR_URL,
                                      json={"calendarStartDate": f"{year}-01-01",
                                            "calendarEndDate": f"{year}-12-31"})
        except httpx.HTTPError as exc:
            log(f"  trca {year}: calendar fetch failed: {exc}")
            continue
        detail_urls = meeting_detail_urls(calendar)
        log(f"  trca {year}: {len(detail_urls)} meetings")
        for murl in detail_urls:
            try:
                mhtml = http.get_text(murl)
            except httpx.HTTPError as exc:
                log(f"  trca skip meeting {murl}: {exc}")
                continue
            for durl in escribe_document_urls(mhtml):
                if "ashx" in durl.lower():
                    db.upsert_row(conn, BackgroundPdf(url=durl, kind="agency_board"),
                                  overwrite=False)
        conn.commit()
    # 2. Fetch: everything indexed but not yet held.
    return _store_pending_pdfs(conn, http, config.TRCA_REPORTS_DIR,
                               "%escribemeetings%", log, "trca")


def _store_pending_pdfs(conn, http, reports_dir, url_like: str, log, prefix: str) -> int:
    """Fetch every queued (sha256 IS NULL) agency_board PDF matching url_like. Resumable.

    Shared by the TRCA and Zoo download passes — the indexing differs (eSCRIBE walk vs.
    agenda parse), the fetch loop is identical. A single dead/404 URL is logged and
    SKIPPED (the row stays queued), never aborting the run: across hundreds of legdocs /
    eSCRIBE URLs a stray 404 is routine, and get_bytes re-raises 4xx — found live when one
    dead legdocs URL killed the whole Zoo body after storing 1 of 859 reports (#135).
    Queue keys on sha256 IS NULL (#96): the hash records we hold the bytes.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for row in conn.execute(
            "SELECT id, url FROM background_pdf WHERE kind='agency_board' "
            "AND url LIKE ? AND sha256 IS NULL ORDER BY id", (url_like,)).fetchall():
        try:
            blob = http.get_bytes(row["url"])
        except Exception as exc:            # noqa: BLE001 — 404/5xx/transport OR a malformed
            # URL (an entity that slipped decoding): one bad URL among thousands must never
            # abort the batch. Scoped to the single fetch call, so real bugs still surface.
            log(f"  {prefix} skip {row['url']}: {exc}")
            continue
        if not blob.startswith(b"%PDF"):
            continue                        # HTML error page; leave queued
        sha = hashlib.sha256(blob).hexdigest()
        path = reports_dir / f"{sha}.pdf"
        path.write_bytes(blob)
        conn.execute("UPDATE background_pdf SET sha256=?, local_path=?, text=? WHERE id=?",
                     (sha, str(path), _pdftotext(path), row["id"]))
        conn.commit()
        n += 1
        log(f"  {prefix} report {n}: {row['url']}")
    return n


def _pdftotext(path: pathlib.Path) -> str | None:
    try:
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                             capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", errors="replace") or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def store_trca_reports(conn, buyer_id: int) -> dict:
    """Parse every held agency_board report into agency_* rows. Offline, idempotent."""
    counts = {"solicitations": 0, "awards": 0, "bids": 0}
    for row in conn.execute("SELECT url, text FROM background_pdf "
                            "WHERE kind='agency_board' AND text IS NOT NULL "
                            "AND url LIKE '%escribemeetings%' ORDER BY url").fetchall():
        for item in parse_trca_report(row["text"], report_url=row["url"]):
            db.upsert_row(conn, AgencySolicitation(
                buyer_id=buyer_id, native_ref=item["native_ref"], title=item["title"],
                status="awarded" if item["winners"] else None,
                posted_date=None, closing_date=None, portal_url=None,
                source="trca_board"), overwrite=False)
            counts["solicitations"] += 1
            for winner, amount in item["winners"]:
                db.upsert_row(conn, AgencyAward(
                    buyer_id=buyer_id, native_ref=item["native_ref"],
                    supplier_name_raw=winner, award_amount=amount,
                    value_confidential=0, award_date=None,
                    report_url=item["report_url"], source="trca_board"), overwrite=True)
                counts["awards"] += 1
            for bidder in item["bidders"]:
                db.upsert_row(conn, AgencyBid(
                    buyer_id=buyer_id, native_ref=item["native_ref"],
                    bidder_name_raw=bidder, bid_price=None,
                    report_url=item["report_url"], source="trca_board"), overwrite=True)
                counts["bids"] += 1
    conn.commit()
    return counts
