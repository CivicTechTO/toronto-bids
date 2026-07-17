"""Apply human verdicts to the amounts the parser refuses, and keep the queue honest (#74).

Two passes, deliberately separate:

`backfill_numeric_amounts` recomputes *_numeric wherever it is NULL but the raw string does
parse. That is not a parser fix — the models derive the number on upsert — it is archive
semantics catching up with a code change. Rows are never deleted, so a row written before
amount.py existed keeps its NULL forever unless the feed happens to re-upsert that exact key;
2,856 noncompetitive rows and 1 award row were in that state, and `numeric IS NULL` therefore
did NOT mean "not machine-parseable" at all. It has to, because that is the review queue this
whole file depends on.

`apply_amount_labels` then writes the human verdicts from data/amount_labels.toml into
*_labelled / *_verdict — never into *_numeric. See the schema comment for why the tiers stay
apart.
"""
import tomllib
from importlib import resources

from toronto_bids.amount import parse_amount

# (table, raw column). Both keyspaces carry an amount the City publishes as text.
_AMOUNT_COLUMNS = [("award", "award_amount"), ("noncompetitive", "contract_amount")]

VERDICTS = frozenset({"amount", "not_an_amount", "corrupt", "unknown", "not_an_award"})


def load_labels(path=None) -> dict:
    """The label file, validated. Raises rather than silently mislabelling.

    A typo'd verdict is worse than no label: it would write a column consumers read as
    human judgement while meaning nothing.
    """
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
    """Recompute *_numeric where it is NULL but the raw string parses. Idempotent.

    Self-limiting: once a row is filled it never matches again. Only touches rows the parser
    can already read, so it introduces no judgement — it makes the column mean what it claims.
    """
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
    """Write the human verdicts onto every row carrying a labelled string. Idempotent.

    Keyed on the raw string, so one label covers every row that publishes it — 35 labels
    cover 68 rows, because both feeds publish most of the same strings, and any future row
    carrying a labelled string is covered the moment it arrives.

    Only ever writes *_labelled / *_verdict. A label never reaches *_numeric: an analyst
    summing that column is asking what the machine could read, and must keep getting that
    answer.
    """
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
    """Raw strings the parser refuses and no label covers — the review queue.

    This is the whole point of keeping *_numeric machine-only. Depends on
    backfill_numeric_amounts having run: without it the queue is full of rows whose string
    parses perfectly and whose column is merely stale.
    """
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
