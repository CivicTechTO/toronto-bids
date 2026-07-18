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

from toronto_bids import config
from toronto_bids.models import AgencyAward, AgencyBid, AgencySolicitation, BackgroundPdf
from toronto_bids.store import db

# 'RFP No. 10036307' / 'RFT No. 10039751, 10039753' — match the ref shape (8 digits),
# never the label vocabulary (call_number lesson: labels vary, shapes don't).
_REFS = re.compile(r"\bR[FQ][TPQ]\s*No\.?\s*((?:\d{8}(?:\s*,\s*)?)+)")
_REF = re.compile(r"\d{8}")
_TITLE = re.compile(r"^RE:\s*(.+?)(?=^\S|\Z)", re.M | re.S)
# One award clause: ref ... awarded to WINNER at a total cost not to exceed $AMOUNT.
_AWARD = re.compile(
    r"(?:RFT|RFP|RFQ|Contract)\s*No\.?\s*(\d{8})[^$]*?be\s+awarded\s+to\s+"
    r"(.+?)\s+at\s+a\s+total\s+cost\s+not\s+to\s+exceed\s+"
    r"(\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?)",
    re.S)
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


def parse_trca_report(text: str, report_url: str | None = None) -> list[dict]:
    refs_m = _REFS.search(text)
    if not refs_m:
        return []
    refs = _REF.findall(refs_m.group(1))
    title_m = _TITLE.search(text)
    title = _squash(title_m.group(1)) if title_m else None
    bidders = _bullet_names(text)

    winners_by_ref: dict[str, list] = {r: [] for r in refs}
    seen_amounts_by_ref: dict[str, set] = {r: set() for r in refs}
    for ref, winner, amount in _AWARD.findall(text):
        if ref in winners_by_ref:
            # RECOMMENDATION and RATIONALE both carry an award clause for the same
            # award, sometimes under different name strings (e.g. legal name vs.
            # trade name) — dedupe by amount, keeping the first (RECOMMENDATION,
            # which comes first in document order and carries the fuller legal name).
            if amount not in seen_amounts_by_ref[ref]:
                seen_amounts_by_ref[ref].add(amount)
                winners_by_ref[ref].append((_fix_quotes(_squash(winner)), amount))

    # VOR shape: several winners joined by 'and', no per-winner amounts.
    if not any(winners_by_ref.values()):
        vor = _VOR.search(text)
        if vor:
            names = re.split(r"\s+and\s+", _squash(vor.group(1)))
            for ref in refs:
                winners_by_ref[ref] = [(n.strip(), None) for n in names if n.strip()]

    return [{"native_ref": ref, "title": title, "winners": winners_by_ref[ref],
             "bidders": bidders, "report_url": report_url} for ref in refs]


_FILESTREAM = re.compile(r"""(?:href|src)=["']([^"']*[Ff]ile[Ss]tream\.ashx\?DocumentId=\d+[^"']*)""")
_MEETING = re.compile(r"""href=["']([^"']*Meeting\.aspx\?[^"']+)""")


def _absolute(url: str) -> str:
    if url.startswith("http"):
        return url
    return config.TRCA_ESCRIBE_BASE.rstrip("/") + "/" + url.lstrip("/").replace("&amp;", "&")


def escribe_document_urls(html: str) -> list[str]:
    """Every FileStream + Meeting link on a page, absolute, order-preserving, deduped."""
    seen, out = set(), []
    for m in (_FILESTREAM.findall(html) + _MEETING.findall(html)):
        u = _absolute(m.replace("&amp;", "&"))
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def download_reports(conn, http, log=lambda _m: None) -> int:
    """Walk eSCRIBE year pages -> meeting pages -> FileStream PDFs. Resumable.

    Queue keys on sha256 IS NULL (#96): the hash records that we hold the bytes; text
    records whether pdftotext could read them. Never re-download for unreadable text.
    """
    config.TRCA_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    # 1. Index: discover FileStream URLs and upsert a background_pdf row per document.
    for year in config.TRCA_ESCRIBE_YEARS:
        page = http.get_text(config.TRCA_ESCRIBE_BASE, params={"FillWidth": 1, "Year": year})
        meeting_urls = [u for u in escribe_document_urls(page) if "Meeting.aspx" in u]
        log(f"  trca {year}: {len(meeting_urls)} meetings")
        for murl in meeting_urls:
            mhtml = http.get_text(murl)
            for durl in escribe_document_urls(mhtml):
                if "ashx" in durl.lower():
                    db.upsert_row(conn, BackgroundPdf(url=durl, kind="agency_board"),
                                  overwrite=False)
        conn.commit()
    # 2. Fetch: everything indexed but not yet held.
    n = 0
    for row in conn.execute("SELECT id, url FROM background_pdf "
                            "WHERE kind='agency_board' AND url LIKE '%escribemeetings%' "
                            "AND sha256 IS NULL ORDER BY id").fetchall():
        blob = http.get_bytes(row["url"])
        if not blob.startswith(b"%PDF"):
            continue                       # HTML error page; leave queued
        sha = hashlib.sha256(blob).hexdigest()
        path = config.TRCA_REPORTS_DIR / f"{sha}.pdf"
        path.write_bytes(blob)
        text = _pdftotext(path)
        conn.execute("UPDATE background_pdf SET sha256=?, local_path=?, text=? WHERE id=?",
                     (sha, str(path), text, row["id"]))
        conn.commit()
        n += 1
        log(f"  trca report {n}: {row['url']}")
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
