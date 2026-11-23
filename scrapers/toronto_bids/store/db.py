import sqlite3
from contextlib import closing
from dataclasses import fields
from importlib import resources

from toronto_bids.models import (
    AgencyAward,
    AgencyBid,
    AgencySolicitation,
    PortalListing,
    AribaAttachment,
    AribaPosting,
    Award,
    BackgroundPdf,
    Bid,
    BidScope,
    BidResponse,
    BidResponsePrice,
    Buyer,
    CapitalProject,
    CompositeAward,
    CouncilItem,
    NonCompetitive,
    Solicitation,
    Supplier,
    SuspendedFirm,
)

# Model -> (table, key_cols). DB-side columns (id, first_seen, last_seen, supplier_id, title_source)
# are omitted from models so upsert_row never overwrites them.
_TABLES = {
    BidScope: ("bid_scope", ["reference", "source", "scope_key"]),
    BidResponse: ("bid_response", ["scope_id", "response_key"]),
    BidResponsePrice: ("bid_response_price", ["response_id", "price_key"]),
    Solicitation: ("solicitation", ["document_number"]),
    NonCompetitive: ("noncompetitive", ["workspace_number"]),
    Award: (
        "award",
        [
            "document_number",
            "supplier_name_raw",
            "award_amount",
            "award_date",
            "source",
        ],
    ),
    AribaPosting: ("ariba_posting", ["rfx_id"]),
    AribaAttachment: ("ariba_attachment", ["document_number", "path"]),
    SuspendedFirm: ("suspended_firm", ["supplier_name_raw", "council_authority"]),
    Supplier: ("supplier", ["supplier_key"]),
    CouncilItem: ("council_item", ["reference"]),
    BackgroundPdf: ("background_pdf", ["url"]),
    CapitalProject: ("capital_project", ["name"]),
    Bid: (
        "bid",
        ["reference", "document_number", "bidder_name_raw", "bid_price", "source"],
    ),
    CompositeAward: (
        "composite_award",
        ["call_number", "supplier_name_raw", "award_value", "source"],
    ),
    Buyer: ("buyer", ["slug"]),
    PortalListing: ("portal_listing", ["buyer_id", "portal_url"]),
    AgencySolicitation: ("agency_solicitation", ["buyer_id", "native_ref"]),
    AgencyAward: (
        "agency_award",
        ["buyer_id", "native_ref", "supplier_name_raw", "award_amount", "source"],
    ),
    AgencyBid: ("agency_bid", ["buyer_id", "native_ref", "bidder_name_raw", "source"]),
}

# Expression-index conflict targets; must match the COALESCE unique indexes in schema.sql.
_CONFLICT_TARGETS = {
    "award": "document_number, supplier_name_raw, "
    "COALESCE(award_amount, ''), COALESCE(award_date, ''), source",
    "bid": "COALESCE(reference, ''), COALESCE(document_number, ''), bidder_name_raw, "
    "COALESCE(bid_price, ''), source",
    "composite_award": "call_number, COALESCE(supplier_name_raw, ''), "
    "COALESCE(award_value, ''), source",
    "agency_award": "buyer_id, native_ref, COALESCE(supplier_name_raw, ''), "
    "COALESCE(award_amount, ''), source",
}

# Published table set shared by export and counts().
EXPORT_TABLES = [
    "pdf_text",
    "solicitation",
    "award",
    "noncompetitive",
    "ariba_posting",
    "suspended_firm",
    "supplier",
    "capital_project",
    "bid",
    "council_item",
    "background_pdf",
    "composite_award",
    "sync_run",
    "buyer",
    "agency_solicitation",
    "agency_award",
    "agency_bid",
    "ariba_attachment",
    "solicitation_link",
    "city_crosswalk_evidence",
    "city_bid_evidence",
    "city_bulk_bid_evidence",
    "city_native_bid_evidence",
    "city_document_alias_evidence",
    "city_scoped_bid_evidence",
    "bid_scope",
    "bid_response",
    "bid_response_price",
    "portal_listing",
    "procurement",
    "procurement_key",
    "procurement_document",
    "procurement_council_item",
    "award_publication",
    "published_award",
    "publication_extraction",
    "merx_notice",
    "merx_capture",
]


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn) -> None:
    schema = resources.files("toronto_bids.store").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    # Add procurement_id with its FK clause before _add_missing_columns (which cannot add REFERENCES).
    from toronto_bids.linking.procurement import FACT_TABLES
    for table in FACT_TABLES:
        if 'procurement_id' not in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN procurement_id INTEGER REFERENCES procurement(id)')
    _add_missing_columns(conn, schema)
    _rebuild_award_for_line_key(conn, schema)
    _upgrade_bid_tables(conn, schema)
    _rebuild_ariba_attachment_for_path(conn, schema)
    conn.commit()


