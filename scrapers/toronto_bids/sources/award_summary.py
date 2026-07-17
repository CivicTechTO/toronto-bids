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

    `text` is ARCHIVAL here and nothing parses it (#116): the bids are read from the PDF's own
    cells by store_award_summary_bids. `layout=True` is kept because it is the more faithful
    rendering of a columnar form and the bytes are already on disk under it — not because
    anything depends on it.
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
# The form is a ruled table end to end — every field is a real (label, value) cell pair, and
# section 5 is a real bid table. So it is read as CELLS, via pdfplumber, not reconstructed
# from whitespace (#116). Measured over the 229 archived forms:
#
#     forms parsed:  pdftotext 144/229   pdfplumber 205/229
#     bids stored:   pdftotext 632       pdfplumber 1033
#
# 229 of 229 carry ruled tables and none are image-only, which is what separates this corpus
# from the staff reports #83 measured (ruled tables in only 13-20 of 229 — prose, not tables,
# so no extractor can invent the structure). The rule is not "pdfplumber is better"; it is
# "read cells where the PDF HAS cells".
#
# The three shapes that beat the whitespace parser, all of them structure pdftotext destroyed:
#
#     ['5. Bid Summary']
#     ['Supplier Name\n* indicates non-compliant Supplier', 'Bid Price (Excluding HST)\n...']
#     ['Range:']
#     ['1. 2489960 ONTARIO INC., O/A Kore Infrastructure', '$7,710,000.00']
#     ['3. The Stevens Company LTD*', '$ -']          # a non-price; the name cell is still a name
#     ['Dependable Truck and Tank Limited', '$652,700.00']            # no leading number at all
#
_DOC_NUMBER = re.compile(r"\bDoc\s*(\d{10})\b", re.I)
_COUNT = re.compile(r"(\d{1,3})")
# The City numbers most bidders and not all of them, so the numbering is stripped where present
# and never required. Requiring it cost 57 forms their entire bid table.
_NUMBERING = re.compile(r"^\s*\d{1,2}\s*[.)]\s*")
# Trailing compliance markers, plus the replacement char pdftotext and pdfplumber both emit for
# the form's non-Latin glyphs.
_NAME_MARKERS = re.compile(r"^[\s*^+†‡§�]+|[\s*^+†‡§�]+$")
_WS = re.compile(r"\s+")


def form_rows(path) -> list[list[str]]:
    """Every non-empty row of every ruled table in the form, as stripped cells. Does I/O."""
    import pdfplumber

    rows = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    cells = [(c or "").strip() for c in row]
                    if any(cells):
                        rows.append([c for c in cells if c])
    return rows


def _zip_cell(name_cell: str, price_cell: str) -> list[tuple[str, str | None]]:
    """Pair one row's bidder lines against its price lines.

    Usually a row is one bidder. A multi-package tender instead puts a whole column in a single
    cell, exactly as the BD agendas did (#94):

        ['26TW-CPI-17CWD (Package A):\nClean Water Works Inc.*\nAqua Tech...',
         '$3,551,718.88\n$3,978,656.19\n$5,381,389.24\n$5,668,197.16']

    Same rule as #94, and for the same reason: pairing is positional, so one stray line
    misattributes every bid after it. Zip the columns and REFUSE an unequal pair rather than
    guess. The package heading is dropped first — it ends in ':' and has no price beside it.

    **THE PRICE CELL'S LINE COUNT SAYS HOW MANY BIDS THE ROW HOLDS.** A newline inside a name
    cell is otherwise ambiguous, and guessing costs real bids: pdfplumber wraps a long name
    within its own cell, so

        ['2489960 Ontario Inc.\no/a Kore Infrastructure Group', '$3,198,000.00']

    is ONE bidder, and reading its two lines as two names against one price refused the pair
    and silently dropped a bidder from each of 4 forms. One price, one bid — join the name.
    """
    prices = [ln.strip() for ln in price_cell.split("\n") if ln.strip()]
    names = [ln.strip() for ln in name_cell.split("\n") if ln.strip()]
    names = [n for n in names if not n.endswith(":")]
    if len(prices) <= 1:
        name = " ".join(names)
        if not name:
            return []
        # An RFP publishes its proponents with NO price at all ("NOTE: Not applicable for
        # RFP"). #84 already stores those as bid_price NULL — requiring a price here dropped
        # every proponent on every scored RFP.
        return [(name, prices[0] if prices else None)]
    if len(names) != len(prices):
        return []
    return list(zip(names, prices))


def parse_award_summary(rows) -> dict | None:
    """{document_number, price_header, hst_basis, declared_bids, bids: [...]} or None.

    Pure: `rows` is what form_rows() read off the PDF, so this is testable against a JSON
    fixture without pdfplumber or a file — the same fetch/normalize split sources/base.py's
    Source protocol draws.
    """
    from toronto_bids.sources.bid_award_panel import _hst_basis

    doc = declared = price_header = None
    bids, in_section_5 = [], False
    for cells in rows:
        label = cells[0]
        value = cells[1] if len(cells) > 1 else ""
        if not in_section_5:
            # Section 5 is found by its own cell, not by a line anchor. `^\s*5\. Bid Summary$`
            # failed on 16 forms whose heading cell carries more than the heading.
            if label.startswith("5.") and "Bid Summary" in label:
                in_section_5 = True
            elif "Ariba Document No" in label and _DOC_NUMBER.search(value):
                doc = _DOC_NUMBER.search(value).group(1)
            elif "Number of Bids Received" in label and _COUNT.search(value):
                declared = int(_COUNT.search(value).group(1))
            continue
        if label.startswith("Supplier Name"):
            # The header cell carries the HST basis, load-bearing exactly as on the agendas
            # (#94): a price whose basis is unknown cannot be compared with one whose is known.
            # Its own cell also carries the footnote legend below it — take the first line.
            price_header = _WS.sub(" ", value.split("\n")[0]).strip() or None
            continue
        if label.lower().startswith(("range:", "note")):
            continue
        for name, price in _zip_cell(label, value):
            name = _NAME_MARKERS.sub("", _WS.sub(" ", _NUMBERING.sub("", name)).strip())
            if not name:
                continue          # an empty numbered row: the City leaves '5.' blank
            bids.append({"bidder_name_raw": name,
                         "bid_price": _WS.sub(" ", price).strip() if price else None})
    if not (doc and in_section_5):
        return None
    return {
        "document_number": normalize_document_number(doc),
        "price_header": price_header,
        "hst_basis": _hst_basis(price_header) if price_header else None,
        "declared_bids": declared,
        "bids": bids,
    }


def store_award_summary_bids(conn, log=lambda _m: None) -> int:
    """Parse every archived Award Summary Form into `bid` rows. Idempotent, offline.

    Reads the PDFs already on disk, not `background_pdf.text` — the form is a ruled table and
    its cells are the record (#116).

    `Number of Bids Received` is checked against what section 5 actually yields, and a
    mismatch REFUSES the form rather than storing a partial bid table. The Bid Award Panel
    corpus never offered that check — #94 had to infer its own ceiling from declared counts
    and could only guess at what it was dropping. Here the form states the answer, so a silent
    partial parse is a choice rather than an accident.
    """
    stored = refused = 0
    for row in conn.execute("SELECT document_number, local_path FROM background_pdf "
                            "WHERE kind='award_summary' AND local_path IS NOT NULL"):
        try:
            parsed = parse_award_summary(form_rows(row["local_path"]))
        except Exception as exc:
            log(f"    unreadable {row['document_number']}: {exc}")
            continue
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
