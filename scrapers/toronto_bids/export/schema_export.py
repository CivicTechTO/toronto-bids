from datetime import datetime, timezone

from toronto_bids.store import db


def build_schema_document(conn, generated_at: str | None = None) -> dict:
    """A column-level data dictionary generated from the SQLite (#168).

    Pure and deterministic given `generated_at`: PRAGMA table_info + COUNT(*) per table,
    no file I/O. type/nullable/primary_key come from the declared schema; row_count from
    the live data. Descriptions (curated) and enums (observed) are layered on in later steps.
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()

    tables: dict[str, dict] = {}
    for table in db.EXPORT_TABLES:
        columns = []
        for row in conn.execute(f"PRAGMA table_info({table})"):
            # row: (cid, name, type, notnull, dflt_value, pk)
            col: dict = {"name": row[1], "nullable": not row[3]}
            if row[2]:
                col["type"] = row[2]
            if row[5]:
                col["primary_key"] = True
            columns.append(col)
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        tables[table] = {"row_count": row_count, "columns": columns}

    return {"generated_at": generated_at, "tables": tables}
