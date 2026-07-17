"""Award Summary Forms — the losing bidders, after the Bid Award Panel was abolished (#114).

By-law 766-2025 eliminated the Bid Award Panel effective 2025-10-01, and its agendas were the
only published record of who *lost*. `sources/bid_award_panel.py` therefore stops dead at
2025.BA151 (2025-09-25) and will never find another agenda — 891 cached pages is the complete
and final corpus.

The bidders did not stop. Every awarded contract over $500,000 now carries an **Award Summary
Form** PDF on the Toronto Bids Portal, and it publishes more than the panel ever did:

    4. Solicitation Summary
       Number of Bids Received             Five (5)
    5. Bid Summary
       Supplier Name                                    Bid Price (Excluding HST)
       * indicates non-compliant Supplier
       1. 2489960 ONTARIO INC., O/A Kore Infrastructure  $7,710,000.00
       2. CRCE Construction Ltd.                         $8,624,203.50
       ...

No browser. The portal's table is driven by `feis_solicitation_published` — the same OData
spine sources/odata.py already reads — and `secure.toronto.ca` is not Akamai-gated the way
TMMIS is. The PDF hangs off the record itself in `uploadedFilesStaff`, so the whole path is
plain HTTP.

Two limits worth knowing before reading any number out of this table. The form only exists
**over $500,000** — the panel had no such floor, so the bid record thins permanently for small
awards. And the City says "a portion of work to create the notice of award records will be
manual", which shows: 223 of the 244 awards since the cutover carry one (91%), not all.
"""
import re
from urllib.parse import quote

from toronto_bids import config
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import BackgroundPdf, Bid
from toronto_bids.store import db

_DATA = ("https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/"
         "feis_solicitation_published")
_UPLOAD = "https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/"

# The portal's own filter, minus its date cut. The UI appends `Latest_Date_Awarded gt
# <today-18mo>` to honour "contracts are available to the public for 18 months" — that is
# enforced in the client, not the server. Without it the same endpoint serves 6,504 awarded
# records back to 2010-04-15. We omit it deliberately: the City could start enforcing it
# server-side at any time, and this archive exists for exactly that eventuality.
_AWARDED_FILTER = ("Ready_For_Posting eq 'Yes' and Solicitation_Form_Type eq 'Awarded "
                   "Contracts' and Awarded_Cancelled eq 'No'")
_PAGE = 500


def fetch_awarded_records(http, log=lambda _m: None) -> list:
    """Every awarded-contract record the portal's API will serve, paged. Plain HTTP."""
    out, skip = [], 0
    while True:
        page = http.get_json(
            f"{_DATA}?$format=application/json;odata.metadata=none&$count=true"
            f"&$skip={skip}&$top={_PAGE}&$filter={quote(_AWARDED_FILTER)}"
            f"&$orderby=Latest_Date_Awarded desc")
        rows = page.get("value") or []
        out.extend(rows)
        total = page.get("@odata.count", len(out))
        skip += _PAGE
        log(f"    {min(skip, total)}/{total}")
        if skip >= total or not rows:
            return out


def award_summary_files(record: dict) -> list:
    """(url, name) for each Award Summary Form on a record. Empty for awards under $500,000.

    The attachment rides on the record itself:

        "uploadedFilesStaff": [{"bin_id": "kSj1PnNq2nX0FApSenhvCA",
                                "name": "Doc5616191850 Award Summary Form.pdf", ...}]
    """
    out = []
    for f in record.get("uploadedFilesStaff") or []:
        bin_id = f.get("bin_id")
        if bin_id and "award summary" in str(f.get("name", "")).lower():
            out.append((_UPLOAD + bin_id, f.get("name")))
    return out


