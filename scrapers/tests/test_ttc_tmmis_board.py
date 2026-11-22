"""Tests for 2025+ TTC Board (TTC) TMMIS report capture and extraction (#340)."""

import json

from toronto_bids import agency_coverage, cli, config
from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking.agency_reference import report_reference
from toronto_bids.sources import ttc_tmmis_board
from toronto_bids.store import db

_TTC_AGENDA_HTML = """
<html><body>
  <h3>TTC8.8 - Procurement Authorization - Single Source Extensions for Uniforms</h3>
  <a href="https://www.toronto.ca/legdocs/mmis/2025/ttc/bgrd/backgroundfile-259001.pdf">Staff Report</a>
  <a href="https://www.toronto.ca/legdocs/mmis/2025/bu/bgrd/backgroundfile-250001.pdf">Cross-linked Budget Notes</a>
</body></html>
"""


def test_download_ttc_tmmis_reports_indexes_agendas_and_fetches_only_tmmis_ttc_pdfs(
    conn, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "TTC_TMMIS_REPORTS_DIR", tmp_path / "ttc_tmmis")
    monkeypatch.setattr(
        "toronto_bids.sources.trca_board._pdftotext", lambda _p: "TTC TMMIS award report text"
    )
    # Historical ttc.ca URLs contain '/TTC/' case-insensitively; ensure they are never touched.
    conn.execute(
        "INSERT INTO background_pdf(url,reference,kind) VALUES (?,?,?)",
        (
            "https://cdn.ttc.ca/-/media/Project/TTC/DevProto/Documents/Board/2017/Report.pdf",
            None,
            "agency_board",
        ),
    )
    conn.commit()

    fetched = []

    class FakeHttp:
        def get_bytes(self, url):
            fetched.append(url)
            return b"%PDF-1.4\n%ttc\n" + url.encode()

    failures = []
    n = ttc_tmmis_board.download_ttc_tmmis_reports(
        conn, FakeHttp(), {"2025.TTC8": _TTC_AGENDA_HTML}, failures=failures
    )
    assert n == 1
    assert failures == []
    assert fetched == [
        "https://www.toronto.ca/legdocs/mmis/2025/ttc/bgrd/backgroundfile-259001.pdf"
    ]
    row = conn.execute(
        "SELECT url, reference, kind, sha256, text FROM background_pdf "
        "WHERE url LIKE '%/legdocs/mmis/%/ttc/%'"
    ).fetchone()
    assert row["reference"] == "2025.TTC8"
    assert row["kind"] == "agency_board"
    assert row["sha256"]
    assert row["text"] == "TTC TMMIS award report text"


def test_ttc_report_reference_accepts_item_and_falls_back_on_meeting():
    url = "https://www.toronto.ca/legdocs/mmis/2025/ttc/bgrd/backgroundfile-259001.pdf"
    assert report_reference("2025.TTC8.8", url) == "2025.TTC8.8"
    assert report_reference("2025.TTC8", url) == url


