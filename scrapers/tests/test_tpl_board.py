"""Tests for Toronto Public Library (TPL) Board procurement report capture and extraction (#333)."""

from datetime import date
import json
import httpx
import pytest

from toronto_bids import agency_coverage, cli, config
from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking.agency_reference import normalize_agency_reference
from toronto_bids.sources import tpl_board
from toronto_bids.store import db

_ROOT_MEETINGS_HTML = """
<html><body><main>
  <a href="https://tpl.ca/about-the-library/board/meetings/2026-jan-26/">Monday, January 26, 6 p.m.</a>
  <a href="/about-the-library/board/meetings/june-22-2026/">Monday, June 22, 6 p.m.</a>
  <a href="/about-the-library/board/meetings/library-board-meeting-december-14-2026/">Monday, December 14, 6 p.m.</a>
  <a href="/about-the-library/board/meetings/naming-committee-meeting-april-2-2026/">Thursday, April 2, 4 p.m.</a>
  <a href="/about-the-library/board/meetings/city-librarians-performance-review-committee-april-13-2026/">Monday, April 13, 4 p.m.</a>
  <a href="/about-the-library/board/meetings/board-meetings-2025/">Library Board Meetings – 2025</a>
  <a href="/about-the-library/board/meetings/board-meetings-2021/">Library Board Meetings – 2021</a>
  <a href="/about-the-library/board/meetings/board-meetings-2013/">Library Board Meetings – 2013</a>
</main></body></html>
"""

_YEAR_2021_HTML = """
<html><body><main>
  <a href="/about-the-library/board/meetings/2021-jun-22/">Library Board Meeting – June 22, 2021</a>
  <a href="/about-the-library/board/meetings/2021-jan-14/">City Librarian Selection Committee Meeting – January 14, 2021</a>
</main></body></html>
"""

_MEETING_PAGE_HTML = """
<html><body><main>
  <a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/B20250224-full-agenda.pdf">Consolidated Board Document Package</a>
  <a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/04-minutes-january-27.pdf">Minutes (PDF)</a>
  <a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/18-deer-park-branch-general-contractor-award-of-contract.pdf">Deer Park Branch General Contractor – Award of Contract (PDF)</a>
  <a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2026/04/15-dawes-road-reconstruction-and-expansion-project-award-of-construction-contract.pdf">Dawes Road Reconstruction and Expansion Project - Award of Construction Contract and Budget Adjustment (PDF)</a>
  <a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/11/16-dawes-road-branch-increase-to-architect-contract.pdf">Dawes Road branch – Increase to Architect Contract (PDF)</a>
  <a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/12-runnymede-branch-award-of-tender.pdf">Board Report (PDF)</a>
  <a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/19-janitorial-services-contract-extension.pdf">Janitorial Services Contract Extension (PDF)</a>
  <a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/07-chairs-report.pdf">Chair's Report (PDF)</a>
  <a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/10-purchasing-policy-review.pdf">Purchasing Policy Review (PDF)</a>
  <a href="https://example.org/other-award-of-contract.pdf">External Award of Contract</a>
</main></body></html>
"""


def test_parse_year_archive_urls_respects_min_year():
    archives = tpl_board.parse_year_archive_urls(_ROOT_MEETINGS_HTML, min_year=2014)
    assert archives == [
        {"year": 2021, "url": "https://tpl.ca/about-the-library/board/meetings/board-meetings-2021/"},
        {"year": 2025, "url": "https://tpl.ca/about-the-library/board/meetings/board-meetings-2025/"},
    ]


def test_parse_meeting_urls_skips_subcommittees_and_future_meetings():
    meetings = tpl_board.parse_meeting_urls(
        _ROOT_MEETINGS_HTML, year=2026, today=date(2026, 11, 22)
    )
    assert [(m["slug"], m["date"]) for m in meetings] == [
        ("2026-jan-26", "2026-01-26"),
        ("june-22-2026", "2026-06-22"),
    ]


