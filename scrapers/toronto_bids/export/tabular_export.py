import csv
import io
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from toronto_bids.store import db

# Columns dropped from the bulk tabular export. The two blobs would dominate file size and are
# not analytical (the nested JSON export drops them too); `local_path` is a server filesystem
# path. Everything else — ids, keys, checksums, City identifiers — is kept as a join key.
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
    """The kept columns and rows of one table, ORDER BY the first column (deterministic).

    Rows are plain tuples so both csv.writer and pyarrow consume them without sqlite3.Row quirks.
    """
    cols = _kept_columns(conn, table)
    col_list = ", ".join(cols)
    rows = [tuple(r) for r in conn.execute(f"SELECT {col_list} FROM {table} ORDER BY 1")]
    return cols, rows


def write_csv_zip(conn, out_path) -> Path:
    """One `<table>.csv` per EXPORT_TABLES, bundled into a single zip. NULL renders as an empty
    field (csv default). For the non-technical / Excel audience (#161)."""
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


def write_parquet_files(conn, out_dir) -> list[Path]:
    """One `<table>.parquet` per EXPORT_TABLES in out_dir. Arrow infers each column's type from
    its values (an all-NULL column becomes Arrow null type, which Parquet stores fine). For the
    DuckDB/pandas/Polars audience — the files are individually HTTP-queryable (#161)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for table in db.EXPORT_TABLES:
        cols, rows = _read_table(conn, table)
        data = {col: [row[i] for row in rows] for i, col in enumerate(cols)}
        arrow_table = pa.table(data)
        path = out_dir / f"{table}.parquet"
        pq.write_table(arrow_table, path)
        written.append(path)
    return written
