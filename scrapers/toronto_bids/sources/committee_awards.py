import csv
import io
import re

from toronto_bids import config

_DOC = re.compile(r"(?:Ariba\s+)?Doc(?:ument)?(?:\s*Number)?\s*[:#]?\s*(\d{10})", re.IGNORECASE)


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


def fetch_voting_records(http):
    """The per-term voting-record CSV bodies from CKAN (UUIDs resolved at runtime, never hardcoded)."""
    data = http.get_json(config.CKAN_BASE + "package_show",
                          params={"id": "members-of-toronto-city-council-voting-record"})
    csvs = []
    for r in data["result"]["resources"]:
        if r.get("format", "").upper() == "CSV" and r.get("name", "").startswith("member-voting-record-2"):
            csvs.append(http.get_text(r["url"]))
    return csvs