def _upgrade_bid_tables(conn, schema: str) -> None:
    legacy_bid = any(col["name"] == "reference" and col["notnull"]
                     for col in conn.execute("PRAGMA table_info(bid)"))
    broken_response = any(fk["from"] == "bid_id" and fk["table"] == "_bid_pre114"
                          for fk in conn.execute("PRAGMA foreign_key_list(bid_response)"))
    if not (legacy_bid or broken_response):
        return

    with closing(sqlite3.connect(":memory:")) as canonical:
        canonical.executescript(schema)
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if legacy_bid:
                    _replace_bid_table(conn, canonical, "bid")
                if broken_response:
                    _replace_bid_table(conn, canonical, "bid_response")
                    conn.execute("""
                        UPDATE bid_response SET bid_id = NULL
                        WHERE bid_id IS NOT NULL AND NOT EXISTS (
                            SELECT 1 FROM bid WHERE bid.id = bid_response.bid_id
                        )
                    """)
                for table in ("bid", "bid_response", "bid_response_price"):
                    if conn.execute(f"PRAGMA foreign_key_check({table})").fetchone() is not None:
                        raise sqlite3.IntegrityError(f"Foreign key violation after upgrading {table}")
        finally:
            conn.execute(f"PRAGMA foreign_keys = {foreign_keys}")


def _replace_bid_table(conn, canonical, table: str) -> None:
    # Rename the replacement, so SQLite never rewrites references to the original table.
    replacement = f"_new_{table}"
    ddl = canonical.execute("SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?", (table,)).fetchone()[0]
    conn.execute(ddl.replace(f"CREATE TABLE {table} (", f"CREATE TABLE {replacement} (", 1))
    columns = ", ".join(row["name"] for row in conn.execute(f"PRAGMA table_info({table})"))
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
    conn.execute(f"INSERT INTO {replacement} ({columns}) SELECT {columns} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {replacement} RENAME TO {table}")
    if sequence is not None:
        conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?", (sequence[0], table))
    for (sql,) in canonical.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (table,)
    ):
        conn.execute(sql)


def _rebuild_award_for_line_key(conn, schema: str) -> bool:
    """Rebuild legacy award table to drop table-level UNIQUE in favor of award_line_key, preserving first_seen. Idempotent."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='award'"
    ).fetchone()
    if row is None or "UNIQUE" not in (row["sql"] or "").upper():
        return False

    cols = [r[1] for r in conn.execute("PRAGMA table_info(award)")]
    quoted = ", ".join(cols)
    conn.executescript("PRAGMA foreign_keys = OFF;")
    try:
        conn.execute("ALTER TABLE award RENAME TO _award_pre73")
        # Drop renamed index first so schema.sql recreates award_line_key on the new table.
        conn.execute("DROP INDEX IF EXISTS award_line_key")
        conn.executescript(schema)
        conn.execute(f"INSERT INTO award ({quoted}) SELECT {quoted} FROM _award_pre73")
        conn.execute("DROP TABLE _award_pre73")
        conn.commit()
    finally:
        conn.executescript("PRAGMA foreign_keys = ON;")
    return True


def _rebuild_ariba_attachment_for_path(conn, schema: str) -> bool:
    """Rebuild legacy ariba_attachment table to key on (document_number, path) instead of filename, preserving first_seen. Idempotent."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ariba_attachment'"
    ).fetchone()
    if row is None or "document_number, filename)" not in (row["sql"] or ""):
        return False

    cols = [r[1] for r in conn.execute("PRAGMA table_info(ariba_attachment)")]
    quoted = ", ".join(cols)
    conn.executescript("PRAGMA foreign_keys = OFF;")
    try:
        conn.execute("ALTER TABLE ariba_attachment RENAME TO _ariba_attachment_pre123")
        conn.executescript(schema)
        conn.execute(
            f"INSERT INTO ariba_attachment ({quoted}) "
            f"SELECT {quoted} FROM _ariba_attachment_pre123"
        )
        conn.execute("DROP TABLE _ariba_attachment_pre123")
    finally:
        conn.executescript("PRAGMA foreign_keys = ON;")
    return True


