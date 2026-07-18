import json
import pathlib
import sqlite3

import pytest

from toronto_bids.buyers import seed_buyers
from toronto_bids.models import AgencySolicitation
from toronto_bids.sources.bids_tenders import parse_listing, store_listings
from toronto_bids.store import db

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _sample():
    return json.loads((FIXTURES / "bids_tenders_record_sample.json").read_text())


def test_parse_listing_maps_documented_fields():
    row = parse_listing(_sample(), buyer_id=7)
    assert row.buyer_id == 7
    assert row.native_ref == "RFT-2026-014"          # ReferenceNumber, normalized
    assert row.title == "Trail Bridge Replacement - Example Creek"
    assert row.status == "Open"
    assert row.closing_date == "2026-08-15T14:00:00"
    assert row.posted_date == "2026-07-20T09:00:00"
    assert row.portal_url.endswith("/Tender/Detail/0f9a1b2c-3d4e-5f60-7182-93a4b5c6d7e8")
    assert row.source == "bids_tenders"


def test_parse_listing_falls_back_to_id_when_no_reference():
    rec = _sample(); del rec["ReferenceNumber"]
    row = parse_listing(rec, buyer_id=7)
    assert row.native_ref == "0F9A1B2C-3D4E-5F60-7182-93A4B5C6D7E8"   # Id, uppercased


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_store_listings_inserts_portal_row(conn):
    ids = seed_buyers(conn)
    n = store_listings(conn, [_sample()], ids)
    assert n == 1
    row = conn.execute("SELECT native_ref, title, status, source FROM agency_solicitation").fetchone()
    assert row["native_ref"] == "RFT-2026-014"
    assert row["source"] == "bids_tenders"
    assert row["title"] == "Trail Bridge Replacement - Example Creek"


def test_store_listings_enriches_a_board_report_row(conn):
    ids = seed_buyers(conn)
    # A board-report row already exists for the same ref, with a title but no dates/status.
    db.upsert_row(conn, AgencySolicitation(
        buyer_id=ids["trca"], native_ref="RFT-2026-014", title="Board title",
        status="awarded", source="trca_board"), overwrite=False)
    store_listings(conn, [_sample()], ids)
    rows = conn.execute("SELECT title, status, closing_date FROM agency_solicitation "
                        "WHERE native_ref='RFT-2026-014'").fetchall()
    assert len(rows) == 1                              # COALESCE-enriched, not duplicated
    assert rows[0]["title"] == "Board title"           # board title preserved (overwrite guard)
    assert rows[0]["closing_date"] == "2026-08-15T14:00:00"  # portal filled the empty date


def test_store_listings_empty_is_noop(conn):
    ids = seed_buyers(conn)
    assert store_listings(conn, [], ids) == 0


def test_record_listings_writes_one_file_per_record(tmp_path):
    from toronto_bids.sources.bids_tenders import record_listings
    recs = [dict(_sample(), status_code=1), dict(_sample(), status_code=3)]
    n = record_listings(recs, tmp_path)
    assert n == 2
    written = sorted(p.name for p in tmp_path.glob("*.json"))
    assert all(name.startswith("trca-") for name in written)


def test_run_portal_capture_isolates_a_failing_body(conn, monkeypatch):
    from toronto_bids.sources import bids_tenders as bt
    ids = seed_buyers(conn)

    def fake_fetch(portal, **_kw):
        if portal["slug"] == "trca":
            raise RuntimeError("boom")            # one body fails
        yield dict(_sample(), buyer_slug="toronto-zoo")

    monkeypatch.setattr(bt, "fetch_listings", fake_fetch)
    result = bt.run_portal_capture(conn, log=lambda _m: None)
    assert result["toronto-zoo"] == 1             # zoo still captured
    assert "trca" in result and result["trca"] == "FAILED: boom"   # trca isolated, recorded
