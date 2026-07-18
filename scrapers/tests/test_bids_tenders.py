import json
import pathlib

from toronto_bids.sources.bids_tenders import parse_listing

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
