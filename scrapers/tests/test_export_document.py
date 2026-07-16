import json

import pytest

from toronto_bids.export.document import build_export_document
from toronto_bids.models import AribaPosting, Award, NonCompetitive, Solicitation, SuspendedFirm
from toronto_bids.store import db


@pytest.fixture
def seeded(conn):
    # One solicitation with an award and a bridged Ariba posting.
    db.upsert_row(conn, Solicitation(document_number="5672751291", status="Open",
                                     title="RFT Watermain", source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5672751291", supplier_name_raw="Acme Co",
                              award_amount="1000", source="odata"), overwrite=True)
    db.upsert_row(conn, AribaPosting(rfx_id="1110015885", document_number="5672751291",
                                     title="RFT Watermain", categories='["Sidewalk"]',
                                     raw_json='{"big":"blob"}', source="ariba_discovery"), overwrite=True)
    # An Ariba posting that never bridged (document_number is NULL).
    db.upsert_row(conn, AribaPosting(rfx_id="1110099999", document_number=None,
                                     title="Unbridged posting", categories='["Water"]',
                                     source="ariba_discovery"), overwrite=True)
    # A non-competitive contract (separate keyspace).
    db.upsert_row(conn, NonCompetitive(workspace_number="8614", supplier_name_raw="Sole Source Inc",
                                       reason="Emergency", source="odata"), overwrite=True)
    conn.commit()
    return conn


def test_meta_has_generated_at_counts_and_sources(seeded):
    db.finish_sync_run(seeded, db.start_sync_run(seeded, "odata_solicitations"),
                       status="ok", rows_fetched=5, rows_upserted=5)
    doc = build_export_document(seeded, generated_at="2026-07-15T00:00:00Z")
    assert doc["meta"]["generated_at"] == "2026-07-15T00:00:00Z"
    assert doc["meta"]["counts"]["solicitation"] == 1
    assert doc["meta"]["sources"][-1]["source"] == "odata_solicitations"
    assert doc["meta"]["sources"][-1]["status"] == "ok"


def test_award_and_posting_nested_under_solicitation(seeded):
    doc = build_export_document(seeded, generated_at="t")
    sols = doc["solicitations"]
    assert len(sols) == 1
    sol = sols[0]
    assert sol["document_number"] == "5672751291"
    assert "odata_id" not in sol
    # award nested, internal cols dropped, redundant document_number dropped
    assert len(sol["awards"]) == 1
    assert sol["awards"][0]["supplier_name_raw"] == "Acme Co"
    assert "id" not in sol["awards"][0]
    assert "document_number" not in sol["awards"][0]
    # ariba posting nested, raw_json dropped, categories parsed to a list
    assert len(sol["ariba_postings"]) == 1
    assert sol["ariba_postings"][0]["rfx_id"] == "1110015885"
    assert "raw_json" not in sol["ariba_postings"][0]
    assert sol["ariba_postings"][0]["categories"] == ["Sidewalk"]
    assert "document_number" not in sol["ariba_postings"][0]


def test_unbridged_posting_goes_to_unlinked_not_dropped(seeded):
    doc = build_export_document(seeded, generated_at="t")
    unlinked = doc["unlinked_ariba_postings"]
    assert len(unlinked) == 1
    assert unlinked[0]["rfx_id"] == "1110099999"
    assert unlinked[0]["categories"] == ["Water"]
    assert "raw_json" not in unlinked[0]
    # It must NOT appear under any solicitation.
    assert all(p["rfx_id"] != "1110099999"
               for s in doc["solicitations"] for p in s["ariba_postings"])


def test_noncompetitive_is_separate_top_level(seeded):
    doc = build_export_document(seeded, generated_at="t")
    assert len(doc["noncompetitive"]) == 1
    assert doc["noncompetitive"][0]["workspace_number"] == "8614"
    # supplier_id is retained for joining to suppliers[]
    assert "supplier_id" in doc["noncompetitive"][0]


def test_document_is_json_serializable(seeded):
    doc = build_export_document(seeded, generated_at="t")
    json.dumps(doc)  # must not raise


