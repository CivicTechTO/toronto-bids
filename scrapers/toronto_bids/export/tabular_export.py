import csv
import io
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from toronto_bids.store import db

# Large non-analytical blobs omitted from tabular exports (local_path is also excluded).
_EXCLUDE_COLUMNS = {
    ("ariba_posting", "raw_json"),
    ("background_pdf", "text"),
}


def _kept_columns(conn, table: str) -> list[str]:
    return [
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})")
        if (table, row[1]) not in _EXCLUDE_COLUMNS and row[1] != "local_path"
    ]


def _read_table(conn, table: str) -> tuple[list[str], list[tuple]]:
    """Return kept columns and rows for one table as plain tuples, ordered by column 1."""
    cols = _kept_columns(conn, table)
    col_list = ", ".join(cols)
    rows = [tuple(r) for r in conn.execute(f"SELECT {col_list} FROM {table} ORDER BY 1")]
    return cols, rows


def write_csv_zip(conn, out_path) -> Path:
    """Write one <table>.csv per EXPORT_TABLES into a single zip archive."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in db.EXPORT_TABLES:
            cols, rows = _read_table(conn, table)
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(cols)
            writer.writerows(rows)
            zf.writestr(f"{table}.csv", buf.getvalue())
    return out_path


def _arrow_type(sqlite_type: str) -> pa.DataType:
    """Map a SQLite declared column type to a stable Arrow type."""
    upper = (sqlite_type or "").upper()
    if "INT" in upper:
        return pa.int64()
    if any(token in upper for token in ("REAL", "FLOA", "DOUB", "NUM")):
        return pa.float64()
    if "BLOB" in upper:
        return pa.binary()
    return pa.string()


def _table_schema(conn, table: str) -> pa.Schema:
    fields = [
        pa.field(row[1], _arrow_type(row[2]), nullable=True)
        for row in conn.execute(f"PRAGMA table_info({table})")
        if (table, row[1]) not in _EXCLUDE_COLUMNS and row[1] != "local_path"
    ]
    return pa.schema(fields)


def write_parquet_files(conn, out_dir) -> list[Path]:
    """Write one <table>.parquet per EXPORT_TABLES into out_dir with stable column types."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for table in db.EXPORT_TABLES:
        cols, rows = _read_table(conn, table)
        schema = _table_schema(conn, table)
        data = {col: [row[i] for row in rows] for i, col in enumerate(cols)}
        arrow_table = pa.table(data, schema=schema)
        path = out_dir / f"{table}.parquet"
        pq.write_table(arrow_table, path)
        written.append(path)
    return written
