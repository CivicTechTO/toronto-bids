"""Backfill parseable numeric amounts and apply human verdicts from amount_labels.toml.

Human verdicts write only to *_labelled and *_verdict, never *_numeric.
"""
import tomllib
from importlib import resources

from toronto_bids.amount import parse_amount

_AMOUNT_COLUMNS = [("award", "award_amount"), ("noncompetitive", "contract_amount")]

VERDICTS = frozenset({"amount", "not_an_amount", "corrupt", "unknown", "not_an_award"})


def load_labels(path=None) -> dict:
    """Load and validate amount_labels.toml (or path)."""
    if path is None:
        text = resources.files("toronto_bids.data").joinpath("amount_labels.toml").read_text()
    else:
        text = open(path).read()
    labels = tomllib.loads(text)
    for raw, label in labels.items():
        verdict = label.get("verdict")
        if verdict not in VERDICTS:
            raise ValueError(f"amount_labels: {raw!r} has unknown verdict {verdict!r}; "
                             f"expected one of {sorted(VERDICTS)}")
        if verdict == "amount" and not isinstance(label.get("value"), (int, float)):
            raise ValueError(f"amount_labels: {raw!r} is verdict 'amount' but has no "
                             f"numeric `value`")
        if verdict != "amount" and "value" in label:
            raise ValueError(f"amount_labels: {raw!r} is verdict {verdict!r} and must not "
                             f"carry a `value` — only 'amount' resolves to a number")
    return labels


def backfill_numeric_amounts(conn) -> int:
    """Populate NULL *_numeric columns wherever parse_amount succeeds. Idempotent."""
    filled = 0
    for table, column in _AMOUNT_COLUMNS:
        updates = []
        for row in conn.execute(
                f"SELECT rowid AS rid, {column} AS raw FROM {table} "
                f"WHERE {column} IS NOT NULL AND TRIM({column}) != '' "
                f"AND {column}_numeric IS NULL"):
            value = parse_amount(row["raw"])
            if value is not None:
                updates.append((value, row["rid"]))
        conn.executemany(f"UPDATE {table} SET {column}_numeric = ? WHERE rowid = ?", updates)
        filled += len(updates)
    conn.commit()
    return filled


def apply_amount_labels(conn, labels=None) -> int:
    """Write human verdicts into *_labelled and *_verdict by exact raw string match. Idempotent."""
    labels = load_labels() if labels is None else labels
    labelled = 0
    for table, column in _AMOUNT_COLUMNS:
        rows = [(label.get("value"), label["verdict"], raw)
                for raw, label in labels.items()]
        cursor = conn.executemany(
            f"UPDATE {table} SET {column}_labelled = ?, {column}_verdict = ? "
            f"WHERE {column} = ?", rows)
        labelled += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    conn.commit()
    return labelled


def unlabelled_amounts(conn) -> list:
    """Return unparseable, unlabelled raw amount strings (assumes backfill_numeric_amounts ran)."""
    labels = load_labels()
    out = []
    for table, column in _AMOUNT_COLUMNS:
        for row in conn.execute(
                f"SELECT {column} AS raw, COUNT(*) AS rows FROM {table} "
                f"WHERE {column} IS NOT NULL AND TRIM({column}) != '' "
                f"AND {column}_numeric IS NULL GROUP BY 1 ORDER BY 1"):
            if row["raw"] not in labels:
                out.append({"table": table, "raw": row["raw"], "rows": row["rows"]})
    return out
