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
