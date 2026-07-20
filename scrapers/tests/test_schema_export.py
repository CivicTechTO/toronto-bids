from toronto_bids.export.schema_export import build_schema_document
from toronto_bids.models import Award, Solicitation
from toronto_bids.store import db


def test_all_tables_present_and_ordered(conn):
    doc = build_schema_document(conn, generated_at="2026-07-20T00:00:00Z")
    assert doc["generated_at"] == "2026-07-20T00:00:00Z"
    assert list(doc["tables"].keys()) == db.EXPORT_TABLES


def test_column_type_nullable_and_pk(conn):
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["solicitation"]["columns"]}
    assert cols["document_number"]["type"] == "TEXT"
    assert cols["document_number"]["primary_key"] is True
    # first_seen is declared NOT NULL -> nullable False; status is nullable
    assert cols["first_seen"]["nullable"] is False
    assert cols["status"]["nullable"] is True
    assert "primary_key" not in cols["status"]


def test_row_count_matches(conn):
    db.upsert_row(conn, Solicitation(document_number="5672751291", status="Open",
                                     source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5672751291", supplier_name_raw="Acme",
                              award_amount="1000", source="odata"), overwrite=True)
    conn.commit()
    doc = build_schema_document(conn, generated_at="t")
    assert doc["tables"]["solicitation"]["row_count"] == 1
    assert doc["tables"]["award"]["row_count"] == 1
    assert doc["tables"]["bid"]["row_count"] == 0


def test_enum_is_observed_sorted_distinct(conn):
    db.upsert_row(conn, Solicitation(document_number="1000000001", status="Open",
                                     source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation(document_number="1000000002", status="Awarded",
                                     source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation(document_number="1000000003", status="Awarded",
                                     source="odata"), overwrite=True)
    conn.commit()
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["solicitation"]["columns"]}
    assert cols["status"]["enum"] == ["Awarded", "Open"]


def test_enum_omitted_when_no_values(conn):
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["solicitation"]["columns"]}
    assert "enum" not in cols["status"]  # empty table -> no observed values


def test_non_enum_column_never_gets_enum(conn):
    db.upsert_row(conn, Solicitation(document_number="1000000001", title="Real Title",
                                     source="odata"), overwrite=True)
    conn.commit()
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["solicitation"]["columns"]}
    assert "enum" not in cols["title"]  # title is not a declared coded column
