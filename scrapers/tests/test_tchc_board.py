"""Tests for TCHC BIFAC and Board procurement report capture and extraction (#332)."""

import json
import httpx
import pytest

from toronto_bids import agency_coverage, cli, config
from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking.agency_reference import normalize_agency_reference
from toronto_bids.sources import tchc_board
from toronto_bids.store import db

_PAST_MEETINGS_HTML = """
<html><body><main>
  <a href="/events/building-investment-finance-and-audit-committee-bifac-meeting-november-12-2026">BIFAC Meeting - November 12, 2026</a>
  <a href="https://www.torontohousing.ca/events/building-investment-finance-and-audit-committee-bifac-meeting-november-12-2026/">BIFAC Dup</a>
  <a href="/events/building-investment-finance-and-audit-committee-bifac-meeting-april-28-2026-camera">BIFAC In-Camera Meeting - April 28, 2026</a>
  <a href="/events/board-directors-meeting-october-15-2026">Board of Directors Meeting - October 15, 2026</a>
  <a href="/events/tchc-special-camera-board-meeting-january-19-2026">Special In-Camera Board Meeting</a>
  <a href="/events/building-investment-finance-and-audit-committee-bifac-meeting-may-18-2021">BIFAC Meeting - May 18, 2021</a>
  <a href="/events/building-investment-finance-and-audit-committee-bifac-public-session-september-14-2020">BIFAC Meeting - September 14, 2020</a>
  <a href="/events/governance-communications-and-human-resources-committee-gchrc-meeting-november-5-2026">GCHRC Meeting</a>
  <a href="/events/tenant-services-committee-tsc-meeting-september-23-2026">TSC Meeting</a>
  <a href="https://example.org/events/building-investment-finance-and-audit-committee-2026">External</a>
</main></body></html>
"""

_MEETING_PAGE_HTML = """
<html><body><main>
  <a href="/sites/default/files/2026-11/public_bifac_materials_-_november_12_2026.pdf">Public BIFAC Materials – November 12, 2026</a>
  <a href="/sites/default/files/2026-11/item_2_-_public_bifac_agenda_-_november_12_2026.pdf">Item 2 – Public BIFAC Meeting Agenda – November 12, 2026</a>
  <a href="/sites/default/files/2026-11/item_4_-_public_bifac_meeting_minutes_-_september_9_2026.pdf">Item 4 – Public BIFAC Meeting Minutes of September 9, 2026</a>
  <a href="/sites/default/files/2026-11/item_5_-_public_bifac_action_items_list_-_november_12_2026.pdf">Item 5 – Public Action Items List</a>
  <a href="/sites/default/files/2026-11/item_6_-_2026-83_-_annual_review_of_bifac_iac_and_pac_charters_w_attach.pdf">Item 6 – Annual Review of BIFAC, IAC and Procurement Award Committee Charters</a>
  <a href="/sites/default/files/2026-11/item_6a_-_2026-105_-_vendor_award_window_repairs.pdf">Item 6A – Vendor Award: Demand Maintenance Services for Window and Door Repairs</a>
  <a href="/sites/default/files/2026-11/item_6c_-_2026-107_-_2026_reconciliation_of_pm_and_demand_repair_contracts_over_5m.pdf">Item 6C – 2026 Reconciliation of Preventive Maintenance and Demand Repair Contracts Over $5M</a>
  <a href="/sites/default/files/2026-11/item_7a_-_2026-109_-_direct_award_microsoft_licensing_services.pdf">Item 7A – Direct Award: Microsoft Licensing Services</a>
  <a href="/sites/default/files/2026-11/item_7e_-_2026-113_-_q3_2026_procurement_award_committee_awards_and_revenue_contracts.pdf">Item 7E – Q3 2026 Procurement Award Committee (“PAC”) Awards and Revenue Contracts</a>
  <a href="/sites/default/files/2026-11/item_8c_-_2026-86_-_change_order_reconciliation_of_design_fees_at_300_dufferin.pdf">Item 8C – Change Order: Reconciliation of Design and Contract Administration Fees at 300 Dufferin Street</a>
  <a href="/sites/default/files/2026-11/item_9c_-_2026-90_-_procurement_transformation_plan_implementation_update.pdf">Item 9C – Procurement Transformation Plan Implementation Update</a>
  <a href="/sites/default/files/2026-11/item_6b_-_deputation_-_cap_-_bifac_2026-11-12.pdf">Item 6B – Deputation – CAP</a>
</main></body></html>
"""


