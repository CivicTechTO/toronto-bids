"""Tests for Toronto Parking Authority (PA) TMMIS board report capture and extraction (#335)."""

import json

from toronto_bids import agency_coverage, cli, config
from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking.agency_reference import report_reference
from toronto_bids.sources import tpa_board
from toronto_bids.store import db

_PA_AGENDA_HTML = """
<html><body>
  <h3>PA21.7 - Winter Maintenance Services Contract Award (2026-2031)</h3>
  <a href="https://www.toronto.ca/legdocs/mmis/2026/pa/bgrd/backgroundfile-270001.pdf">Staff Report</a>
  <a href="https://www.toronto.ca/legdocs/mmis/2026/bu/bgrd/backgroundfile-261182.pdf">Cross-linked Budget Notes</a>
</body></html>
"""


def test_download_tpa_reports_indexes_agendas_and_fetches_only_pa_pdfs(
    conn, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "TPA_REPORTS_DIR", tmp_path / "tpa")
    monkeypatch.setattr(
        "toronto_bids.sources.trca_board._pdftotext", lambda _p: "TPA award report text"
    )

    fetched = []

    class FakeHttp:
        def get_bytes(self, url):
            fetched.append(url)
            return b"%PDF-1.4\n%tpa\n" + url.encode()

    failures = []
    n = tpa_board.download_tpa_reports(
        conn, FakeHttp(), {"2026.PA21": _PA_AGENDA_HTML}, failures=failures
    )
    assert n == 1
    assert failures == []
    assert fetched == [
        "https://www.toronto.ca/legdocs/mmis/2026/pa/bgrd/backgroundfile-270001.pdf"
    ]
    row = conn.execute(
        "SELECT url, reference, kind, sha256, text FROM background_pdf WHERE url LIKE '%/pa/%'"
    ).fetchone()
    assert row["reference"] == "2026.PA21"
    assert row["kind"] == "agency_board"
    assert row["sha256"]
    assert row["text"] == "TPA award report text"


def test_pa_report_reference_accepts_item_and_falls_back_on_meeting():
    url = "https://www.toronto.ca/legdocs/mmis/2026/pa/bgrd/backgroundfile-270001.pdf"
    assert report_reference("2026.PA21.7", url) == "2026.PA21.7"
    assert report_reference("2026.PA21", url) == url


def test_tpa_backfill_writes_awards_and_bids_for_parking_authority(conn, monkeypatch):
    seed_buyers(conn)
    url1 = "https://www.toronto.ca/legdocs/mmis/2026/pa/bgrd/backgroundfile-270001.pdf"
    url2 = "https://www.toronto.ca/legdocs/mmis/2026/pa/bgrd/backgroundfile-270002.pdf"

    ext1 = {
        "contracts": [
            {
                "reference": "2025-CON-CP150-01",
                "solicitation_type": "RFT",
                "title": "Car Park 150 Structural Restoration",
                "decision_date": "2026-06-15",
                "bids": [
                    {"supplier_name": "Heritage Restoration Inc.", "amount_raw": "$3,200,000", "amount_basis": "plus_HST", "status": "compliant"},
                    {"supplier_name": "Runner Up Co.", "amount_raw": "$3,450,000", "amount_basis": "plus_HST", "status": "compliant"},
                ],
                "awards": [
                    {"supplier_name": "Heritage Restoration Inc.", "amount_raw": "$3,200,000", "amount_basis": "plus_HST", "value_confidential": False}
                ],
            }
        ]
    }
    ext2 = {
        "contracts": [
            {
                "reference": "Sole Source",
                "solicitation_type": "sole_source",
                "title": "PARCS Software Maintenance",
                "decision_date": "2026-06-15",
                "bids": [],
                "awards": [
                    {"supplier_name": "PARCS Vendor Inc.", "amount_raw": "$310,000", "amount_basis": "plus_HST", "value_confidential": False}
                ],
            }
        ]
    }

    conn.execute(
        "INSERT INTO background_pdf(url,reference,kind,sha256,text) VALUES (?,?,?,?,?)",
        (url1, "2026.PA21", "agency_board", "tpasha1", "held tpa text 1"),
    )
    conn.execute(
        "INSERT INTO background_pdf(url,reference,kind,sha256,text) VALUES (?,?,?,?,?)",
        (url2, "2026.PA21.9", "agency_board", "tpasha2", "held tpa text 2"),
    )
    db.mark_extracted(conn, "tpasha1", EXTRACTOR_VERSION, json.dumps(ext1))
    db.mark_extracted(conn, "tpasha2", EXTRACTOR_VERSION, json.dumps(ext2))
    conn.commit()

    result = backfill_from_extraction(conn, "tpa")
    assert result["awards_written"] == 2
    assert result["bids_written"] == 2
    assert result["contracts_refused"] == 0

    awards = conn.execute(
        "SELECT native_ref, supplier_name_raw, award_amount, source "
        "FROM agency_award WHERE source='tpa_board' ORDER BY native_ref"
    ).fetchall()
    assert [tuple(r) for r in awards] == [
        ("2025-CON-CP150-01", "Heritage Restoration Inc.", "$3,200,000", "tpa_board"),
        ("2026.PA21.9", "PARCS Vendor Inc.", "$310,000", "tpa_board"),
    ]

    cov = agency_coverage.report(conn, only="tpa")
    assert cov["agencies"]["tpa"]["summary"]["contracts_accepted"] == 2
    assert cov["incomplete"] is False

    import toronto_bids.extract as extract_mod
    monkeypatch.setattr(extract_mod, "ExtractionClient", lambda: (_ for _ in ()).throw(ValueError("no key")))
    monkeypatch.setattr(tpa_board, "cached_pa_agendas", lambda: {})
    failures = cli._capture_agency_bodies(
        conn, bodies=["tpa"], fetch=False, scrape=False, virtual_display=False, out=lambda _m: None
    )
    assert failures == []


def test_only_tpa_portal_skips_bids_tenders_cleanly(conn, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    from toronto_bids.sources import bids_tenders
    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)
    called = []
    monkeypatch.setattr(bids_tenders, "run_portal_capture", lambda *a, **k: called.append(k) or {})

    assert cli.main(["enrich-agencies", "--only", "tpa", "--portal"]) == 0
    assert not called
    assert "no bids&tenders portal" in capsys.readouterr().out
