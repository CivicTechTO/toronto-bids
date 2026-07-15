import json

from toronto_bids.sources import ckan, odata
from toronto_bids.store import db
from tests.conftest import FIXTURES


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_odata_spine_wins_and_ckan_links_on_document_number(conn):
    # OData spine first (overwrite=True)
    for raw in _load("odata_solicitation.json")["value"]:
        for row in odata.normalize_solicitation(raw):
            db.upsert_row(conn, row, overwrite=True)
    # CKAN awarded backfills (overwrite=False)
    for raw in _load("ckan_awarded.json")["result"]["records"]:
        for row in ckan.normalize_awarded(raw):
            db.upsert_row(conn, row, overwrite=False)
    conn.commit()

    sol = conn.execute(
        "SELECT title, status FROM solicitation WHERE document_number='3303123110'"
    ).fetchone()
    assert sol is not None
    assert sol["title"] == "Toner Cartridges"          # OData spine value wins
    assert sol["status"] == "Awarded"

    # The doc links across sources: both OData and CKAN award rows exist (dual provenance).
    sources = {r["source"] for r in conn.execute(
        "SELECT source FROM award WHERE document_number='3303123110'"
    )}
    assert "odata" in sources
    assert "ckan_awarded" in sources