@pytest.mark.parametrize(
    "label,url,expected",
    [
        ("Deer Park Branch General Contractor – Award of Contract (PDF)",
         "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/18-deer-park.pdf", True),
        ("Dawes Road – Award of Construction Contract and Budget Adjustment (PDF)",
         "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2026/04/15-dawes.pdf", True),
        ("Board Report (PDF)",
         "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2016/11/12-runnymede-branch-award-of-tender.pdf", True),
        ("Security Guard Service – Increase to the Value of the Contract and Purchase Order (PDF)",
         "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2021/06/12-security.pdf", True),
        ("Bridlewood branch Construction – Increase to Tender",
         "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2026/04/20-bridlewood.pdf", True),
        ("Specialized Professional Services – Extension of Contract (PDF)",
         "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/11/18-specialized.pdf", True),
        ("Consolidated Board Document Package (5.2 MB)",
         "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2015/10/B20151026-full-agenda.pdf", False),
        ("Minutes (PDF)",
         "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/04-minutes.pdf", False),
        ("Purchasing Policy Update (PDF)",
         "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/10-purchasing-policy.pdf", False),
    ],
)
def test_is_procurement_pdf(label, url, expected):
    assert tpl_board.is_procurement_pdf(label, url) is expected


def test_parse_meeting_pdfs_filters_and_scopes_to_tpl_hosts():
    pdfs = tpl_board.parse_meeting_pdfs(_MEETING_PAGE_HTML)
    filenames = [p["url"].rsplit("/", 1)[-1] for p in pdfs]
    assert filenames == [
        "18-deer-park-branch-general-contractor-award-of-contract.pdf",
        "15-dawes-road-reconstruction-and-expansion-project-award-of-construction-contract.pdf",
        "16-dawes-road-branch-increase-to-architect-contract.pdf",
        "12-runnymede-branch-award-of-tender.pdf",
        "19-janitorial-services-contract-extension.pdf",
    ]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("142-24-TPL-RFT", "142-24-TPL-RFT"),
        ("Project 142-24-tpl-rft (Deer Park)", "142-24-TPL-RFT"),
        ("221-26-TPL-RFT (L2)", "221-26-TPL-RFT"),
        ("TPL 037-2014", "TPL 037-2014"),
        ("TPL-075-2017 RFSQ", "TPL 075-2017"),
        ("Sole Source", None),
        ("Direct Award", None),
    ],
)
def test_tpl_reference_normalization(raw, expected):
    assert normalize_agency_reference("toronto-public-library", raw) == expected


def test_tpl_ambiguous_reference_raises():
    with pytest.raises(ValueError, match="ambiguous TPL reference"):
        normalize_agency_reference("toronto-public-library", "142-24-TPL-RFT and 210-26-TPL-RFP")


def test_download_tpl_reports_caches_historical_years_and_stores_pdfs(
    conn, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "TPL_REPORTS_DIR", tmp_path / "tpl")
    monkeypatch.setattr(config, "TPL_MEETINGS_DIR", tmp_path / "tpl" / "meetings")
    monkeypatch.setattr(
        "toronto_bids.sources.trca_board._pdftotext", lambda _p: "TPL award text"
    )

    pdf_2021 = "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2021/06/12-security-increase-to-contract.pdf"
    pdf_2026 = "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2026/06/14-tote-bags-award-of-contract.pdf"
    fetched = []

    class FakeHttp:
        def get_text(self, url):
            fetched.append(url)
            if url == config.TPL_MEETINGS_URL:
                return _ROOT_MEETINGS_HTML
            if url.endswith("/board-meetings-2021/"):
                return _YEAR_2021_HTML
            if url.endswith("/board-meetings-2025/"):
                raise httpx.ConnectError("transient timeout")
            if url.endswith("/2021-jun-22/"):
                return f'<html><body><main><a href="{pdf_2021}">Security – Increase to Contract</a></main></body></html>'
            if url.endswith("/2026-jan-26/"):
                return '<html><body><main><a href="https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2026/01/04-minutes.pdf">Minutes</a></main></body></html>'
            if url.endswith("/june-22-2026/"):
                return f'<html><body><main><a href="{pdf_2026}">Tote Bags – Award of Contract</a></main></body></html>'
            raise AssertionError(f"unexpected URL {url}")

        def get_bytes(self, url):
            return b"%PDF-1.4\n%fake\n" + url.encode()

    failures = []
    n = tpl_board.download_tpl_reports(
        conn, FakeHttp(), failures=failures, today=date(2026, 11, 22)
    )
    assert n == 2
    assert len(failures) == 1
    assert failures[0][0] == "download:tpl"
    assert "2025" in failures[0][1]

    # Second run reuses cached 2021 year/meeting HTML while refreshing 2026.
    fetched.clear()
    n2 = tpl_board.download_tpl_reports(
        conn, FakeHttp(), failures=[], today=date(2026, 11, 22)
    )
    assert n2 == 0
    assert "https://tpl.ca/about-the-library/board/meetings/board-meetings-2021/" not in fetched
    assert "https://tpl.ca/about-the-library/board/meetings/2021-jun-22/" not in fetched
    assert "https://tpl.ca/about-the-library/board/meetings/june-22-2026/" in fetched


