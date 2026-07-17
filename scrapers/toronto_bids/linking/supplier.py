import json
import re

from toronto_bids.models import Supplier
from toronto_bids.store import db

_SUBMITTED_BY = re.compile(r"\(\s*submitted by:.*?\)", re.IGNORECASE)
_NON_KEY = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def supplier_key(raw: str | None) -> str:
    """Deterministic grouping key for a raw supplier name.

    Drops a trailing "(Submitted by: …)" note, lowercases, removes every character
    that is not [a-z0-9 ], and collapses whitespace. Legal suffixes (Inc, Ltd, …) are
    intentionally kept so genuinely different entities are not merged. Returns "" for
    blank/garbage input (caller skips those).
    """
    if raw is None:
        return ""
    text = _SUBMITTED_BY.sub(" ", str(raw))
    text = _NON_KEY.sub(" ", text.lower())
    return _WS.sub(" ", text).strip()


# (source table, its primary-key column) for the tables carrying a supplier name + supplier_id.
# `bid` is the odd one: it names losing bidders, so most of its 4,751 names never appear in
# award at all and the dimension roughly doubles. That is the point — a supplier dimension
# built only from winners cannot answer who lost, who only ever bids unopposed, or whether a
# suspended firm kept bidding (#87).
_SUPPLIER_TABLES = [
    ("award", "id"),
    ("noncompetitive", "workspace_number"),
    ("suspended_firm", "id"),
    ("bid", "id"),
    # 2009-2012 winners, from years the City's feed barely covers (#96). Firms that only ever
    # won pre-Ariba would otherwise be absent from the dimension entirely.
    ("composite_award", "id"),
]

# bid names its supplier in a different column from the other three.
_NAME_COLUMN = {"bid": "bidder_name_raw"}


def build_supplier_dimension(conn) -> int:
    """Build/refresh the supplier dimension and backfill supplier_id FKs. Idempotent.

    Returns the number of distinct suppliers.
    """
    # 1. Collect every (row pk, raw name, key) and group raw names by key.
    variants_by_key: dict[str, set] = {}
    row_keys: list[tuple[str, object, str]] = []  # (table, pk, key)
    for table, pk in _SUPPLIER_TABLES:
        name_col = _NAME_COLUMN.get(table, "supplier_name_raw")
        for row in conn.execute(f"SELECT {pk} AS pk, {name_col} AS supplier_name_raw FROM {table}"):
            raw = row["supplier_name_raw"]
            key = supplier_key(raw)
            if not key:
                continue
            variants_by_key.setdefault(key, set()).add(raw)
            row_keys.append((table, row["pk"], key))

    # 2. Upsert one supplier per key (deterministic display_name + variants).
    for key, variants in variants_by_key.items():
        ordered = sorted(variants)
        db.upsert_row(
            conn,
            Supplier(supplier_key=key, display_name=ordered[0], variants=json.dumps(ordered)),
            overwrite=True,
        )

    # 3. Recompute FKs from scratch each run: clear all, then set the matched rows below.
    # (A row whose name blanked out since last run must lose its stale supplier_id.)
    for table, _pk in _SUPPLIER_TABLES:
        conn.execute(f"UPDATE {table} SET supplier_id = NULL")

    # 4. Map key -> supplier_id, then backfill the FK on each source row.
    id_by_key = {r["supplier_key"]: r["supplier_id"]
                 for r in conn.execute("SELECT supplier_key, supplier_id FROM supplier")}
    pk_by_table = dict(_SUPPLIER_TABLES)
    for table, row_pk, key in row_keys:
        conn.execute(
            f"UPDATE {table} SET supplier_id = ? WHERE {pk_by_table[table]} = ?",
            (id_by_key[key], row_pk),
        )
    conn.commit()
    return len(variants_by_key)
