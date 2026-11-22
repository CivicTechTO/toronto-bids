"""Tests for Toronto Police Service Board (TPSB) public minutes capture, slicing, and extraction (#334)."""

import json
import httpx
import pytest

from toronto_bids import agency_coverage, cli, config
from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking.agency_reference import normalize_agency_reference
from toronto_bids.sources import tpsb_board
from toronto_bids.store import db

_ROOT_MEETINGS_HTML = """
<html><body>
  <a href="https://tpsb.ca/wp-content/uploads/2026/07/Public-Meeting-Minutes-May-14-2026.pdf">Read Minutes</a>
  <a href="https://tpsb.ca/wp-content/uploads/2026/07/PUBLIC-Minutes-Audit-Risk-Management-Committee-July-13-2026.pdf">Read Minutes</a>
  <a href="https://tpsb.ca/wp-content/uploads/2026/05/Public-Agenda-May-14-2026.pdf">Read Agenda</a>
  <a href="https://tpsb.ca/home/meeting-archive-2025/">2025 Archive</a>
  <a href="https://tpsb.ca/home/current-and-past-meetings/past-meetings-2020/">2020 Archive</a>
  <a href="https://tpsb.ca/home/current-and-past-meetings/past-meetings-2014/">2014 Archive</a>
</body></html>
"""

_ARCHIVE_2025_HTML = """
<html><body>
  <a href="https://tpsb.ca/wp-content/uploads/2026/04/Public-Minutes-December-10-2025.pdf">Read the Minutes</a>
  <a href="https://tpsb.ca/wp-content/uploads/download-manager-files/37public_mibutes_-_june_12_2025.pdf">Read the Minutes</a>
  <a href="https://tpsb.ca/wp-content/uploads/2025/11/BUDGET_COMMITTEE_MINUTES_NOVEMBER_27.pdf">Read the Minutes</a>
  <a href="https://tpsb.ca/wp-content/uploads/2025/12/Public-Agenda-December-10-2025.pdf">Read Agenda</a>
</body></html>
"""

_ARCHIVE_2020_HTML = """
<html><body>
  <a href="https://tpsb.ca/wp-content/uploads/download-manager-files/e9public_minutes_dec15.pdf">Read the Minutes</a>
</body></html>
"""

_SAMPLE_MINUTES_TEXT = (
    "Minutes of the Public Meeting of the Toronto Police Service Board\n"
    "held on December 10, 2025\n"
    "\x0c"
    "1. Contract Award to Rampart International Corp.\n"
    "\x0c"
    "PUBLIC REPORT\n"
    "To: Chair and Members\n"
    "Subject: Contract Award to Rampart International Corp. for 9mm Glock Pistols\n"
    "Purpose: Seeking Decision\n"
    "Recommendation: approve award of $10.54M.\n"
    "\x0c"
    "Table 1: Cost Breakdown...\n"
    "\x0c"
    "2. Special Constable Re-Appointments\n"
    "\x0c"
    "PUBLIC REPORT\n"
    "Subject: Special Constable Re-Appointments - December 2025\n"
    "Purpose: Seeking Decision\n"
    "Unrelated appointment report.\n"
    "\x0c"
    "3. Annual Report of Non-Competitive Purchases for 2025\n"
    "\x0c"
    "PUBLIC REPORT\n"
    "Subject: Annual Report of Non-Competitive Purchases for 2025\n"
    "Purpose: Information Purposes Only\n"
    "Appendix A: Axon Public Safety Canada Inc. $12,379,227\n"
    "\x0c"
    "Appendix B: Single Source Purchases\n"
)


def test_parse_archive_urls_respects_min_year():
    archives = tpsb_board.parse_archive_urls(_ROOT_MEETINGS_HTML, min_year=2015)
    assert archives == [
        {"year": 2020, "url": "https://tpsb.ca/home/current-and-past-meetings/past-meetings-2020/"},
        {"year": 2025, "url": "https://tpsb.ca/home/meeting-archive-2025/"},
    ]


