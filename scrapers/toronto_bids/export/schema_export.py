import tomllib
from datetime import datetime, timezone
from importlib import resources

from toronto_bids.store import db

# Coded columns whose observed value set is worth publishing as an enum. Declared (not
# auto-detected by cardinality) so `title` — hundreds of distinct values but not coded —
# is never mistaken for one. Enums are OBSERVED (SELECT DISTINCT), so a new City value
# appears automatically (the drift #168 exists to prevent).
_ENUM_COLUMNS = {
    ("solicitation", "status"),
    ("solicitation", "rfx_type"),
    ("solicitation", "form_type"),
    ("solicitation", "title_source"),
    ("award", "award_amount_verdict"),
    ("award", "source"),
    ("noncompetitive", "contract_amount_verdict"),
    ("bid", "hst_basis"),
    ("bid", "source"),
    ("suspended_firm", "status"),
    ("sync_run", "status"),
    ("background_pdf", "kind"),
    ("agency_award", "value_confidential"),
}


def _observed_enum(conn, table: str, column: str) -> list:
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM {table} "
        f"WHERE {column} IS NOT NULL ORDER BY {column}"
    ).fetchall()
    return [r[0] for r in rows]


def load_descriptions(text: str | None = None) -> dict[str, str]:
    """The curated column dictionary, keyed '<table>.<column>' -> gloss (#168)."""
    if text is None:
        text = resources.files("toronto_bids.data").joinpath("schema_dictionary.toml").read_text()
    return {key: entry["description"] for key, entry in tomllib.loads(text).items()}


def build_schema_document(conn, generated_at: str | None = None) -> dict:
    """A column-level data dictionary generated from the SQLite (#168).

    Pure and deterministic given `generated_at`: PRAGMA table_info + COUNT(*) per table,
    no file I/O. type/nullable/primary_key come from the declared schema; row_count from
    the live data. Descriptions (curated) and enums (observed) are layered on in later steps.
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()

    descriptions = load_descriptions()

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
            if (table, row[1]) in _ENUM_COLUMNS:
                values = _observed_enum(conn, table, row[1])
                if values:
                    col["enum"] = values
            gloss = descriptions.get(f"{table}.{row[1]}")
            if gloss:
                col["description"] = gloss
            columns.append(col)
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        tables[table] = {"row_count": row_count, "columns": columns}

    return {"generated_at": generated_at, "tables": tables}
