import sqlite3
from dataclasses import fields
from importlib import resources

from toronto_bids.models import Award, CapitalProject, NonCompetitive, Solicitation, AribaPosting, SuspendedFirm, Supplier, CouncilItem, BackgroundPdf

# model -> (table, conflict-key columns). A model's fields ARE the table's writable
# columns, in INSERT order; auto/default columns (id, first_seen, last_seen, supplier_id)
# are DB-side only and deliberately absent from the models.
_TABLES = {
    Solicitation: ("solicitation", ["document_number"]),
    NonCompetitive: ("noncompetitive", ["workspace_number"]),
    Award: ("award", ["document_number", "supplier_name_raw", "award_amount",
                      "award_date", "source"]),
    AribaPosting: ("ariba_posting", ["rfx_id"]),
    SuspendedFirm: ("suspended_firm", ["supplier_name_raw", "council_authority"]),
    Supplier: ("supplier", ["supplier_key"]),
    CouncilItem: ("council_item", ["reference"]),
    BackgroundPdf: ("background_pdf", ["url"]),
    CapitalProject: ("capital_project", ["name"]),
}

# Tables whose uniqueness is enforced by an expression index rather than a column list, so
# ON CONFLICT must name the same expressions. award's key COALESCEs its nullable parts
# because SQLite treats NULLs as distinct — see the award_line_key comment in schema.sql.
_CONFLICT_TARGETS = {
    "award": "document_number, supplier_name_raw, "
             "COALESCE(award_amount, ''), COALESCE(award_date, ''), source",
}


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn) -> None:
    schema = resources.files("toronto_bids.store").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    _add_missing_columns(conn, schema)
    _rebuild_award_for_line_key(conn, schema)
    conn.commit()


def _rebuild_award_for_line_key(conn, schema: str) -> bool:
    """Drop award's old table-level UNIQUE so award_line_key governs instead (#73).

    `_add_missing_columns` only ADDs columns; it cannot remove a constraint, and
    `CREATE TABLE IF NOT EXISTS` never alters an existing table. A database built before #73
    therefore keeps `UNIQUE (document_number, supplier_name_raw, source)` — which silently
    discards every award line after the first for a (document, supplier), and would reject the
    additional lines the next sync tries to insert. So the table needs a genuine rebuild.

    Rows are copied rather than re-fetched so `first_seen` survives: it is archive metadata
    recording when we first saw a row, and no feed can tell us that again. The lines that were
    already dropped cannot be recovered here — the next sync re-reads the feed and inserts them.

    Returns True if a rebuild happened. Idempotent: a no-op once the old UNIQUE is gone.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='award'").fetchone()
    if row is None or "UNIQUE" not in (row["sql"] or "").upper():
        return False                        # fresh DB, or already rebuilt

    cols = [r[1] for r in conn.execute("PRAGMA table_info(award)")]
    quoted = ", ".join(cols)
    conn.executescript("PRAGMA foreign_keys = OFF;")
    try:
        conn.execute("ALTER TABLE award RENAME TO _award_pre73")
        # The rename drags award_line_key along with the old table, and CREATE INDEX
        # IF NOT EXISTS would then skip it by name — leaving the rebuilt table with no
        # unique index at all. Free the name first.
        conn.execute("DROP INDEX IF EXISTS award_line_key")
        # Recreating from schema.sql keeps one definition of the table, rather than a second
        # copy of the DDL drifting here. Every statement is IF NOT EXISTS, so re-running the
        # whole script only materialises the award table we just renamed away.
        conn.executescript(schema)
        conn.execute(f"INSERT INTO award ({quoted}) SELECT {quoted} FROM _award_pre73")
        conn.execute("DROP TABLE _award_pre73")
        conn.commit()
    finally:
        conn.executescript("PRAGMA foreign_keys = ON;")
    return True


def _add_missing_columns(conn, schema: str) -> None:
    """Additively self-heal an older database.

    `CREATE TABLE IF NOT EXISTS` never alters a table that already exists, so a
    database created before a column was added to schema.sql (e.g. suspended_firm.
    supplier_id, added in P5a) silently lacks that column. Build the reference schema
    in a scratch in-memory DB, then `ALTER TABLE ... ADD COLUMN` any column present in
    the reference but missing from the live table. Only additive, nullable-or-defaulted
    columns are handled — exactly what ADD COLUMN can safely apply.
    """
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(schema)
        ref_tables = [r[0] for r in ref.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for table in ref_tables:
            actual = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not actual:
                continue  # table didn't exist — the CREATE above already made it current
            for _cid, name, coltype, notnull, default, _pk in ref.execute(
                    f"PRAGMA table_info({table})"):
                if name in actual:
                    continue
                if notnull and default is None:
                    continue  # ADD COLUMN can't add NOT NULL without a default
                decl = f"{name} {coltype}".strip()
                if notnull:
                    decl += " NOT NULL"
                if default is not None:
                    decl += f" DEFAULT {default}"
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {decl}")
                except sqlite3.OperationalError:
                    # ADD COLUMN also refuses non-constant defaults (e.g. datetime('now')),
                    # UNIQUE, and PRIMARY KEY. Such columns can't be retrofitted onto an
                    # existing table; they predate any realistic legacy DB anyway. Skip.
                    continue
    finally:
        ref.close()


def _upsert_keyed(conn, table, cols, values, key_cols, overwrite: bool) -> None:
    placeholders = ", ".join("?" for _ in cols)
    non_key = [c for c in cols if c not in key_cols]
    if overwrite:
        # New non-null value wins; keep existing when the new value is NULL.
        sets = ", ".join(f"{c} = COALESCE(excluded.{c}, {table}.{c})" for c in non_key)
    else:
        # Backfill only: keep existing value; fill in only where existing is NULL.
        sets = ", ".join(f"{c} = COALESCE({table}.{c}, excluded.{c})" for c in non_key)
    conflict = _CONFLICT_TARGETS.get(table) or ", ".join(key_cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {sets}, last_seen = datetime('now')"
    )
    conn.execute(sql, values)


def upsert_row(conn, row, *, overwrite: bool) -> None:
    try:
        table, key_cols = _TABLES[type(row)]
    except KeyError:
        raise TypeError(f"Cannot upsert row of type {type(row).__name__}") from None
    cols = [f.name for f in fields(row)]
    values = [getattr(row, c) for c in cols]
    if table == "suspended_firm":
        # council_authority is part of the UNIQUE key; coerce None -> '' so a firm with no
        # parseable Authority stays idempotent (SQLite treats NULLs as distinct in UNIQUE indexes).
        ca_idx = cols.index("council_authority")
        values[ca_idx] = values[ca_idx] or ""
    _upsert_keyed(conn, table, cols, values, key_cols, overwrite)


def counts(conn) -> dict:
    tables = ["solicitation", "award", "noncompetitive", "ariba_posting",
              "suspended_firm", "supplier", "capital_project", "council_item",
              "background_pdf", "sync_run"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def last_runs(conn) -> list:
    """The most recent sync_run per source, newest source-run first."""
    return conn.execute(
        "SELECT source, status, started_at, rows_upserted, error FROM sync_run "
        "WHERE id IN (SELECT MAX(id) FROM sync_run GROUP BY source) ORDER BY source"
    ).fetchall()


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
