import json

from toronto_bids.export import schema_export
from toronto_bids.export.json_export import export_schema
from toronto_bids.export.schema_export import (
    build_manifest_document,
    build_schema_document,
    load_descriptions,
)
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
    assert "enum" not in cols["status"]


def test_non_enum_column_never_gets_enum(conn):
    db.upsert_row(conn, Solicitation(document_number="1000000001", title="Real Title",
                                     source="odata"), overwrite=True)
    conn.commit()
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["solicitation"]["columns"]}
    assert "enum" not in cols["title"]


def test_description_applied_where_present(conn):
    doc = build_schema_document(conn, generated_at="t")
    cols = {c["name"]: c for c in doc["tables"]["award"]["columns"]}
    assert "verbatim" in cols["award_amount"]["description"]
    assert "description" not in cols["first_seen"]


def test_every_dictionary_key_and_enum_column_resolves_to_a_real_column(conn):
    real_pairs = {
        (table, row[1])
        for table in db.EXPORT_TABLES
        for row in conn.execute(f"PRAGMA table_info({table})")
    }
    real_keys = {f"{table}.{col}" for table, col in real_pairs}
    for key in load_descriptions():
        assert key in real_keys, f"schema_dictionary.toml key {key!r} is not a real column"
    for pair in schema_export._ENUM_COLUMNS:
        assert pair in real_pairs, f"_ENUM_COLUMNS entry {pair!r} is not a real column"


def test_export_schema_writes_valid_json(conn, tmp_path):
    out = tmp_path / "schema.json"
    written = export_schema(conn, out, generated_at="2026-07-20T00:00:00Z")
    assert written == out
    doc = json.loads(out.read_text())
    assert doc["generated_at"] == "2026-07-20T00:00:00Z"
    assert "solicitation" in doc["tables"]


def test_manifest_sizes_and_order(tmp_path):
    a = tmp_path / "bids.json"
    a.write_bytes(b"x" * 10)
    b = tmp_path / "bids.json.gz"
    b.write_bytes(b"y" * 3)
    doc = build_manifest_document([a, b], generated_at="t")
    assert doc["generated_at"] == "t"
    assert doc["artifacts"] == [
        {"name": "bids.json", "bytes": 10},
        {"name": "bids.json.gz", "bytes": 3},
    ]


def test_manifest_omits_missing_file(tmp_path):
    a = tmp_path / "bids.json"
    a.write_bytes(b"x" * 5)
    missing = tmp_path / "nope.sqlite"
    doc = build_manifest_document([a, missing], generated_at="t")
    assert [x["name"] for x in doc["artifacts"]] == ["bids.json"]
