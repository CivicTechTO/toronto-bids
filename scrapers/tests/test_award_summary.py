"""#114: the losing bidders, after the Bid Award Panel was abolished on 2025-10-01.

By-law 766-2025 eliminated the panel, so `bid_award_panel.py` stops dead at 2025.BA151 and
891 agendas is the final corpus. The bidders did not stop — they moved to an Award Summary
Form on the Toronto Bids Portal.

The form is a ruled table end to end, so it is read as cells (#116), and the fixtures are the
real pdfplumber output of two real forms. `parse_award_summary` takes those rows and does no
I/O, so every test here is offline and needs neither pdfplumber nor a PDF.
"""
import copy
import json
import pathlib

import pytest

from toronto_bids.models import BackgroundPdf, Bid
from toronto_bids.sources import award_summary
from toronto_bids.sources.award_summary import (
    award_summary_files, parse_award_summary, store_award_summary_bids)
from toronto_bids.store import db

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "award_summary"
FORM = json.loads((FIXTURES / "Doc5616191850.rows.json").read_text())
RFP = json.loads((FIXTURES / "Doc5386487782-rfp.rows.json").read_text())


# --- the bid table, which is the whole point ---------------------------------------------

def test_reads_every_bidder_off_a_real_form():
    """Doc5616191850, awarded 2026-06-23. Five bidders, one of whom won — the other four are
    what spec §2.5.2 calls unrecoverable and what the City stopped publishing via TMMIS."""
    p = parse_award_summary(FORM)
    assert [b["bidder_name_raw"] for b in p["bids"]] == [
        "2489960 ONTARIO INC., O/A Kore Infrastructure Group",
        "CRCE Construction Ltd.",
        "GIO CRETE CONSTRUCTION LTD.",
        "Aecon Utilities Inc.",
        "GIP Paving Inc.",
    ]
    assert [b["bid_price"] for b in p["bids"]][:2] == ["$7,710,000.00", "$8,624,203.50"]


def test_joins_the_spine_on_the_document_number():
    """No council reference exists for these — the panel that issued them is gone. The Ariba
    document number is the join, and it is the spine's own primary key."""
    assert parse_award_summary(FORM)["document_number"] == "5616191850"


def test_the_price_header_carries_the_hst_basis():
    """Load-bearing, exactly as on the agendas (#94): a price whose basis is unknown cannot be
    compared with one whose basis is known. The header's own cell also carries the footnote
    legend below it, so only the first line is the header."""
    p = parse_award_summary(FORM)
    assert p["price_header"] == "Bid Price (Excluding HST)"
    assert p["hst_basis"] == "excluding"


# --- the shapes that broke the whitespace parser (#116) -----------------------------------

def test_an_rfp_lists_proponents_with_no_price_at_all():
    """'NOTE: Not applicable for RFP'. Requiring a price dropped two of these three bidders
    and left a one-bid parse of a three-bid award. #84 already stores priceless bids."""
    p = parse_award_summary(RFP)
    assert p["declared_bids"] == 3
    assert [b["bidder_name_raw"] for b in p["bids"]] == [
        "ClaimsPro LP", "Sedgwick Canada Inc.", "DSB Claims Solutions Inc."]
    assert [b["bid_price"] for b in p["bids"]][1:] == [None, None]


def test_a_numbered_ontario_corporation_is_a_real_bidder():
    """The #87 lesson. '2489960 Ontario Inc.' won an $8.4M watermain contract. A guard that
    treats a long digit run as a leaked price eats it — and took 26 forms' bid tables down
    before it was caught. Reading cells removes the reason such a guard ever existed: a name
    cell cannot contain a price that leaked out of the price cell."""
    p = parse_award_summary(FORM)
    assert p["bids"][0]["bidder_name_raw"].startswith("2489960 ONTARIO INC.")


def test_an_unnumbered_bidder_is_still_a_bidder():
    """The City numbers most bidders and not all of them. Requiring the numbering cost 57 of
    229 forms their entire bid table — they parsed to zero bids and were silently skipped."""
    rows = [["Ariba Document No. (Ex. DocXXXXXXXXXX)", "Doc1234567890"],
            ["Number of Bids Received", "1"],
            ["5. Bid Summary"],
            ["Supplier Name\n* indicates non-compliant Supplier", "Bid Price (Excluding HST)"],
            ["Dependable Truck and Tank Limited", "$652,700.00"]]
    p = parse_award_summary(rows)
    assert p["bids"] == [{"bidder_name_raw": "Dependable Truck and Tank Limited",
                          "bid_price": "$652,700.00"}]


def test_a_dash_is_not_a_price_and_does_not_cost_the_bidder():
    """'3. The Stevens Company LTD*   $ -'. Under pdftotext the price group declined to match,
    the '$' fell into the name, and a leaked-price guard then dropped the whole row — turning a
    3-bid form into a refused one. The cell boundary settles it: the name cell is the name."""
    rows = [["Ariba Document No.", "Doc1234567890"],
            ["5. Bid Summary"],
            ["3. The Stevens Company LTD*", "$ -"]]
    assert parse_award_summary(rows)["bids"] == [
        {"bidder_name_raw": "The Stevens Company LTD", "bid_price": "$ -"}]


