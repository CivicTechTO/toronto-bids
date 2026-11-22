import csv
import io
import zipfile

import pyarrow.parquet as pq

from toronto_bids.export.tabular_export import _read_table, write_csv_zip, write_parquet_files
from toronto_bids.models import Award, Solicitation
from toronto_bids.store import db


def test_read_table_excludes_blob_and_path_columns(conn):
    cols, _ = _read_table(conn, "ariba_posting")
    assert "raw_json" not in cols
    assert "rfx_id" in cols
    cols_pdf, _ = _read_table(conn, "background_pdf")
    assert "text" not in cols_pdf
    assert "local_path" not in cols_pdf
    assert "url" in cols_pdf


def test_read_table_orders_by_first_column(conn):
    db.upsert_row(conn, Solicitation(document_number="2000000002", source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation(document_number="1000000001", source="odata"), overwrite=True)
    conn.commit()
    cols, rows = _read_table(conn, "solicitation")
    assert cols[0] == "document_number"
    assert [r[0] for r in rows] == ["1000000001", "2000000002"]


def test_write_csv_zip_has_one_csv_per_table_and_omits_excluded_columns(conn, tmp_path):
    out = write_csv_zip(conn, tmp_path / "bids-csv.zip")
    assert out == tmp_path / "bids-csv.zip"
    with zipfile.ZipFile(out) as zf:
        assert set(zf.namelist()) == {f"{t}.csv" for t in db.EXPORT_TABLES}
        header = zf.read("ariba_posting.csv").decode("utf-8").splitlines()[0]
    assert "raw_json" not in header


def test_csv_has_header_and_null_is_empty(conn, tmp_path):
    db.upsert_row(conn, Award(document_number="1000000001", supplier_name_raw="Acme",
                              award_amount="1000", source="odata"), overwrite=True)
    conn.commit()
    out = write_csv_zip(conn, tmp_path / "bids-csv.zip")
    with zipfile.ZipFile(out) as zf:
        text = zf.read("award.csv").decode("utf-8")
    reader = list(csv.reader(io.StringIO(text)))
    header = reader[0]
    assert "supplier_name_raw" in header
    row = reader[1]
    assert row[header.index("award_date")] == ""
    assert row[header.index("supplier_name_raw")] == "Acme"


def test_write_parquet_one_file_per_table_and_omits_excluded_columns(conn, tmp_path):
    paths = write_parquet_files(conn, tmp_path)
    names = {p.name for p in paths}
    assert names == {f"{t}.parquet" for t in db.EXPORT_TABLES}
    for p in paths:
        assert p.exists()
    ap = pq.read_table(tmp_path / "ariba_posting.parquet")
    assert "raw_json" not in ap.column_names
    bp = pq.read_table(tmp_path / "background_pdf.parquet")
    assert "text" not in bp.column_names
    assert "local_path" not in bp.column_names


def test_parquet_roundtrips_rows_and_types(conn, tmp_path):
    db.upsert_row(conn, Award(document_number="1000000001", supplier_name_raw="Acme",
                              award_amount="1000", source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="1000000002", supplier_name_raw="Beta",
                              award_amount="kj", source="odata"), overwrite=True)
    conn.commit()
    write_parquet_files(conn, tmp_path)
    table = pq.read_table(tmp_path / "award.parquet").to_pylist()
    by_supplier = {r["supplier_name_raw"]: r for r in table}
    assert by_supplier["Acme"]["award_amount_numeric"] == 1000.0
    assert by_supplier["Beta"]["award_amount_numeric"] is None


def test_parquet_column_types_stay_stable_across_empty_all_null_and_populated_exports(conn, tmp_path):
    import pyarrow as pa

    empty_dir = tmp_path / "empty"
    write_parquet_files(conn, empty_dir)
    empty_award = pq.read_schema(empty_dir / "award.parquet")
    assert empty_award.field("award_amount_numeric").type == pa.float64()
    assert empty_award.field("supplier_id").type == pa.int64()
    assert empty_award.field("supplier_name_raw").type == pa.string()

    # Insert an award with NULL numeric & NULL supplier_id (all-null columns).
    db.upsert_row(conn, Award(document_number="1000000001", supplier_name_raw="Null Co",
                              award_amount="not a number", source="odata"), overwrite=True)
    conn.commit()
    null_dir = tmp_path / "all_null"
    write_parquet_files(conn, null_dir)
    null_award = pq.read_schema(null_dir / "award.parquet")
    assert null_award == empty_award

    # Now add a populated numeric award: schema stays identical across all three exports.
    db.upsert_row(conn, Award(document_number="1000000002", supplier_name_raw="Num Co",
                              award_amount="1234.50", source="odata"), overwrite=True)
    conn.commit()
    pop_dir = tmp_path / "populated"
    write_parquet_files(conn, pop_dir)
    pop_award = pq.read_schema(pop_dir / "award.parquet")
    assert pop_award == empty_award == null_award

    # Verify integer, real, text, and blob SQLite types all round-trip with nulls preserved.
    conn.execute("CREATE TABLE _blob_check (id INTEGER, score REAL, label TEXT, raw BLOB)")
    conn.execute("INSERT INTO _blob_check VALUES (1, 2.5, 'ok', X'00FF'), (NULL, NULL, NULL, NULL)")
    conn.commit()
    from toronto_bids.export import tabular_export as te
    schema = te._table_schema(conn, "_blob_check")
    assert [f.type for f in schema] == [pa.int64(), pa.float64(), pa.string(), pa.binary()]
    cols, rows = te._read_table(conn, "_blob_check")
    tbl = pa.table({c: [r[i] for r in rows] for i, c in enumerate(cols)}, schema=schema)
    out = tmp_path / "_blob_check.parquet"
    pq.write_table(tbl, out)
    rt = pq.read_table(out).to_pylist()
    assert {"id": 1, "score": 2.5, "label": "ok", "raw": b"\x00\xff"} in rt
    assert {"id": None, "score": None, "label": None, "raw": None} in rt