def test_empty_store_produces_empty_collections(conn):
    doc = build_export_document(conn, generated_at="t")
    assert doc["solicitations"] == []
    assert doc["noncompetitive"] == []
    assert doc["unlinked_ariba_postings"] == []
    assert doc["unlinked_awards"] == []
    assert doc["meta"]["counts"]["solicitation"] == 0


def test_orphan_posting_docnum_not_in_solicitation_goes_to_unlinked(seeded):
    # A posting bridged to a doc number that matches NO solicitation must NOT vanish.
    db.upsert_row(seeded, AribaPosting(rfx_id="1110088888", document_number="4044346425",
                                       title="Mock RFT", source="ariba_discovery"), overwrite=True)
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    orphan = [p for p in doc["unlinked_ariba_postings"] if p["rfx_id"] == "1110088888"]
    assert len(orphan) == 1
    assert orphan[0]["document_number"] == "4044346425"   # kept for diagnostics
    # and it must NOT be nested under any solicitation
    assert all(p["rfx_id"] != "1110088888"
               for s in doc["solicitations"] for p in s["ariba_postings"])


def test_orphan_award_docnum_not_in_solicitation_goes_to_unlinked_awards(seeded):
    db.upsert_row(seeded, Award(document_number="4044346425", supplier_name_raw="Orphan Co",
                                source="ckan_awarded"), overwrite=True)
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    orphan = [a for a in doc["unlinked_awards"] if a["supplier_name_raw"] == "Orphan Co"]
    assert len(orphan) == 1
    assert orphan[0]["document_number"] == "4044346425"


def test_no_record_is_dropped_counts_reconcile(seeded):
    # Every posting/award is either nested or unlinked — nested + unlinked == db count.
    db.upsert_row(seeded, AribaPosting(rfx_id="1110088888", document_number="4044346425",
                                       source="ariba_discovery"), overwrite=True)
    db.upsert_row(seeded, Award(document_number="4044346425", supplier_name_raw="Orphan Co",
                                source="ckan_awarded"), overwrite=True)
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    counts = doc["meta"]["counts"]
    nested_postings = sum(len(s["ariba_postings"]) for s in doc["solicitations"])
    assert nested_postings + len(doc["unlinked_ariba_postings"]) == counts["ariba_posting"]
    nested_awards = sum(len(s["awards"]) for s in doc["solicitations"])
    assert nested_awards + len(doc["unlinked_awards"]) == counts["award"]


def test_suspended_firms_is_separate_top_level(conn):
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                                      council_authority="2025.GG19.17", source="suspended_firms"),
                  overwrite=True)
    conn.commit()
    doc = build_export_document(conn, generated_at="t")
    assert len(doc["suspended_firms"]) == 1
    firm = doc["suspended_firms"][0]
    assert firm["supplier_name_raw"] == "Duron Ontario Ltd."
    assert firm["council_authority"] == "2025.GG19.17"
    assert "id" not in firm


def test_suspended_firms_empty_when_none(conn):
    doc = build_export_document(conn, generated_at="t")
    assert doc["suspended_firms"] == []


def test_export_has_suppliers_array_and_retains_supplier_id(conn):
    from toronto_bids.models import Award, Solicitation, Supplier
    from toronto_bids.store import db as _db
    _db.upsert_row(conn, Supplier(supplier_key="compugen inc", display_name="Compugen Inc.",
                                  variants='["Compugen Inc."]'), overwrite=True)
    sid = conn.execute("SELECT supplier_id FROM supplier WHERE supplier_key='compugen inc'").fetchone()[0]
    _db.upsert_row(conn, Solicitation(document_number="3303123110", source="odata"), overwrite=True)
    _db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="Compugen Inc.",
                               source="odata"), overwrite=True)
    conn.execute("UPDATE award SET supplier_id=? WHERE document_number='3303123110'", (sid,))
    conn.commit()

    doc = build_export_document(conn, generated_at="t")
    assert len(doc["suppliers"]) == 1
    assert doc["suppliers"][0]["display_name"] == "Compugen Inc."
    # supplier_id is retained on nested awards so consumers can join to suppliers[]
    award = doc["solicitations"][0]["awards"][0]
    assert award["supplier_id"] == sid


def test_export_suppliers_empty_when_none(conn):
    doc = build_export_document(conn, generated_at="t")
    assert doc["suppliers"] == []
