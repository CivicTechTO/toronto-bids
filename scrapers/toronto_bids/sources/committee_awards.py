import csv
import io
import re

from toronto_bids import config
from toronto_bids.amount import parse_bid_price

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