def _add_missing_columns(conn, schema: str) -> None:
    """Additively apply nullable or defaulted columns from schema.sql missing in an older DB."""
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(schema)
        ref_tables = [
            r[0]
            for r in ref.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        for table in ref_tables:
            actual = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not actual:
                continue
            for _cid, name, coltype, notnull, default, _pk in ref.execute(
                f"PRAGMA table_info({table})"
            ):
                if name in actual:
                    continue
                if notnull and default is None:
                    continue  # ADD COLUMN requires a default for NOT NULL
                decl = f"{name} {coltype}".strip()
                if notnull:
                    decl += " NOT NULL"
                if default is not None:
                    decl += f" DEFAULT {default}"
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {decl}")
                except sqlite3.OperationalError:
                    # Skip columns ADD COLUMN cannot retrofit (non-constant defaults, UNIQUE, PRIMARY KEY).
                    continue
    finally:
        ref.close()


def _upsert_keyed(conn, table, cols, values, key_cols, overwrite: bool) -> None:
    placeholders = ", ".join("?" for _ in cols)
    non_key = [c for c in cols if c not in key_cols]
    sets = {}
    for column in non_key:
        existing, incoming = f"{table}.{column}", f"excluded.{column}"
        first, fallback = (incoming, existing) if overwrite else (existing, incoming)
        sets[column] = f"COALESCE({first}, {fallback})"
    if table == "noncompetitive":
        # The derived fields belong to the raw amount that wins the upsert.
        use_incoming = "excluded.contract_amount IS NOT NULL"
        if not overwrite:
            use_incoming += " AND noncompetitive.contract_amount IS NULL"
        sets["contract_amount_numeric"] = (
            f"CASE WHEN {use_incoming} THEN excluded.contract_amount_numeric "
            "ELSE noncompetitive.contract_amount_numeric END"
        )
        changed = f"{sets['contract_amount']} IS NOT noncompetitive.contract_amount"
        for column in ("contract_amount_labelled", "contract_amount_verdict"):
            sets[column] = f"CASE WHEN {changed} THEN NULL ELSE noncompetitive.{column} END"
    if table == "ariba_posting" and overwrite:
        # Search estimates are converted, so replace the entire range only when its source wins.
        use_incoming_range = """
            excluded.currency IS NOT NULL
            OR (CASE WHEN json_valid(excluded.raw_json)
                     THEN json_extract(excluded.raw_json, '$.upperLimit_amount') END) IS NOT NULL
            OR (ariba_posting.currency IS NULL
                AND (ariba_posting.raw_json IS NULL
                     OR (ariba_posting.amount_min IS NULL AND ariba_posting.amount_max IS NULL))
                AND (excluded.amount_min IS NOT NULL OR excluded.amount_max IS NOT NULL))
        """
        for column in ("amount_min", "amount_max", "currency"):
            sets[column] = f"CASE WHEN {use_incoming_range} THEN excluded.{column} ELSE ariba_posting.{column} END"
    assignments = ", ".join(f"{column} = {expr}" for column, expr in sets.items())
    conflict = _CONFLICT_TARGETS.get(table) or ", ".join(key_cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {assignments}, last_seen = datetime('now')"
    )
    conn.execute(sql, values)


def upsert_row(conn, row, *, overwrite: bool) -> None:
    table, key_cols, cols, values = _row_values(row)
    _upsert_keyed(conn, table, cols, values, key_cols, overwrite)


def _row_values(row):
    """Return (table, key_cols, cols, values) for a model row."""
    try:
        table, key_cols = _TABLES[type(row)]
    except KeyError:
        raise TypeError(f"Cannot upsert row of type {type(row).__name__}") from None
    cols = [f.name for f in fields(row)]
    values = [getattr(row, c) for c in cols]
    if table == "suspended_firm":
        # Coerce NULL council_authority to '' so UNIQUE(supplier_name_raw, council_authority) stays idempotent.
        ca_idx = cols.index("council_authority")
        values[ca_idx] = values[ca_idx] or ""
    return table, key_cols, cols, values


_TOUCHED = "temp._rebuild_touched"


def _rebuild_upsert(conn, row) -> None:
    """Upsert one row for rebuild_rows and record its id in _TOUCHED.

    First touch replaces non-key columns verbatim (including NULL); later touches in the same
    run merge with COALESCE(excluded, existing). Preserves id and first_seen.
    """
    table, key_cols, cols, values = _row_values(row)
    non_key = [c for c in cols if c not in key_cols]
    seen = f"{table}.id IN (SELECT id FROM {_TOUCHED})"
    sets = ", ".join(
        f"{c} = CASE WHEN {seen} THEN COALESCE(excluded.{c}, {table}.{c}) "
        f"ELSE excluded.{c} END"
        for c in non_key
    )
    conflict = _CONFLICT_TARGETS.get(table) or ", ".join(key_cols)
    (row_id,) = conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {sets}, last_seen = datetime('now') "
        f"RETURNING id",
        values,
    ).fetchall()[0]
    conn.execute(f"INSERT OR IGNORE INTO {_TOUCHED} (id) VALUES (?)", (row_id,))


