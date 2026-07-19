import json
import re

from toronto_bids.models import Supplier
from toronto_bids.store import db

_SUBMITTED_BY = re.compile(r"\(\s*submitted by:.*?\)", re.IGNORECASE)
_NON_KEY = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")

# Rule 3 salvage: strip appendix / price-form / trailing non-compliant noise wrappers.
_NOISE = re.compile(
    r'\(?\s*appendix\s*["“‘\']?\s*[a-z]?\d*\s*["”’\']?\s*[–-]?\s*(?:price\s*form)?\s*\)?',
    re.IGNORECASE)
_NONCOMPLIANT = re.compile(r'\*?\s*non-?compliant.*$', re.IGNORECASE)

# Rule 3 exclude: pure footnote (no firm identity). Anchored — matches only strings that START
# with a footnote phrase, so a real firm name is never excluded.
_FOOTNOTE = re.compile(
    r"^(?:please see|see prequalif|see the|refer to|as per|various\b|bid prices|"
    r"\d+\s*/?\s*bidder was found|the scope of work|\d+\s+bidder\b|part [a-z]:|"
    r"corrected for mathematical|award amounts have been)", re.IGNORECASE)

# Rule 1: a corporation number (6-7 digits) adjacent to a province token, anywhere in the name.
_CORP = re.compile(r"\b(\d{6,7})\s+(?:ontario|ont|canada|quebec|qc)\b", re.IGNORECASE)

# Rule 2: a trailing trade-name marker (and everything after it).
_MARK = re.compile(
    r"\s*(?:,\s*)?(?:\bo/?a\b|\b0/a\b|\boperating as\b|\bc\.?o\.?b\.?(?:\s*as)?\b|"
    r"\bd\.?b\.?a\.?\b|\btrading as\b|\bt/a\b)\b.*$", re.IGNORECASE)
_LEGAL_SUFFIX = {"inc", "ltd", "limited", "incorporated", "corp", "corporation", "co",
                 "company", "canada", "ontario", "ont", "llp", "lp", "the", "and"}
_GENERIC_BASE = {"ontario ltd", "ontario limited", "ontario inc", "ontario incorporated",
                 "ontario", "inc", "ltd", "limited", "incorporated", "canada inc",
                 "canada ltd", "canada"}


def _normalize(text: str) -> str:
    """Today's conservative key: drop a Submitted-by note, lowercase, strip non-[a-z0-9 ],
    collapse whitespace. Legal suffixes are intentionally kept so different named firms don't
    merge."""
    text = _SUBMITTED_BY.sub(" ", str(text))
    text = _NON_KEY.sub(" ", text.lower())
    return _WS.sub(" ", text).strip()


def _salvage(text: str) -> str:
    """Rule 3 salvage: strip appendix/price-form/non-compliant noise wrappers so a real firm
    wrapped in scraped footnote noise (e.g. 'Fermar Paving Limited* Non-compliant') survives."""
    text = _NONCOMPLIANT.sub(" ", str(text))
    text = _NOISE.sub(" ", text)
    return _WS.sub(" ", text).strip(' *"')


def supplier_key(raw: str | None) -> str:
    """Deterministic entity-resolution key for a raw supplier name.

    Staged (first match wins), see docs/supplier-entity-resolution.md:
      1. salvage noise wrappers, then exclude pure footnote -> "" (caller skips).
      2. a corporation number adjacent to a province token, anywhere -> "#<number>"
         (the number IS the legal identity; Inc/Ltd/Incorporated/O-A/JV are noise).
      3. a trailing trade-name marker on a NON-numbered name -> the legal base, but only when
         the base is not generic (guards the 'ontario ltd' over-merge).
      4. otherwise today's conservative normalization (legal suffix kept).
    Returns "" for blank/garbage. Pure and total; never raises.
    """
    if raw is None:
        return ""
    salvaged = _salvage(raw)                                   # Rule 3 salvage
    if not salvaged or _FOOTNOTE.search(salvaged):             # Rule 3 exclude
        return ""
    m = _CORP.search(salvaged)                                 # Rule 1
    if m:
        return f"#{m.group(1)}"
    if _MARK.search(salvaged):                                 # Rule 2 (guarded)
        base = _normalize(_MARK.sub("", salvaged))
        if base and base not in _GENERIC_BASE and any(t not in _LEGAL_SUFFIX for t in base.split()):
            return base
    return _normalize(salvaged)                                # Rule 4 default


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
    # Agency buyers (#135): winners AND losing bidders, so cross-buyer supplier behaviour
    # (a suspended firm bidding at the Zoo; a firm that loses downtown and wins at TRCA)
    # is queryable at all. Same rationale as `bid` (#87).
    ("agency_award", "id"),
    ("agency_bid", "id"),
]

# bid and agency_bid name their supplier in a different column from the others.
_NAME_COLUMN = {"bid": "bidder_name_raw", "agency_bid": "bidder_name_raw"}


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
