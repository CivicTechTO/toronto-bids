import csv
import hashlib
import io
import re
import subprocess

from toronto_bids import config
from toronto_bids.amount import parse_bid_price
from toronto_bids.models import BackgroundPdf, Bid
from toronto_bids.store import db

COMMITTEE_REPORTS_DIR = config.DATA_DIR / "documents" / "committee_award"

_DOC = re.compile(r"(?:Ariba\s+)?Doc(?:ument)?(?:\s*Number)?\s*[:#]?\s*(\d{10})", re.IGNORECASE)

# Both report formats lay a bid row out as "<name>   <$price>" on one pdftotext -layout
# line, columns separated by the layout's wide gap (2+ spaces) -- never a single space,
# which would also fire on a name that merely contains a numeral. The price is kept
# verbatim (footnote marker and all); only the name's trailing '*' is stripped.
_BID_ROW = re.compile(r"^\s*(?P<name>\S.*?)\s{2,}(?P<price>\$[\d,]+(?:\.\d+)?\*?)\s*$")

# Either table anchor a committee award report may carry. Refuse (return []) when neither
# is present -- an emergency/sole-source/confidential-attachment report has no bid table
# to invent one from (#130 discipline).
_BID_TABLE_ANCHORS = ("Summary of Bids Received", "opened the following bids")


def award_doc_number(title):
    """The 10-digit document number an award item names, else None. Match the number, not the
    vocabulary -- titles say 'Doc<n>', 'Document Number <n>', 'Ariba Document Number <n>'."""
    if not title:
        return None
    m = _DOC.search(title)
    return m.group(1) if m else None


def award_items_from_voting_record(csv_text):
    """Distinct award items carrying a document number, from one voting-record CSV.
    Returns [{document_number, reference, committee, title}]. Dedups the per-member vote rows."""
    out = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        title = row.get("Agenda Item Title") or ""
        doc = award_doc_number(title)
        if not doc:
            continue
        out.setdefault(doc, {"document_number": doc, "reference": row.get("Agenda Item #"),
                             "committee": row.get("Committee"), "title": title})
    return list(out.values())


def parse_committee_bids(text):
    """The bidder/price table from a committee award staff report, as [(bidder, bid_price)].

    Handles both report shapes: the RFQ/RFP 'Summary of Bids Received' table and the RFT
    'opened the following bids' table. Both lay each bid out as one 'name ... $price' line
    once pdftotext -layout has run; the header/prose lines around the table carry no '$' and
    are skipped, never mistaken for a row. Returns [] when neither anchor is present --
    refuse rather than invent a table (#130).
    """
    anchor = None
    for candidate in _BID_TABLE_ANCHORS:
        idx = text.find(candidate)
        if idx != -1 and (anchor is None or idx < anchor):
            anchor = idx
    if anchor is None:
        return []

    bids = []
    started = False
    for line in text[anchor:].splitlines():
        if not line.strip():
            continue
        m = _BID_ROW.match(line)
        price = m.group("price") if m else None
        if m is None or parse_bid_price(price) is None:
            if started:
                break  # first non-matching line after the table started: table is over
            continue  # still walking through the header/caption lines before row 1
        name = m.group("name").rstrip("*").strip()
        bids.append((name, price))
        started = True
    return bids


def fetch_voting_records(http):
    """The per-term voting-record CSV bodies from CKAN (UUIDs resolved at runtime, never hardcoded)."""
    data = http.get_json(config.CKAN_BASE + "package_show",
                          params={"id": "members-of-toronto-city-council-voting-record"})
    csvs = []
    for r in data["result"]["resources"]:
        if r.get("format", "").upper() == "CSV" and r.get("name", "").startswith("member-voting-record-2"):
            csvs.append(http.get_text(r["url"]))
    return csvs


def _pdftotext(path) -> str | None:
    try:
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                             capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", errors="replace") or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def download_committee_reports(conn, http, url_map: dict, log=lambda _m: None) -> int:
    """Fetch each committee award staff report PDF (plain HTTP) and store it.

    `url_map` is {url: document_number} — the award items already discovered from a voting
    record (award_items_from_voting_record) name both. Mirrors trca_board's resilient fetch
    loop (#135): a single dead URL is logged and skipped, never aborting the batch; a body
    that isn't actually a PDF (an HTML error page) is left unqueued rather than stored as
    garbage. Queues/re-fetches only what isn't already held (sha256 IS NULL), keyed on url.
    """
    COMMITTEE_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    have = {r["url"] for r in conn.execute(
        "SELECT url FROM background_pdf WHERE kind='committee_award' AND sha256 IS NOT NULL")}
    n = 0
    for url, document_number in url_map.items():
        if url in have:
            continue
        try:
            blob = http.get_bytes(url)
        except Exception as exc:            # noqa: BLE001 — one dead URL must never abort the batch
            log(f"  committee skip {url}: {exc}")
            continue
        if not blob.startswith(b"%PDF"):
            continue                        # HTML error page, not a report
        sha = hashlib.sha256(blob).hexdigest()
        path = COMMITTEE_REPORTS_DIR / f"{sha}.pdf"
        path.write_bytes(blob)
        db.upsert_row(conn, BackgroundPdf(
            url=url, document_number=document_number, kind="committee_award",
            local_path=str(path), sha256=sha, text=_pdftotext(path),
        ), overwrite=True)
        conn.commit()
        n += 1
        log(f"  committee report {n}: {url}")
    return n


def store_committee_bids(conn, log=lambda _m: None) -> int:
    """Parse every held committee award report's text and attach its bids by document_number.

    Mirrors #114's award-summary bid attachment: reference=None, document_number=<report's>.
    A report whose parse returns [] (no bid table -- refused, not invented) stores nothing:
    an honest zero, not a missing row. Idempotent via db.upsert_row on bid_key.
    """
    n = 0
    for row in conn.execute(
            "SELECT document_number, text FROM background_pdf "
            "WHERE kind='committee_award' AND text IS NOT NULL AND document_number IS NOT NULL"):
        bids = parse_committee_bids(row["text"])
        for bidder_name_raw, bid_price in bids:
            db.upsert_row(conn, Bid(
                bidder_name_raw=bidder_name_raw,
                reference=None,
                document_number=row["document_number"],
                bid_price=bid_price,
                source="committee_award",
            ), overwrite=True)
            n += 1
        conn.commit()
        log(f"  committee bids: {len(bids)} from doc {row['document_number']}")
    return n