def test_parse_meeting_urls_filters_to_public_bifac_and_board_from_min_year():
    meetings = tchc_board.parse_meeting_urls(_PAST_MEETINGS_HTML, min_year=2021)
    assert [(m["body"], m["year"], m["slug"]) for m in meetings] == [
        ("bifac", 2021, "building-investment-finance-and-audit-committee-bifac-meeting-may-18-2021"),
        ("board", 2026, "board-directors-meeting-october-15-2026"),
        ("bifac", 2026, "building-investment-finance-and-audit-committee-bifac-meeting-november-12-2026"),
    ]
    assert all(m["url"].startswith("https://torontohousing.ca/events/") for m in meetings)


@pytest.mark.parametrize(
    "label,url,expected",
    [
        ("Item 10 – Q3 2025 Procurement Award Committee (“PAC”) Awards and Revenue Contracts",
         "https://torontohousing.ca/sites/default/files/2025-11/item_10_-_2025-104.pdf", True),
        ("Item 2F - PAC Awards Q1 2021 w attachment (PDF)",
         "https://torontohousing.ca/sites/default/files/2021-05/item_2f_-_pac_awards_q1_2021.pdf", True),
        ("Item 6A - Contractor Award PTAC Replacement at 145 Mutual",
         "https://torontohousing.ca/sites/default/files/2021-09/item_6a.pdf", True),
        ("Item 7A – Direct Award: Microsoft Licensing Services",
         "https://torontohousing.ca/sites/default/files/2026-11/item_7a.pdf", True),
        ("Item 8C – Change Order: Reconciliation of Design Fees at 300 Dufferin",
         "https://torontohousing.ca/sites/default/files/2026-09/item_8c.pdf", True),
        ("Item 7 - Garage Restoration Services (RFQ 20288-PP)",
         "https://torontohousing.ca/sites/default/files/2021-05/item_7_rfq_20288-pp.pdf", True),
        ("Item 6 – Annual Review of BIFAC, IAC and Procurement Award Committee Charters",
         "https://torontohousing.ca/sites/default/files/2026-09/item_6_charters.pdf", False),
        ("Item 6C – 2026 Reconciliation of Preventive Maintenance and Demand Repair Contracts Over $5M",
         "https://torontohousing.ca/sites/default/files/2026-11/item_6c.pdf", False),
        ("Item 8B – Strategic Procurement Update",
         "https://torontohousing.ca/sites/default/files/2025-11/item_8b.pdf", False),
        ("Public BIFAC Materials - May 18, 2021 Meeting v5 WEB POSTING (PDF)",
         "https://torontohousing.ca/sites/default/files/2021-05/public_bifac_materials.pdf", False),
        ("Item 4 – Public BIFAC Meeting Minutes of September 9, 2026",
         "https://torontohousing.ca/sites/default/files/2026-11/item_4_minutes.pdf", False),
    ],
)
def test_is_procurement_pdf(label, url, expected):
    assert tchc_board.is_procurement_pdf(label, url) is expected