def test_a_heading_cell_carrying_more_than_the_heading_still_starts_section_5():
    """`^\\s*5\\. Bid Summary$` anchored to a whole line and 16 forms' headings are not one."""
    rows = [["Ariba Document No.", "Doc1234567890"],
            ["5. Bid Summary (complete for all solicitations)"],
            ["1. Acme Paving Inc.", "$100.00"]]
    assert len(parse_award_summary(rows)["bids"]) == 1


def test_an_empty_numbered_row_is_not_a_bidder():
    """The City leaves a trailing '4.' on the form. Under pdftotext `\\d{1,2}[.)]\\s*` walked
    off the end of that row — `\\s` matches newlines — and captured the page footer on the next
    line, inventing 88 'Page 2 of 2' bidders. A cell cannot walk into the next row."""
    assert [b["bidder_name_raw"] for b in parse_award_summary(RFP)["bids"]][-1] == \
        "DSB Claims Solutions Inc."


def test_a_multi_package_row_zips_its_columns():
    """A multi-package tender puts a whole column in one cell, exactly as the BD agendas did
    (#94). The package heading has no price beside it and is dropped."""
    rows = [["Ariba Document No.", "Doc1234567890"],
            ["5. Bid Summary"],
            ["26TW-CPI-17CWD (Package A):\nClean Water Works Inc.*\nAqua Tech Ltd.",
             "$3,551,718.88\n$3,978,656.19"]]
    assert parse_award_summary(rows)["bids"] == [
        {"bidder_name_raw": "Clean Water Works Inc.", "bid_price": "$3,551,718.88"},
        {"bidder_name_raw": "Aqua Tech Ltd.", "bid_price": "$3,978,656.19"},
    ]


def test_a_name_wrapped_inside_its_own_cell_is_one_bidder_not_two():
    """The price cell's line count says how many bids the row holds. pdfplumber wraps a long
    name within its own cell, so a two-line name beside ONE price is one bidder — reading it
    as two names against one price made the pair unequal, which the rule above then refused,
    silently dropping a bidder from each of 4 forms."""
    rows = [["Ariba Document No.", "Doc1234567890"],
            ["5. Bid Summary"],
            ["2489960 Ontario Inc.\no/a Kore Infrastructure Group", "$3,198,000.00"]]
    assert parse_award_summary(rows)["bids"] == [
        {"bidder_name_raw": "2489960 Ontario Inc. o/a Kore Infrastructure Group",
         "bid_price": "$3,198,000.00"}]


def test_an_unequal_multi_package_row_is_refused_not_guessed():
    """#94's rule, and for its reason: pairing is positional, so one stray line misattributes
    every bid after it. A wrong bid is worse than a missing one."""
    rows = [["Ariba Document No.", "Doc1234567890"],
            ["5. Bid Summary"],
            ["Clean Water Works Inc.\nAqua Tech Ltd.\nThird Firm Inc.",
             "$3,551,718.88\n$3,978,656.19"]]
    assert parse_award_summary(rows)["bids"] == []


def test_a_form_with_no_bid_section_yields_none():
    assert parse_award_summary([["Ariba Document No.", "Doc1234567890"],
                                ["1. Award Details", "Please fill out below:"]]) is None
    assert parse_award_summary([]) is None


# --- the count check ----------------------------------------------------------------------

def _seed(conn, rows, monkeypatch, doc="5616191850"):
    """Seed one archived form. `form_rows` is the I/O seam, so the fixture stands in for it
    and the test needs no PDF on disk."""
    path = f"/nonexistent/{doc}.pdf"
    db.upsert_row(conn, BackgroundPdf(
        url=f"https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/{doc}",
        kind="award_summary", document_number=doc, local_path=path), overwrite=True)
    conn.commit()
    monkeypatch.setattr(award_summary, "form_rows", lambda _p: rows)


def test_stores_the_bids_a_real_form_publishes(conn, monkeypatch):
    _seed(conn, FORM, monkeypatch)
    assert store_award_summary_bids(conn) == 5
    rows = conn.execute("SELECT bidder_name_raw, bid_price_numeric, hst_basis, reference, "
                        "document_number, source FROM bid ORDER BY bid_price_numeric").fetchall()
    assert rows[0]["bid_price_numeric"] == pytest.approx(7710000.00)
    assert rows[0]["hst_basis"] == "excluding"
    assert rows[0]["reference"] is None            # no council item exists for these
    assert rows[0]["document_number"] == "5616191850"
    assert rows[0]["source"] == "award_summary"


def test_under_parsing_refuses_the_form_rather_than_storing_a_partial_table(conn, monkeypatch):
    """The check the agenda corpus never offered: the form states its own bid count, so a
    partial parse is detectable rather than silent."""
    rows = [r for r in copy.deepcopy(FORM)
            if not r[0].startswith(("2. CRCE", "3. GIO CRETE"))]
    _seed(conn, rows, monkeypatch)
    assert store_award_summary_bids(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0] == 0