def rebuild_rows(conn, rows, *, scope: str, params=()) -> int:
    """Replace rows matching scope via mark-and-sweep, preserving id and first_seen for retained keys.

    rows must be non-empty and of one model type. Does not commit.
    """
    tables = {_row_values(r)[0] for r in rows}
    if len(tables) != 1:
        raise ValueError(f"rebuild_rows needs rows of exactly one table, got {tables}")
    (table,) = tables
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _rebuild_touched (id INTEGER PRIMARY KEY)")
    conn.execute(f"DELETE FROM {_TOUCHED}")
    try:
        for r in rows:
            _rebuild_upsert(conn, r)
        return conn.execute(
            f"DELETE FROM {table} WHERE ({scope}) "
            f"AND id NOT IN (SELECT id FROM {_TOUCHED})",
            tuple(params),
        ).rowcount
    finally:
        conn.execute(f"DELETE FROM {_TOUCHED}")


def counts(conn) -> dict:
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in EXPORT_TABLES
    }


def last_runs(conn) -> list:
    """Return the most recent sync_run row per source."""
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


def finish_sync_run(
    conn, run_id, *, status, rows_fetched=0, rows_upserted=0, error=None
) -> None:
    conn.execute(
        "UPDATE sync_run SET finished_at = datetime('now'), status = ?, "
        "rows_fetched = ?, rows_upserted = ?, error = ? WHERE id = ?",
        (status, rows_fetched, rows_upserted, error, run_id),
    )
    conn.commit()


def sync_runs_since(conn, after_id: int) -> list[dict]:
    """Return sync_run rows with id > after_id, ordered by id."""
    cur = conn.execute(
        "SELECT source, status, rows_fetched, rows_upserted, error "
        "FROM sync_run WHERE id > ? ORDER BY id",
        (after_id,),
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def is_extracted(conn, sha256: str, extractor_version: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM extraction_cache WHERE sha256=? AND extractor_version=?",
        (sha256, extractor_version),
    ).fetchone()
    return row is not None


def mark_extracted(
    conn, sha256: str, extractor_version: str, result_json: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO extraction_cache (sha256, extractor_version, result_json) "
        "VALUES (?, ?, ?) ON CONFLICT(sha256, extractor_version) DO UPDATE "
        "SET result_json=excluded.result_json, extracted_at=datetime('now')",
        (sha256, extractor_version, result_json),
    )
    conn.commit()


def get_extraction(conn, sha256: str, extractor_version: str) -> str | None:
    row = conn.execute(
        "SELECT result_json FROM extraction_cache "
        "WHERE sha256=? AND extractor_version=?",
        (sha256, extractor_version),
    ).fetchone()
    return row["result_json"] if row else None