def download_award_summaries(conn, http, dest_dir=None, log=lambda _m: None) -> int:
    """Archive every Award Summary Form. Idempotent and resumable.

    Queues on `sha256 IS NULL`, not `text IS NULL` — the #83 lesson: a PDF pdftotext cannot
    read keeps a NULL text forever and would re-download on every run, in perpetuity. The hash
    records that we hold the bytes; the text records whether anything could read them.
    """
    from toronto_bids.sources.council import download_pdf

    dest_dir = dest_dir if dest_dir is not None else config.AWARD_SUMMARY_DIR
    have = {r["url"] for r in conn.execute(
        "SELECT url FROM background_pdf WHERE sha256 IS NOT NULL")}
    log("  award summary forms: querying the portal")
    records = fetch_awarded_records(http, log=log)
    wanted = [(url, name, rec) for rec in records
              for url, name in award_summary_files(rec) if url not in have]
    log(f"  award summary forms to fetch: {len(wanted)}")
    stored = 0
    for i, (url, _name, rec) in enumerate(wanted, 1):
        try:
            info = download_pdf(http, url, dest_dir, layout=True)
            db.upsert_row(conn, BackgroundPdf(
                url=url, kind="award_summary",
                # The council reference stays NULL: no council item exists for these. The
                # document number is the join, and it is the spine's own primary key.
                reference=None,
                document_number=normalize_document_number(
                    rec.get("Solicitation_Document_Number")),
                local_path=info["local_path"], sha256=info["sha256"], text=info["text"],
            ), overwrite=True)
            conn.commit()
            stored += 1
        except Exception as exc:
            conn.rollback()
            log(f"    skipped {url.rsplit('/', 1)[-1]}: {exc}")
        if i % 25 == 0:
            log(f"    {i}/{len(wanted)}")
    return stored


# --- parsing ------------------------------------------------------------------------------
#
# The form is a two-page PDF whose fields land as "Label    value" lines under pdftotext
# -layout. Section 5 is the bid table:
#
#     5. Bid Summary
#     Supplier Name                                       Bid Price (Excluding HST)
#     * indicates non-compliant Supplier                  NOTE: Not applicable for RFP
#     1. 2489960 ONTARIO INC., O/A Kore Infrastructure    $7,710,000.00
#     2. CRCE Construction Ltd.                           $8,624,203.50
_SECTION_5 = re.compile(r"^\s*5\.\s*Bid Summary\s*$", re.M)
# Section 5 is the last one, so it runs to the end of the document. Do NOT try to bound it by
# "the next numbered heading": the bidders are numbered too, and `^\d\. [A-Z]` matches
# "2. CRCE Construction Ltd." — which truncated the table at the second bidder and quietly
# produced a one-bid parse of a five-bid award. _BID_LINE is strict enough to ignore the page
# footer that follows.
_DOC_NUMBER = re.compile(r"Ariba Document No\.[^\n]*?\bDoc(\d{10})\b", re.I)
_N_BIDS = re.compile(r"Number of Bids Received\s+(?:([A-Za-z]+)\s*)?\(?(\d{1,3})\)?", re.I)
# "1. Acme Paving Inc.        $7,710,000.00"  /  "2. Beta Ltd.*    Non-Compliant"
#
# The price is OPTIONAL, and that is not tidiness. An RFP publishes its proponents with no
# price at all ("NOTE: Not applicable for RFP"), so requiring one silently dropped two of the
# three bidders on every scored RFP and left a one-bid parse of a three-bid award. The Bid
# Award Panel corpus had the same shape and #84 already stores those as bid_price NULL.
#
# The '$' can also sit a long way from its own digits — pdftotext -layout preserves the form's
# column, and the City right-aligns the number:
#     '1. ClaimsPro LP                    $                          30,460,650.00'
# [ \t] throughout, never \s: `\s` matches newlines, so `\d{1,2}[.)]\s*` walked straight off
# the end of an empty "4." row and captured the page footer on the next line as a bidder
# ("Page 2 of 2"). A bid is one line.
_BID_LINE = re.compile(
    r"^[ \t]*\d{1,2}[.)][ \t]*(?P<name>\S.*?)"
    r"(?:[ \t]{2,}(?P<price>\$?[ \t]*[\d,]+(?:\.\d{2})?|Non.?Compliant|No Bid|N/A))?[ \t]*$",
    re.I | re.M)