def test_ttc_tmmis_backfill_writes_awards_and_bids_without_disturbing_ttc_board(
    conn, monkeypatch
):
    seed_buyers(conn)
    buyer_id = conn.execute(
        "SELECT id FROM buyer WHERE slug='toronto-transit-commission'"
    ).fetchone()["id"]
    # Pre-existing 2000-2024 historical ttc_board row must stay completely untouched.
    conn.execute(
        "INSERT INTO agency_solicitation(buyer_id,native_ref,title,source) VALUES (?,?,?,?)",
        (buyer_id, "C31PM16811", "Historical TTC Report", "ttc_board"),
    )

    url1 = "https://www.toronto.ca/legdocs/mmis/2025/ttc/bgrd/backgroundfile-259001.pdf"
    url2 = "https://www.toronto.ca/legdocs/mmis/2025/ttc/bgrd/backgroundfile-259002.pdf"

    ext1 = {
        "contracts": [
            {
                "reference": "P25RP24001",
                "solicitation_type": "RFP",
                "title": "Enterprise SAP ERP Modernization",
                "decision_date": "2025-09-24",
                "bids": [
                    {
                        "supplier_name": "Deloitte Inc.",
                        "amount_raw": "$20,260,000",
                        "amount_basis": "plus_HST",
                        "status": "compliant",
                    },
                    {
                        "supplier_name": "Runner Up Consulting Ltd.",
                        "amount_raw": "$22,100,000",
                        "amount_basis": "plus_HST",
                        "status": "compliant",
                    },
                ],
                "awards": [
                    {
                        "supplier_name": "Deloitte Inc.",
                        "amount_raw": "$20,260,000",
                        "amount_basis": "plus_HST",
                        "value_confidential": False,
                    }
                ],
            }
        ]
    }
    ext2 = {
        "contracts": [
            {
                "reference": "Single Source",
                "solicitation_type": "sole_source",
                "title": "Single Source Extensions for Uniforms",
                "decision_date": "2025-09-24",
                "bids": [],
                "awards": [
                    {
                        "supplier_name": "Logistik Unicorp Inc.",
                        "amount_raw": "$17,390,000",
                        "amount_basis": "plus_HST",
                        "value_confidential": False,
                    }
                ],
            }
        ]
    }

    conn.execute(
        "INSERT INTO background_pdf(url,reference,kind,sha256,text) VALUES (?,?,?,?,?)",
        (url1, "2025.TTC8", "agency_board", "ttctmmissha1", "held ttc tmmis text 1"),
    )
    conn.execute(
        "INSERT INTO background_pdf(url,reference,kind,sha256,text) VALUES (?,?,?,?,?)",
        (url2, "2025.TTC8.8", "agency_board", "ttctmmissha2", "held ttc tmmis text 2"),
    )
    db.mark_extracted(conn, "ttctmmissha1", EXTRACTOR_VERSION, json.dumps(ext1))
    db.mark_extracted(conn, "ttctmmissha2", EXTRACTOR_VERSION, json.dumps(ext2))
    conn.commit()

    result = backfill_from_extraction(conn, "ttc_tmmis")
    assert result["awards_written"] == 2
    assert result["bids_written"] == 2
    assert result["contracts_refused"] == 0

    awards = conn.execute(
        "SELECT native_ref, supplier_name_raw, award_amount, source "
        "FROM agency_award WHERE source='ttc_tmmis_board' ORDER BY native_ref"
    ).fetchall()
    assert [tuple(r) for r in awards] == [
        ("2025.TTC8.8", "Logistik Unicorp Inc.", "$17,390,000", "ttc_tmmis_board"),
        ("P25RP24001", "Deloitte Inc.", "$20,260,000", "ttc_tmmis_board"),
    ]
    # Historical ttc_board solicitation still intact.
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM agency_solicitation WHERE source='ttc_board'"
        ).fetchone()[0]
        == 1
    )

    cov = agency_coverage.report(conn, only="ttc_tmmis")
    assert cov["agencies"]["ttc_tmmis"]["summary"]["contracts_accepted"] == 2
    assert cov["incomplete"] is False

    import toronto_bids.extract as extract_mod

    monkeypatch.setattr(
        extract_mod,
        "ExtractionClient",
        lambda: (_ for _ in ()).throw(ValueError("no key")),
    )
    monkeypatch.setattr(ttc_tmmis_board, "cached_ttc_agendas", lambda: {})
    failures = cli._capture_agency_bodies(
        conn,
        bodies=["ttc_tmmis"],
        fetch=False,
        scrape=False,
        virtual_display=False,
        out=lambda _m: None,
    )
    assert failures == []


def test_only_ttc_tmmis_portal_skips_bids_tenders_cleanly(
    conn, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    from toronto_bids.sources import bids_tenders

    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)
    called = []
    monkeypatch.setattr(
        bids_tenders, "run_portal_capture", lambda *a, **k: called.append(k) or {}
    )

    assert cli.main(["enrich-agencies", "--only", "ttc_tmmis", "--portal"]) == 0
    assert not called
    assert "no bids&tenders portal" in capsys.readouterr().out
