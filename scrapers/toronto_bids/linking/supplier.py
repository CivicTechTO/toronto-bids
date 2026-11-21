import json
import re

from toronto_bids.models import Supplier
from toronto_bids.store import db

_SUBMITTED_BY = re.compile(r"\(\s*submitted by:.*?\)", re.IGNORECASE)
_NON_KEY = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")

# Strip appendix, price-form, and trailing non-compliant wrappers.
_NOISE = re.compile(
    r'\(?\s*appendix\s*["“‘\']?\s*[a-z]?\d*\s*["”’\']?\s*[–-]?\s*(?:price\s*form)?\s*\)?',
    re.IGNORECASE)
_NONCOMPLIANT = re.compile(r'\*?\s*non-?compliant.*$', re.IGNORECASE)

# Anchored footnote prefixes (not real firm names).
_FOOTNOTE = re.compile(
    r"^(?:please see|see prequalif|see the|refer to|as per|various\b|bid prices|"
    r"\d+\s*/?\s*bidder was found|the scope of work|\d+\s+bidder\b|part [a-z]:|"
    r"corrected for mathematical|award amounts have been)", re.IGNORECASE)

# Corporation number (6-7 digits) adjacent to a province token.
_CORP = re.compile(r"\b(\d{6,7})\s+(?:ontario|ont|canada|quebec|qc)\b", re.IGNORECASE)

# Leading 6-7 digit corp number before a trade-name marker, keyed as #<number>.
_LEADING_CORP = re.compile(r"^(\d{6,7})\b")

# Trailing trade-name marker and everything after it.
_MARK = re.compile(
    r"\s*(?:,\s*)?(?:\bo/?a\b|\b0/a\b|\boperating as\b|\bc\.?o\.?b\.?(?:\s*as)?\b|"
    r"\bd\.?b\.?a\.?\b|\btrading as\b|\bt/a\b)\b.*$", re.IGNORECASE)
_LEGAL_SUFFIX = {"inc", "ltd", "limited", "incorporated", "corp", "corporation", "co",
                 "company", "canada", "ontario", "ont", "llp", "lp", "the", "and"}
_GENERIC_BASE = {"ontario ltd", "ontario limited", "ontario inc", "ontario incorporated",
                 "ontario", "inc", "ltd", "limited", "incorporated", "canada inc",
                 "canada ltd", "canada"}


def _normalize(text: str) -> str:
    """Drop Submitted-by notes, lowercase, strip punctuation, and collapse whitespace (keeps legal suffixes)."""
    text = _SUBMITTED_BY.sub(" ", str(text))
    text = _NON_KEY.sub(" ", text.lower())
    return _WS.sub(" ", text).strip()


def _salvage(text: str) -> str:
    """Strip appendix, price-form, and non-compliant wrappers from a raw name."""
    text = _NONCOMPLIANT.sub(" ", str(text))
    text = _NOISE.sub(" ", text)
    return _WS.sub(" ", text).strip(' *"')


def supplier_key(raw: str | None) -> str:
    """Compute a deterministic supplier key (see docs/supplier-entity-resolution.md).

    Order: salvage noise and drop pure footnotes -> #<corp_no> (with province token) ->
    trade-name legal base (or #<corp_no> if leading digits, skipping generic bases) ->
    conservative normalization. Returns "" for blank or footnote strings.
    """
    if raw is None:
        return ""
    salvaged = _salvage(raw)
    if not salvaged or _FOOTNOTE.search(salvaged):
        return ""
    m = _CORP.search(salvaged)
    if m:
        return f"#{m.group(1)}"
    if _MARK.search(salvaged):
        base = _normalize(_MARK.sub("", salvaged))
        cm = _LEADING_CORP.match(base)
        if cm:
            return f"#{cm.group(1)}"
        if base and base not in _GENERIC_BASE and any(t not in _LEGAL_SUFFIX for t in base.split()):
            return base
    return _normalize(salvaged)


# (table, pk_column) for all tables carrying supplier names and supplier_id.
_SUPPLIER_TABLES = [
    ("award", "id"),
    ("noncompetitive", "workspace_number"),
    ("suspended_firm", "id"),
    ("bid", "id"),
    ("composite_award", "id"),
    ("agency_award", "id"),
    ("agency_bid", "id"),
    ("published_award", "source_key"),
]

_NAME_COLUMN = {"bid": "bidder_name_raw", "agency_bid": "bidder_name_raw"}


def build_supplier_dimension(conn) -> int:
    """Rebuild the supplier dimension and backfill supplier_id FKs across source tables. Idempotent."""
    variants_by_key: dict[str, set] = {}
    row_keys: list[tuple[str, object, str]] = []
    for table, pk in _SUPPLIER_TABLES:
        name_col = _NAME_COLUMN.get(table, "supplier_name_raw")
        for row in conn.execute(f"SELECT {pk} AS pk, {name_col} AS supplier_name_raw FROM {table}"):
            raw = row["supplier_name_raw"]
            key = supplier_key(raw)
            if not key:
                continue
            variants_by_key.setdefault(key, set()).add(raw)
            row_keys.append((table, row["pk"], key))

    for key, variants in variants_by_key.items():
        ordered = sorted(variants)
        db.upsert_row(
            conn,
            Supplier(supplier_key=key, display_name=ordered[0], variants=json.dumps(ordered)),
            overwrite=True,
        )

    # Delete orphan supplier rows by explicit id list to avoid SQLite parameter limits.
    live = set(variants_by_key)
    stale_ids = [r["supplier_id"] for r in conn.execute("SELECT supplier_id, supplier_key FROM supplier")
                 if r["supplier_key"] not in live]
    conn.executemany("DELETE FROM supplier WHERE supplier_id = ?", [(i,) for i in stale_ids])

    # Clear stale supplier_id FKs before backfilling matched rows.
    for table, _pk in _SUPPLIER_TABLES:
        conn.execute(f"UPDATE {table} SET supplier_id = NULL")

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
