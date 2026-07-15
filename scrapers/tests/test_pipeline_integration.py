import json

from toronto_bids.sources import ckan, odata
from toronto_bids.store import db
from tests.conftest import FIXTURES


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_odata_spine_wins_and_ckan_links_on_document_number(conn):
    # OData spine first (overwrite=True) -> division = "Purchasing & Materials Management"
    for raw in _load("odata_solicitation.json")["value"]:
        for row in odata.normalize_solicitation(raw):
            db.upsert_row(conn, row, overwrite=True)
    # CKAN awarded backfills (overwrite=False). Inject a CONFLICTING division on the shared
    # doc so the test actually proves backfill does NOT clobber the spine value.
    for raw in _load("ckan_awarded.json")["result"]["records"]:
        if raw.get("Document Number") == "3303123110":
            raw = {**raw, "Division": "CKAN DIVISION - MUST NOT WIN"}
        for row in ckan.normalize_awarded(raw):
            db.upsert_row(conn, row, overwrite=False)
    conn.commit()

    sol = conn.execute(
        "SELECT title, status, division FROM solicitation WHERE document_number='3303123110'"
    ).fetchone()
    assert sol is not None
    assert sol["status"] == "Awarded"
    # Spine wins the CONTESTED field: OData's division survives CKAN's conflicting backfill value.
    assert sol["division"] == "Purchasing & Materials Management"
    # (title is an OData-only field; present as a sanity check, not a conflict test.)
    assert sol["title"] == "Toner Cartridges"

    # The doc links across sources: both OData and CKAN award rows exist (dual provenance).
    sources = {r["source"] for r in conn.execute(
        "SELECT source FROM award WHERE document_number='3303123110'"
    )}
    assert "odata" in sources
    assert "ckan_awarded" in sources


def test_ariba_fetch_normalize_upsert_bridges_and_archives(conn):
    from toronto_bids.sources.ariba import normalize_posting
    from toronto_bids.store import db

    search = _load("ariba_search_record.json")
    detail = _load("ariba_detail.json")
    # Simulate the fetch output: one detail-200 posting, one detail-500 posting.
    raws = [
        {"search": search, "detail": detail},
        {"search": {**search, "rfxID": "1110099999", "title": "no doc here"}, "detail": None},
    ]
    for raw in raws:
        for row in normalize_posting(raw):
            db.upsert_row(conn, row, overwrite=True)
    conn.commit()

    assert db.counts(conn)["ariba_posting"] == 2
    bridged = conn.execute(
        "SELECT document_number, raw_json FROM ariba_posting WHERE rfx_id='1110015885'"
    ).fetchone()
    assert bridged["document_number"] == "5672751291"   # linked to the OData/CKAN spine
    assert bridged["raw_json"] is not None               # snapshot archived
    unbridged = conn.execute(
        "SELECT document_number, raw_json FROM ariba_posting WHERE rfx_id='1110099999'"
    ).fetchone()
    assert unbridged["document_number"] is None          # archived even though un-bridged
    assert unbridged["raw_json"] is None