def test_tpl_backfill_handles_rft_and_oecm_reports(conn, monkeypatch):
    seed_buyers(conn)
    rft_url = "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2025/05/18-deer-park-branch-general-contractor-award-of-contract.pdf"
    oecm_url = "https://cdn-web-main.bibliocms.com/wp-content/uploads/sites/164/2026/04/23-library-furniture-award-of-contract.pdf"

    rft_extraction = {
        "contracts": [
            {
                "reference": "project 142-24-TPL-RFT",
                "solicitation_type": "RFT",
                "title": "Deer Park Branch General Contractor",
                "declared_submissions": 7,
                "decision_date": "2025-02-24",
                "bids": [],
                "awards": [
                    {
                        "supplier_name": "Struct-Con Construction Limited",
                        "amount_raw": "$867,028",
                        "amount_basis": "plus_HST",
                        "contingency_raw": "$78,821",
                        "value_confidential": False,
                    }
                ],
            }
        ]
    }
    oecm_extraction = {
        "contracts": [
            {
                "reference": None,
                "solicitation_type": "VOR",
                "title": "Library Furniture",
                "decision_date": "2025-01-27",
                "bids": [],
                "awards": [
                    {
                        "supplier_name": "POI Business Interiors",
                        "amount_raw": "$4.4 million",
                        "amount_basis": "plus_HST",
                        "value_confidential": False,
                    }
                ],
            }
        ]
    }

    conn.execute(
        "INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)",
        (rft_url, "agency_board", "rftsha", "held rft text"),
    )
    conn.execute(
        "INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)",
        (oecm_url, "agency_board", "oecmsha", "held oecm text"),
    )
    db.mark_extracted(conn, "rftsha", EXTRACTOR_VERSION, json.dumps(rft_extraction))
    db.mark_extracted(conn, "oecmsha", EXTRACTOR_VERSION, json.dumps(oecm_extraction))
    conn.commit()

    result = backfill_from_extraction(conn, "tpl")
    assert result["awards_written"] == 2
    assert result["contracts_refused"] == 0

    awards = conn.execute(
        "SELECT native_ref, supplier_name_raw, award_amount, award_date, source "
        "FROM agency_award WHERE source='tpl_board' ORDER BY native_ref"
    ).fetchall()
    assert [tuple(r) for r in awards] == [
        ("142-24-TPL-RFT", "Struct-Con Construction Limited", "$867,028", "2025-02-24", "tpl_board"),
        (oecm_url, "POI Business Interiors", "$4.4 million", "2025-01-27", "tpl_board"),
    ]

    cov = agency_coverage.report(conn, only="tpl")
    assert cov["agencies"]["tpl"]["summary"]["contracts_accepted"] == 2
    assert cov["incomplete"] is False

    import toronto_bids.extract as extract_mod
    monkeypatch.setattr(extract_mod, "ExtractionClient", lambda: (_ for _ in ()).throw(ValueError("no key")))
    failures = cli._capture_agency_bodies(
        conn, bodies=["tpl"], fetch=False, scrape=False, virtual_display=False, out=lambda _m: None
    )
    assert failures == []


def test_only_tpl_portal_skips_bids_tenders_cleanly(conn, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    from toronto_bids.sources import bids_tenders
    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)
    called = []
    monkeypatch.setattr(bids_tenders, "run_portal_capture", lambda *a, **k: called.append(k) or {})

    assert cli.main(["enrich-agencies", "--only", "tpl", "--portal"]) == 0
    assert not called
    assert "no bids&tenders portal" in capsys.readouterr().out