def test_parse_meeting_pdfs_selects_only_procurement_reports():
    pdfs = tchc_board.parse_meeting_pdfs(_MEETING_PAGE_HTML)
    filenames = [p["url"].rsplit("/", 1)[-1] for p in pdfs]
    assert filenames == [
        "item_6a_-_2026-105_-_vendor_award_window_repairs.pdf",
        "item_7a_-_2026-109_-_direct_award_microsoft_licensing_services.pdf",
        "item_7e_-_2026-113_-_q3_2026_procurement_award_committee_awards_and_revenue_contracts.pdf",
        "item_8c_-_2026-86_-_change_order_reconciliation_of_design_fees_at_300_dufferin.pdf",
    ]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("RFP 25047", "RFP 25047"),
        ("RFP-25304", "RFP 25304"),
        ("RFP21075", "RFP 21075"),
        ("RFP 25109 PP", "RFP 25109-PP"),
        ("RFP 25109-PP", "RFP 25109-PP"),
        ("RFVQ 24036 - 2", "RFVQ 24036-2"),
        ("RFVQ 25190 (State of Good Repair)", "RFVQ 25190"),
        ("RFP RP-025", "RFP RP-025"),
        ("Direct Award", None),
        ("Sole Source", None),
        ("Single Source", None),
        ("Change Order", None),
        ("Not Applicable", None),
    ],
)
def test_tchc_reference_normalization(raw, expected):
    assert normalize_agency_reference("toronto-community-housing", raw) == expected


def test_tchc_ambiguous_reference_raises():
    with pytest.raises(ValueError, match="ambiguous TCHC reference"):
        normalize_agency_reference("toronto-community-housing", "RFP 25047 and RFQ 25171")


def test_download_tchc_reports_indexes_caches_old_meetings_and_stores_pdfs(
    conn, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "TCHC_REPORTS_DIR", tmp_path / "tchc")
    monkeypatch.setattr(config, "TCHC_MEETINGS_DIR", tmp_path / "tchc" / "meetings")
    monkeypatch.setattr(
        "toronto_bids.sources.trca_board._pdftotext", lambda _p: "PAC award text"
    )

    pdf_url = "https://torontohousing.ca/sites/default/files/2026-11/item_7a_-_2026-109_-_direct_award_microsoft_licensing_services.pdf"
    meeting_2021 = "https://torontohousing.ca/events/building-investment-finance-and-audit-committee-bifac-meeting-may-18-2021"
    meeting_2026_board = "https://torontohousing.ca/events/board-directors-meeting-october-15-2026"
    meeting_2026_bifac = "https://torontohousing.ca/events/building-investment-finance-and-audit-committee-bifac-meeting-november-12-2026"

    fetched_urls = []

    class FakeHttp:
        def get_text(self, url):
            fetched_urls.append(url)
            if url == config.TCHC_PAST_MEETINGS_URL:
                return _PAST_MEETINGS_HTML
            if url == meeting_2021:
                return '<html><body><a href="/sites/default/files/2021-05/item_2f_-_pac_awards_q1_2021.pdf">Item 2F - PAC Awards Q1 2021</a></body></html>'
            if url == meeting_2026_board:
                raise httpx.ConnectError("transient timeout")
            if url == meeting_2026_bifac:
                return f'<html><body><a href="{pdf_url}">Item 7A – Direct Award: Microsoft</a></body></html>'
            raise AssertionError(f"unexpected URL {url}")

        def get_bytes(self, url):
            return b"%PDF-1.4\n%fake\n" + url.encode()

    failures = []
    n = tchc_board.download_tchc_reports(
        conn, FakeHttp(), failures=failures, current_year=2026
    )
    assert n == 2
    assert len(failures) == 1
    assert failures[0][0] == "download:tchc"
    assert meeting_2026_board in failures[0][1]
    rows = conn.execute(
        "SELECT url, kind, sha256, text FROM background_pdf WHERE url LIKE '%torontohousing.ca%' ORDER BY url"
    ).fetchall()
    assert len(rows) == 2
    assert all(r["kind"] == "agency_board" and r["sha256"] and r["text"] == "PAC award text" for r in rows)

    # Second run reuses cached 2021 meeting HTML while refreshing 2026 meetings.
    fetched_urls.clear()
    n2 = tchc_board.download_tchc_reports(conn, FakeHttp(), failures=[], current_year=2026)
    assert n2 == 0
    assert meeting_2021 not in fetched_urls
    assert meeting_2026_bifac in fetched_urls