# A name is a firm, never a price that leaked out of its column when the optional price group
# declined to match.
#
# ONLY the '$'. A long digit run looks like a price and is not: numbered Ontario corporations
# are real bidders — '2489960 Ontario Inc.' won an $8.4M watermain contract and is the very
# example #87 pins a test on ("the numbering rule must not eat it"). Adding `\d[\d,]{5,}` here
# dropped it again and took 26 forms' bid tables down with it.
_NOT_A_NAME = re.compile(r"\$")
# The price column header carries the basis, exactly as the agendas' did (#94).
_PRICE_HEADER = re.compile(r"^\s*Supplier Name\s{2,}(?P<hdr>.*?)\s*$", re.M)
_NAME_MARKERS = re.compile(r"^[\s*^+†‡§]+|[\s*^+†‡§]+$")
_WS = re.compile(r"\s+")


def parse_award_summary(text: str) -> dict | None:
    """{document_number, price_header, hst_basis, declared_bids, bids: [...]} or None.

    Pure: `text` is the pdftotext output already stored in background_pdf.text.
    """
    from toronto_bids.sources.bid_award_panel import _hst_basis

    doc = _DOC_NUMBER.search(text or "")
    start = _SECTION_5.search(text or "")
    if not (doc and start):
        return None
    block = text[start.end():]

    header = _PRICE_HEADER.search(block)
    price_header = _WS.sub(" ", header.group("hdr")).strip() if header else None
    declared = _N_BIDS.search(text)
    bids = []
    for m in _BID_LINE.finditer(block):
        name = _NAME_MARKERS.sub("", _WS.sub(" ", m.group("name")).strip())
        if not name or name.lower().startswith(("note", "range", "supplier name")):
            continue
        if _NOT_A_NAME.search(name):
            continue          # the price column leaked into the name; refuse rather than store
        price = m.group("price")
        bids.append({"bidder_name_raw": name,
                     "bid_price": _WS.sub(" ", price).strip() if price else None})
    return {
        "document_number": normalize_document_number(doc.group(1)),
        "price_header": price_header,
        "hst_basis": _hst_basis(price_header) if price_header else None,
        "declared_bids": int(declared.group(2)) if declared else None,
        "bids": bids,
    }


def store_award_summary_bids(conn, log=lambda _m: None) -> int:
    """Parse every archived Award Summary Form into `bid` rows. Idempotent, offline.

    `Number of Bids Received` is checked against what section 5 actually yields, and a
    mismatch REFUSES the form rather than storing a partial bid table. The Bid Award Panel
    corpus never offered that check — #94 had to infer its own ceiling from declared counts
    and could only guess at what it was dropping. Here the form states the answer, so a silent
    partial parse is a choice rather than an accident.
    """
    stored = refused = 0
    for row in conn.execute("SELECT document_number, text FROM background_pdf "
                            "WHERE kind='award_summary' AND text IS NOT NULL"):
        parsed = parse_award_summary(row["text"])
        if not parsed or not parsed["bids"]:
            continue
        declared = parsed["declared_bids"]
        # Refuse only when we parsed FEWER rows than the form declares — that means the parse
        # lost bidders, and a partial bid table is worse than none.
        #
        # Parsing MORE is not a failure and must not be refused: "Number of Bids Received"
        # sometimes counts only the COMPLIANT bids while the table lists everyone. Doc
        # 5247418372 declares 2 and tabulates 3, the third marked '*' non-compliant at $0.00.
        # The table is the record; the count is a summary of part of it.
        if declared is not None and len(parsed["bids"]) < declared:
            log(f"    refused {parsed['document_number']}: form declares {declared} bids, "
                f"parsed {len(parsed['bids'])}")
            refused += 1
            continue
        for bid in parsed["bids"]:
            db.upsert_row(conn, Bid(
                reference=None,                       # no council item exists for these
                document_number=parsed["document_number"] or row["document_number"],
                bidder_name_raw=bid["bidder_name_raw"],
                bid_price=bid["bid_price"],
                hst_basis=parsed["hst_basis"],
                price_header=parsed["price_header"],
                source="award_summary",
            ), overwrite=True)
            stored += 1
    conn.commit()
    if refused:
        # Never silent: a refused form is a known gap, and one nobody prints reads as coverage.
        log(f"  award summary forms refused on a bid-count mismatch: {refused}")
    return stored