def test_parsing_more_than_declared_is_kept_not_refused(conn, monkeypatch):
    """'Number of Bids Received' sometimes counts only the COMPLIANT bids while the table
    lists everyone — doc 5247418372 declares 2 and tabulates 3, the third marked '*' at
    $0.00. The table is the record; the count is a summary of part of it."""
    rows = copy.deepcopy(FORM)
    for r in rows:
        if r[0].startswith("Number of Bids Received"):
            r[1] = "4"
    _seed(conn, rows, monkeypatch)
    assert store_award_summary_bids(conn) == 5


def test_is_idempotent(conn, monkeypatch):
    _seed(conn, FORM, monkeypatch)
    assert store_award_summary_bids(conn) == 5
    store_award_summary_bids(conn)
    assert conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0] == 5


def test_a_priceless_rfp_bid_does_not_duplicate_on_every_run(conn, monkeypatch):
    """SQLite treats NULLs as distinct in a UNIQUE index — the #73/#84 trap. bid_key COALESCEs
    both identifiers AND the price, so these survive a re-run."""
    _seed(conn, RFP, monkeypatch, doc="5386487782")
    first = store_award_summary_bids(conn)
    store_award_summary_bids(conn)
    assert conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0] == first


def test_an_unreadable_form_is_reported_and_skipped(conn, monkeypatch):
    """16 of the composite reports are image-only (#96) and this corpus may yet grow one. A
    form pdfplumber cannot open must not take the whole pass down with it."""
    def boom(_p):
        raise OSError("not a PDF")
    _seed(conn, FORM, monkeypatch)
    monkeypatch.setattr(award_summary, "form_rows", boom)
    said = []
    assert store_award_summary_bids(conn, log=said.append) == 0
    assert any("unreadable" in m for m in said)


def test_forms_are_read_from_the_current_data_dir_not_a_stale_absolute_path(conn, monkeypatch, tmp_path):
    """local_path is absolute and baked in at download time on whatever machine fetched the
    form. The archive is designed to migrate — server becomes primary — so a path from the old
    machine will not resolve on the new one. The file's identity is its basename (the portal
    bin_id) and its home is the current data dir, so it must be resolved there. Found live: a
    migrated DB logged `unreadable` for all 229 forms and parsed zero bids."""
    from toronto_bids import config
    monkeypatch.setattr(config, "AWARD_SUMMARY_DIR", tmp_path / "documents" / "award_summary")
    seen = {}
    def fake_form_rows(path):
        seen["path"] = str(path)
        return FORM
    monkeypatch.setattr(award_summary, "form_rows", fake_form_rows)
    db.upsert_row(conn, BackgroundPdf(
        url="https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/kSj1PnNq2nX0FApSenhvCA",
        kind="award_summary", document_number="5616191850",
        local_path="/Users/someone-else/scrapers/files/documents/award_summary/kSj1PnNq2nX0FApSenhvCA",
    ), overwrite=True)
    conn.commit()
    store_award_summary_bids(conn)
    assert seen["path"] == str(tmp_path / "documents" / "award_summary" / "kSj1PnNq2nX0FApSenhvCA")


def test_a_bid_award_panel_bid_and_an_award_summary_bid_coexist(conn, monkeypatch):
    """Both write `bid`, and neither identifier is required: a panel bid has a council item
    and (pre-2019) no document number; an award-summary bid is the reverse."""
    db.upsert_row(conn, Bid(reference="2022.BA189.1", bidder_name_raw="Hatch Ltd.",
                            source="bid_award_panel"), overwrite=True)
    _seed(conn, FORM, monkeypatch)
    store_award_summary_bids(conn)
    n = conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0]
    assert n == 6
    assert conn.execute("SELECT COUNT(*) FROM bid WHERE reference IS NULL").fetchone()[0] == 5


# --- the attachment --------------------------------------------------------------------

def test_finds_the_award_summary_form_on_a_record():
    rec = {"uploadedFilesStaff": [
        {"bin_id": "kSj1PnNq2nX0FApSenhvCA", "name": "Doc5616191850 Award Summary Form.pdf"}]}
    assert award_summary_files(rec) == [
        ("https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/kSj1PnNq2nX0FApSenhvCA",
         "Doc5616191850 Award Summary Form.pdf")]


def test_an_award_under_500k_carries_no_form():
    """The form only exists over $500,000 — the panel had no such floor, so the bid record
    thins permanently for small awards. An empty list is the normal case, not an error."""
    assert award_summary_files({"uploadedFilesStaff": []}) == []
    assert award_summary_files({}) == []


def test_other_attachments_are_not_mistaken_for_the_form():
    rec = {"uploadedFilesStaff": [{"bin_id": "x", "name": "Doc123 Addendum 1.pdf"}]}
    assert award_summary_files(rec) == []