def test_tchc_backfill_accepts_rfx_and_single_direct_award_and_refuses_multi_direct_award(
    conn, monkeypatch
):
    seed_buyers(conn)
    pac_url = "https://torontohousing.ca/sites/default/files/2025-11/item_10_-_2025-104_-_q3_2025_pac.pdf"
    direct_url = "https://torontohousing.ca/sites/default/files/2026-11/item_7a_-_2026-109_-_direct_award_microsoft.pdf"

    pac_extraction = {
        "contracts": [
            {
                "reference": "RFP 25109 PP",
                "solicitation_type": "RFP",
                "title": "Design build solar PV carport",
                "declared_submissions": 3,
                "decision_date": "2025-08-28",
                "bids": [],
                "awards": [
                    {
                        "supplier_name": "12256894 Canada Incorporated",
                        "amount_raw": "$619,600.00",
                        "amount_basis": "plus_HST",
                        "value_confidential": False,
                    }
                ],
            },
            {
                "reference": "Direct Award",
                "solicitation_type": "sole_source",
                "title": "Preventive maintenance for 18 elevators",
                "decision_date": "2025-07-03",
                "bids": [],
                "awards": [
                    {
                        "supplier_name": "Schindler Elevator Canada",
                        "amount_raw": "$1,073,099.00",
                        "amount_basis": "plus_HST",
                        "value_confidential": False,
                    }
                ],
            },
        ]
    }
    direct_extraction = {
        "contracts": [
            {
                "reference": "Direct Award",
                "solicitation_type": "sole_source",
                "title": "Microsoft Licensing Services",
                "decision_date": "2026-11-12",
                "bids": [],
                "awards": [
                    {
                        "supplier_name": "Microsoft Canada Inc.",
                        "amount_raw": "$400,067.76",
                        "amount_basis": "plus_HST",
                        "value_confidential": False,
                    }
                ],
            }
        ]
    }

    conn.execute(
        "INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)",
        (pac_url, "agency_board", "pacsha", "held pac text"),
    )
    conn.execute(
        "INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)",
        (direct_url, "agency_board", "directsha", "held direct text"),
    )
    db.mark_extracted(conn, "pacsha", EXTRACTOR_VERSION, json.dumps(pac_extraction))
    db.mark_extracted(conn, "directsha", EXTRACTOR_VERSION, json.dumps(direct_extraction))
    conn.commit()

    result = backfill_from_extraction(conn, "tchc")
    assert result["awards_written"] == 2
    assert result["contracts_refused"] == 1

    awards = conn.execute(
        "SELECT native_ref, supplier_name_raw, award_amount, award_date, source "
        "FROM agency_award WHERE source='tchc_board' ORDER BY native_ref"
    ).fetchall()
    assert [tuple(r) for r in awards] == [
        ("RFP 25109-PP", "12256894 Canada Incorporated", "$619,600.00", "2025-08-28", "tchc_board"),
        (direct_url, "Microsoft Canada Inc.", "$400,067.76", "2026-11-12", "tchc_board"),
    ]

    cov = agency_coverage.report(conn, only="tchc")
    assert cov["agencies"]["tchc"]["summary"]["contracts_accepted"] == 2
    assert cov["agencies"]["tchc"]["summary"]["contracts_refused"] == 1
    assert cov["incomplete"] is True

    # Capture helper runs tchc cleanly offline when all docs are cached.
    import toronto_bids.extract as extract_mod
    monkeypatch.setattr(extract_mod, "ExtractionClient", lambda: (_ for _ in ()).throw(ValueError("no key")))
    failures = cli._capture_agency_bodies(
        conn, bodies=["tchc"], fetch=False, scrape=False, virtual_display=False, out=lambda _m: None
    )
    assert [name for name, _ in failures] == ["identity:tchc"]


def test_only_tchc_portal_skips_bids_tenders_cleanly(conn, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    from toronto_bids.sources import bids_tenders
    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)
    called = []
    monkeypatch.setattr(bids_tenders, "run_portal_capture", lambda *a, **k: called.append(k) or {})

    assert cli.main(["enrich-agencies", "--only", "tchc", "--portal"]) == 0
    assert not called
    assert "no bids&tenders portal" in capsys.readouterr().out
