import csv
import io
import zipfile

import pyarrow.parquet as pq

from toronto_bids.export.tabular_export import _read_table, write_csv_zip, write_parquet_files
from toronto_bids.models import AribaPosting, Award, Solicitation
from toronto_bids.store import db


def test_read_table_excludes_blob_and_path_columns(conn):
    cols, _ = _read_table(conn, "ariba_posting")
    assert "raw_json" not in cols          # giant blob excluded
    assert "rfx_id" in cols                # keys kept
    cols_pdf, _ = _read_table(conn, "background_pdf")
    assert "text" not in cols_pdf          # giant blob excluded
    assert "local_path" not in cols_pdf    # server path excluded
    assert "url" in cols_pdf


def test_read_table_orders_by_first_column(conn):
    db.upsert_row(conn, Solicitation(document_number="2000000002", source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation(document_number="1000000001", source="odata"), overwrite=True)
    conn.commit()
    cols, rows = _read_table(conn, "solicitation")
    assert cols[0] == "document_number"
    assert [r[0] for r in rows] == ["1000000001", "2000000002"]


def test_write_csv_zip_has_one_csv_per_table(conn, tmp_path):
    out = write_csv_zip(conn, tmp_path / "bids-csv.zip")
    assert out == tmp_path / "bids-csv.zip"
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert names == {f"{t}.csv" for t in db.EXPORT_TABLES}


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
    # award_date was never set -> empty field, not the string "None"
    assert row[header.index("award_date")] == ""
    assert row[header.index("supplier_name_raw")] == "Acme"


def test_excluded_column_absent_from_csv_header(conn, tmp_path):
    out = write_csv_zip(conn, tmp_path / "bids-csv.zip")
    with zipfile.ZipFile(out) as zf:
        header = zf.read("ariba_posting.csv").decode("utf-8").splitlines()[0]
    assert "raw_json" not in header


def test_write_parquet_one_file_per_table(conn, tmp_path):
    paths = write_parquet_files(conn, tmp_path)
    names = {p.name for p in paths}
    assert names == {f"{t}.parquet" for t in db.EXPORT_TABLES}
    for p in paths:
        assert p.exists()


def test_parquet_roundtrips_rows_and_types(conn, tmp_path):
    # A REAL-with-NULL column (award_amount_numeric) and a TEXT column round-trip.
    db.upsert_row(conn, Award(document_number="1000000001", supplier_name_raw="Acme",
                              award_amount="1000",
                              source="odata"), overwrite=True)  # numeric derived from "1000"
    db.upsert_row(conn, Award(document_number="1000000002", supplier_name_raw="Beta",
                              award_amount="kj", source="odata"), overwrite=True)  # numeric NULL
    conn.commit()
    write_parquet_files(conn, tmp_path)
    table = pq.read_table(tmp_path / "award.parquet").to_pylist()
    by_supplier = {r["supplier_name_raw"]: r for r in table}
    assert by_supplier["Acme"]["award_amount_numeric"] == 1000.0
    assert by_supplier["Beta"]["award_amount_numeric"] is None


def test_parquet_omits_excluded_columns(conn, tmp_path):
    write_parquet_files(conn, tmp_path)
    ap = pq.read_table(tmp_path / "ariba_posting.parquet")
    assert "raw_json" not in ap.column_names
    bp = pq.read_table(tmp_path / "background_pdf.parquet")
    assert "text" not in bp.column_names
    assert "local_path" not in bp.column_names