def test_parse_minutes_pdfs_includes_mibutes_typo_and_excludes_agendas_and_subcommittees():
    pdfs = tpsb_board.parse_minutes_pdfs(_ARCHIVE_2025_HTML)
    assert [p["url"].rsplit("/", 1)[-1] for p in pdfs] == [
        "Public-Minutes-December-10-2025.pdf",
        "37public_mibutes_-_june_12_2025.pdf",
    ]
    root_pdfs = tpsb_board.parse_minutes_pdfs(_ROOT_MEETINGS_HTML)
    assert [p["url"].rsplit("/", 1)[-1] for p in root_pdfs] == [
        "Public-Meeting-Minutes-May-14-2026.pdf",
    ]


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("Contract Award to Rampart International Corp. for 9mm Glock Pistols", True),
        ("Contract Increase with Paladin Technologies Inc. for Audio Visual", True),
        ("Contract Extension and Increase with D&R Electronics", True),
        ("Pre-Qualification of Vendors for Minor Building Repairs", True),
        ("Annual Report of Non-Competitive Purchases for 2025", True),
        ("Annual Report of Co-operative, Joint and Consolidated Procurements for 2025", True),
        ("Collision Reporting Centre Contract", True),
        ("Special Constable Re-Appointments – December 2025", False),
        ("Annual Report: April 1, 2025 to March 31, 2026 – Grant Applications and Contracts", False),
        ("Request for Funds: Youth in Policing Luncheon and Awards", False),
        ("Toronto Police Service 2025 Annual Hate Crime Statistical Report", False),
    ],
)
def test_is_procurement_subject(subject, expected):
    assert tpsb_board.is_procurement_subject(subject) is expected


def test_slice_procurement_reports_isolates_reports_and_trims_dividers():
    slices = tpsb_board.slice_procurement_reports(_SAMPLE_MINUTES_TEXT)
    assert len(slices) == 2
    assert slices[0]["start_page"] == 3
    assert slices[0]["end_page"] == 4
    assert "Rampart International Corp." in slices[0]["subject"]
    assert "December 10, 2025" in slices[0]["text"]
    assert "Special Constable" not in slices[0]["text"]
    assert "Table 1: Cost Breakdown" in slices[0]["text"]

    assert slices[1]["start_page"] == 8
    assert slices[1]["end_page"] == 9
    assert "Non-Competitive Purchases" in slices[1]["subject"]
    assert "Appendix B: Single Source Purchases" in slices[1]["text"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10001638-26", "10001638-26"),
        ("RFP 10001630 - 26", "10001630-26"),
        ("10000808 \u2013 26", "10000808-26"),
        ("10000808 â\x80\x93 26", "10000808-26"),
        ("Sole Source", None),
        ("Direct Award", None),
    ],
)
def test_tps_reference_normalization(raw, expected):
    assert normalize_agency_reference("toronto-police-service", raw) == expected


def test_tps_ambiguous_reference_raises():
    with pytest.raises(ValueError, match="ambiguous TPS reference"):
        normalize_agency_reference("toronto-police-service", "10001638-26 and 10001985-26")


def test_download_tpsb_reports_slices_marks_base_pdf_and_skips_on_rerun(
    conn, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "TPSB_REPORTS_DIR", tmp_path / "tpsb")
    monkeypatch.setattr(config, "TPSB_MEETINGS_DIR", tmp_path / "tpsb" / "meetings")

    may_pdf = "https://tpsb.ca/wp-content/uploads/2026/07/Public-Meeting-Minutes-May-14-2026.pdf"
    dec_pdf = "https://tpsb.ca/wp-content/uploads/2026/04/Public-Minutes-December-10-2025.pdf"
    jun_pdf = "https://tpsb.ca/wp-content/uploads/download-manager-files/37public_mibutes_-_june_12_2025.pdf"
    y20_pdf = "https://tpsb.ca/wp-content/uploads/download-manager-files/e9public_minutes_dec15.pdf"

    monkeypatch.setattr(
        tpsb_board,
        "_pdftotext",
        lambda path: _SAMPLE_MINUTES_TEXT if "dec10" in path.read_bytes().decode("latin1") else "Minutes\x0cSubject: Receipt of Donation\nPurpose: Info\n",
    )

    fetched = []

    class FakeHttp:
        def get_text(self, url):
            fetched.append(url)
            if url == config.TPSB_MEETINGS_URL:
                return _ROOT_MEETINGS_HTML
            if url.endswith("/meeting-archive-2025/"):
                return _ARCHIVE_2025_HTML
            if url.endswith("/past-meetings-2020/"):
                return _ARCHIVE_2020_HTML
            raise AssertionError(f"unexpected URL {url}")

        def get_bytes(self, url):
            fetched.append(url)
            if url == jun_pdf:
                raise httpx.ConnectError("transient timeout")
            if url == dec_pdf:
                return b"%PDF-1.4\n%dec10\n"
            return b"%PDF-1.4\n%other\n" + url.encode()

    failures = []
    n = tpsb_board.download_tpsb_reports(conn, FakeHttp(), failures=failures, current_year=2026)
    assert n == 2
    assert len(failures) == 1
    assert failures[0][0] == "download:tpsb"
    assert jun_pdf in failures[0][1]

    # Base marker rows have text=None; sliced report rows have #page=N and non-empty text.
    rows = conn.execute(
        "SELECT url, sha256, text FROM background_pdf WHERE url LIKE '%tpsb.ca%' ORDER BY url"
    ).fetchall()
    by_url = {r["url"]: r for r in rows}
    assert by_url[dec_pdf]["text"] is None and by_url[dec_pdf]["sha256"]
    assert by_url[may_pdf]["text"] is None and by_url[may_pdf]["sha256"]
    assert by_url[y20_pdf]["text"] is None and by_url[y20_pdf]["sha256"]
    assert f"{dec_pdf}#page=3" in by_url and "Rampart" in by_url[f"{dec_pdf}#page=3"]["text"]
    assert f"{dec_pdf}#page=8" in by_url and "Non-Competitive" in by_url[f"{dec_pdf}#page=8"]["text"]

    # Second run skips already-held Minutes PDFs and reuses cached 2020 archive HTML.
    fetched.clear()
    n2 = tpsb_board.download_tpsb_reports(conn, FakeHttp(), failures=[], current_year=2026)
    assert n2 == 0
    assert dec_pdf not in fetched
    assert may_pdf not in fetched
    assert y20_pdf not in fetched
    assert "https://tpsb.ca/home/current-and-past-meetings/past-meetings-2020/" not in fetched
    assert jun_pdf in fetched  # previously failed PDF is retried


