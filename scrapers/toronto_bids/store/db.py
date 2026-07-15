import sqlite3
from importlib import resources

from toronto_bids.models import Award, NonCompetitive, Solicitation, AribaPosting, SuspendedFirm, Supplier

# Column lists per table, in the order used for INSERT. Excludes auto/default columns.
_SOLICITATION_COLS = [
    "document_number", "status", "rfx_type", "noip_type", "form_type", "title", "description",
    "issue_date", "submission_deadline", "category", "division", "buyer_name",
    "buyer_email", "buyer_phone", "wards", "ariba_posting_link", "odata_id", "source",
]
_NONCOMP_COLS = [
    "workspace_number", "supplier_name_raw", "reason", "contract_amount",
    "contract_date", "division", "council_authority_link", "odata_id", "source",
]
_ARIBA_POSTING_COLS = [
    "rfx_id", "document_number", "title", "posting_type", "status", "customer_name",
    "posted_date", "close_date", "categories", "amount_min", "amount_max", "currency",
    "public_posting_url", "sourcing_url", "external_rfx_id", "raw_json", "source",
]
_SUSPENDED_COLS = [
    "supplier_name_raw", "status", "start_date", "end_date",
    "suspension_type", "council_authority", "source",
]
_SUPPLIER_COLS = ["supplier_key", "display_name", "variants"]


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn) -> None:
    schema = resources.files("toronto_bids.store").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    conn.commit()


def _upsert_keyed(conn, table, cols, values, key_cols, overwrite: bool) -> None:
    placeholders = ", ".join("?" for _ in cols)
    non_key = [c for c in cols if c not in key_cols]
    if overwrite:
        # New non-null value wins; keep existing when the new value is NULL.
        sets = ", ".join(f"{c} = COALESCE(excluded.{c}, {table}.{c})" for c in non_key)
    else:
        # Backfill only: keep existing value; fill in only where existing is NULL.
        sets = ", ".join(f"{c} = COALESCE({table}.{c}, excluded.{c})" for c in non_key)
    conflict = ", ".join(key_cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {sets}, last_seen = datetime('now')"
    )
    conn.execute(sql, values)


def upsert_row(conn, row, *, overwrite: bool) -> None:
    if isinstance(row, Solicitation):
        values = [getattr(row, c) for c in _SOLICITATION_COLS]
        _upsert_keyed(conn, "solicitation", _SOLICITATION_COLS, values,
                      ["document_number"], overwrite)
    elif isinstance(row, NonCompetitive):
        values = [getattr(row, c) for c in _NONCOMP_COLS]
        _upsert_keyed(conn, "noncompetitive", _NONCOMP_COLS, values,
                      ["workspace_number"], overwrite)
    elif isinstance(row, Award):
        cols = ["document_number", "supplier_name_raw", "award_amount", "award_date", "source"]
        values = [getattr(row, c) for c in cols]
        _upsert_keyed(conn, "award", cols, values,
                      ["document_number", "supplier_name_raw", "source"], overwrite)
    elif isinstance(row, AribaPosting):
        values = [getattr(row, c) for c in _ARIBA_POSTING_COLS]
        _upsert_keyed(conn, "ariba_posting", _ARIBA_POSTING_COLS, values,
                      ["rfx_id"], overwrite)
    elif isinstance(row, SuspendedFirm):
        values = [getattr(row, c) for c in _SUSPENDED_COLS]
        # council_authority is part of the UNIQUE key; coerce None -> '' so a firm with no
        # parseable Authority stays idempotent (SQLite treats NULLs as distinct in UNIQUE indexes).
        ca_idx = _SUSPENDED_COLS.index("council_authority")
        values[ca_idx] = values[ca_idx] or ""
        _upsert_keyed(conn, "suspended_firm", _SUSPENDED_COLS, values,
                      ["supplier_name_raw", "council_authority"], overwrite)
    elif isinstance(row, Supplier):
        values = [getattr(row, c) for c in _SUPPLIER_COLS]
        _upsert_keyed(conn, "supplier", _SUPPLIER_COLS, values, ["supplier_key"], overwrite)
    else:
        raise TypeError(f"Cannot upsert row of type {type(row).__name__}")


def counts(conn) -> dict:
    tables = ["solicitation", "award", "noncompetitive", "ariba_posting",
              "suspended_firm", "supplier", "sync_run"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def start_sync_run(conn, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO sync_run (source, started_at, status) VALUES (?, datetime('now'), 'running')",
        (source,),
    )
    conn.commit()
    return cur.lastrowid


def finish_sync_run(conn, run_id, *, status, rows_fetched=0, rows_upserted=0, error=None) -> None:
    conn.execute(
        "UPDATE sync_run SET finished_at = datetime('now'), status = ?, "
        "rows_fetched = ?, rows_upserted = ?, error = ? WHERE id = ?",
        (status, rows_fetched, rows_upserted, error, run_id),
    )
    conn.commit()