def test_tpsb_backfill_accepts_rfx_and_standalone_extension_page_fallback(conn, monkeypatch):
    seed_buyers(conn)
    rfx_url = "https://tpsb.ca/wp-content/uploads/2026/04/Public-Minutes-December-10-2025.pdf#page=3"
    ext_url = "https://tpsb.ca/wp-content/uploads/2026/04/Public-Minutes-December-10-2025.pdf#page=42"

    ext_rfx = {
        "contracts": [
            {
                "reference": "RFP 10001638 - 26",
                "solicitation_type": "RFP",
                "title": "9mm Glock Pistols",
                "decision_date": "2025-12-10",
                "bids": [],
                "awards": [
                    {
                        "supplier_name": "Rampart International Corp.",
                        "amount_raw": "$10.54 Million",
                        "amount_basis": "plus_HST",
                        "value_confidential": False,
                    }
                ],
            }
        ]
    }
    ext_sole = {
        "contracts": [
            {
                "reference": "Sole Source",
                "solicitation_type": "sole_source",
                "title": "Audio Visual Hardware Contract Increase",
                "decision_date": "2025-12-10",
                "bids": [],
                "awards": [
                    {
                        "supplier_name": "Paladin Technologies Inc.",
                        "amount_raw": "$5.83 Million",
                        "amount_basis": "plus_HST",
                        "value_confidential": False,
                    }
                ],
            }
        ]
    }

    conn.execute(
        "INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)",
        (rfx_url, "agency_board", "tpsbsha1", "held slice 1"),
    )
    conn.execute(
        "INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)",
        (ext_url, "agency_board", "tpsbsha2", "held slice 2"),
    )
    db.mark_extracted(conn, "tpsbsha1", EXTRACTOR_VERSION, json.dumps(ext_rfx))
    db.mark_extracted(conn, "tpsbsha2", EXTRACTOR_VERSION, json.dumps(ext_sole))
    conn.commit()

    result = backfill_from_extraction(conn, "tpsb")
    assert result["awards_written"] == 2
    assert result["contracts_refused"] == 0

    awards = conn.execute(
        "SELECT native_ref, supplier_name_raw, award_amount, award_date, source "
        "FROM agency_award WHERE source='tpsb_board' ORDER BY native_ref"
    ).fetchall()
    assert [tuple(r) for r in awards] == [
        ("10001638-26", "Rampart International Corp.", "$10.54 Million", "2025-12-10", "tpsb_board"),
        (ext_url, "Paladin Technologies Inc.", "$5.83 Million", "2025-12-10", "tpsb_board"),
    ]

    cov = agency_coverage.report(conn, only="tpsb")
    assert cov["agencies"]["tpsb"]["summary"]["contracts_accepted"] == 2
    assert cov["incomplete"] is False

    import toronto_bids.extract as extract_mod
    monkeypatch.setattr(extract_mod, "ExtractionClient", lambda: (_ for _ in ()).throw(ValueError("no key")))
    failures = cli._capture_agency_bodies(
        conn, bodies=["tpsb"], fetch=False, scrape=False, virtual_display=False, out=lambda _m: None
    )
    assert failures == []


def test_only_tpsb_portal_skips_bids_tenders_cleanly(conn, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    from toronto_bids.sources import bids_tenders
    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)
    called = []
    monkeypatch.setattr(bids_tenders, "run_portal_capture", lambda *a, **k: called.append(k) or {})

    assert cli.main(["enrich-agencies", "--only", "tpsb", "--portal"]) == 0
    assert not called
    assert "no bids&tenders portal" in capsys.readouterr().out
