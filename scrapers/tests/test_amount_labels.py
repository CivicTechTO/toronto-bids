"""#74: human verdicts on the 35 amount strings the parser refuses.

amount.py parses 99.5% of what the City publishes and returns NULL for the rest rather than
guessing. Some of the rest a human can read ('S2,035,000.00'); some are genuinely
unrecoverable; one is a real amount in the wrong currency; one is not an award at all. This
is where that judgement lives, and — critically — where it does NOT land.
"""
import pytest

from toronto_bids.linking.amount_labels import (
    VERDICTS, apply_amount_labels, backfill_numeric_amounts, load_labels, unlabelled_amounts)
from toronto_bids.models import Award, NonCompetitive
from toronto_bids.store import db


# --- the shipped file --------------------------------------------------------------------

def test_the_shipped_labels_are_valid():
    """The file ships in the wheel via importlib.resources, as store/schema.sql does."""
    labels = load_labels()
    assert len(labels) == 35
    assert all(v["verdict"] in VERDICTS for v in labels.values())


def test_every_string_the_parser_refuses_has_a_verdict():
    """35 labels cover 68 rows: both feeds publish most of the same strings, so labelling the
    STRING rather than the row covers every current row and any future one for free."""
    from toronto_bids.amount import parse_amount

    for raw, label in load_labels().items():
        assert parse_amount(raw) is None, (
            f"{raw!r} parses to {parse_amount(raw)} — a label would be dead weight, and if "
            f"the City fixed the string the label must stop matching, not override it")


@pytest.mark.parametrize("raw,value", [
    ("S2,035,000.00", 2035000.00),   # 'S' is a typo for '$' — adjacent key
    ("$982, 900", 982900.00),        # stray space inside the thousands group
    ("942467.", 942467.00),          # trailing decimal point
])
def test_the_recoverable_typos_resolve_to_the_obvious_number(raw, value):
    label = load_labels()[raw]
    assert label["verdict"] == "amount"
    assert label["value"] == value


def test_a_rate_is_labelled_not_an_amount_rather_than_guessed():
    """'31.65/MT' is $31.65 per metric tonne. The NULL is correct and the label says so, which
    is what stops it being re-litigated by the next person who looks."""
    assert load_labels()["31.65/MT"]["verdict"] == "not_an_amount"


def test_the_usd_row_is_unknown_not_a_number():
    """The most tempting entry on the list, and the reason it is not labelled: it parses
    cleanly but is USD, and every other amount here is CAD. A value would mix currencies into
    a CAD sum silently — the exact failure amount.py exists to prevent."""
    label = load_labels()["$1,311,936.00 USD"]
    assert label["verdict"] == "unknown"
    assert "value" not in label


def test_the_phantom_row_is_not_an_award_not_merely_unparseable():
    """Doc 4483761813 carries a real award plus a junk OData row (supplier 'kj', amount 'j')
    that CKAN does not have. Rows are never deleted, so the verdict has to mean 'exclude from
    aggregates' — a different downstream behaviour from not_an_amount."""
    assert load_labels()["j"]["verdict"] == "not_an_award"


# --- validation --------------------------------------------------------------------------

def test_an_unknown_verdict_is_rejected_loudly(tmp_path):
    """A typo'd verdict is worse than no label: it writes a column consumers read as human
    judgement while meaning nothing."""
    p = tmp_path / "l.toml"
    p.write_text('["x"]\nverdict = "recoverable"\n')
    with pytest.raises(ValueError, match="unknown verdict"):
        load_labels(p)


def test_an_amount_verdict_without_a_value_is_rejected(tmp_path):
    p = tmp_path / "l.toml"
    p.write_text('["x"]\nverdict = "amount"\n')
    with pytest.raises(ValueError, match="no numeric `value`"):
        load_labels(p)


def test_a_non_amount_verdict_carrying_a_value_is_rejected(tmp_path):
    """Only 'amount' resolves to a number. A 'corrupt' with a value is a contradiction."""
    p = tmp_path / "l.toml"
    p.write_text('["x"]\nverdict = "corrupt"\nvalue = 1.0\n')
    with pytest.raises(ValueError, match="must not"):
        load_labels(p)


# --- the tiers stay apart ----------------------------------------------------------------

_LABELS = {"S2,035,000.00": {"verdict": "amount", "value": 2035000.00},
           "31.65/MT": {"verdict": "not_an_amount"}}


def test_a_label_never_reaches_the_numeric_column(conn):
    """The property that makes #64 useful. `numeric` means "a number the machine derived from
    what the City published"; merging human calls into it makes every SUM part-machine,
    part-opinion, and destroys the review queue."""
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                              award_amount="S2,035,000.00", source="odata"), overwrite=True)
    conn.commit()
    apply_amount_labels(conn, _LABELS)
    row = conn.execute("SELECT award_amount, award_amount_numeric, award_amount_labelled, "
                       "award_amount_verdict FROM award").fetchone()
    assert row["award_amount"] == "S2,035,000.00"      # raw: what the City published
    assert row["award_amount_numeric"] is None         # parsed: the machine still refuses it
    assert row["award_amount_labelled"] == 2035000.00  # labelled: the human call
    assert row["award_amount_verdict"] == "amount"


def test_a_non_amount_verdict_records_the_judgement_but_no_number(conn):
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                              award_amount="31.65/MT", source="odata"), overwrite=True)
    conn.commit()
    apply_amount_labels(conn, _LABELS)
    row = conn.execute("SELECT award_amount_labelled, award_amount_verdict FROM award").fetchone()
    assert row["award_amount_labelled"] is None
    assert row["award_amount_verdict"] == "not_an_amount"


def test_one_label_covers_every_row_carrying_that_string(conn):
    """Both feeds publish the same string, which is why 35 labels cover 68 rows."""
    for source in ("odata", "ckan_awarded"):
        db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                                  award_amount="S2,035,000.00", source=source), overwrite=True)
    conn.commit()
    assert apply_amount_labels(conn, _LABELS) == 2
    assert conn.execute("SELECT COUNT(*) FROM award "
                        "WHERE award_amount_labelled = 2035000.0").fetchone()[0] == 2


def test_labels_reach_noncompetitive_too(conn):
    db.upsert_row(conn, NonCompetitive(workspace_number="W1",
                                       contract_amount="S2,035,000.00", source="odata"),
                  overwrite=True)
    conn.commit()
    apply_amount_labels(conn, _LABELS)
    row = conn.execute("SELECT contract_amount_labelled, contract_amount_verdict "
                       "FROM noncompetitive").fetchone()
    assert row["contract_amount_labelled"] == 2035000.00
    assert row["contract_amount_verdict"] == "amount"


def test_applying_labels_is_idempotent(conn):
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                              award_amount="S2,035,000.00", source="odata"), overwrite=True)
    conn.commit()
    apply_amount_labels(conn, _LABELS)
    apply_amount_labels(conn, _LABELS)
    assert conn.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1


def test_a_sync_cannot_clobber_a_label(conn):
    """The trap #79 shipped. Every sync re-upserts these rows, so anything db.upsert_row can
    write, the feed can reset. The labelled columns are absent from the models on purpose."""
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                              award_amount="S2,035,000.00", source="odata"), overwrite=True)
    conn.commit()
    apply_amount_labels(conn, _LABELS)
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                              award_amount="S2,035,000.00", source="odata"), overwrite=True)
    conn.commit()
    row = conn.execute("SELECT award_amount_labelled, award_amount_verdict FROM award").fetchone()
    assert row["award_amount_labelled"] == 2035000.00
    assert row["award_amount_verdict"] == "amount"


# --- the backfill, without which the review queue lies -----------------------------------

def test_backfill_fills_a_row_written_before_the_parser_existed(conn):
    """Rows are never deleted, so a row written before amount.py keeps its NULL forever
    unless the feed re-upserts that exact key. 2,856 rows were in that state, and
    `numeric IS NULL` therefore did not mean "not machine-parseable" at all."""
    conn.execute("INSERT INTO award (document_number, supplier_name_raw, award_amount, source) "
                 "VALUES ('1234567890', 'Acme', '776,022.90', 'odata')")
    conn.commit()
    assert backfill_numeric_amounts(conn) == 1
    assert conn.execute("SELECT award_amount_numeric FROM award").fetchone()[0] == 776022.90


def test_backfill_never_touches_a_string_the_parser_refuses(conn):
    """It introduces no judgement: only rows the parser can already read."""
    conn.execute("INSERT INTO award (document_number, supplier_name_raw, award_amount, source) "
                 "VALUES ('1234567890', 'Acme', 'S2,035,000.00', 'odata')")
    conn.commit()
    assert backfill_numeric_amounts(conn) == 0
    assert conn.execute("SELECT award_amount_numeric FROM award").fetchone()[0] is None


def test_backfill_is_idempotent(conn):
    conn.execute("INSERT INTO award (document_number, supplier_name_raw, award_amount, source) "
                 "VALUES ('1234567890', 'Acme', '776,022.90', 'odata')")
    conn.commit()
    assert backfill_numeric_amounts(conn) == 1
    assert backfill_numeric_amounts(conn) == 0


# --- the review queue --------------------------------------------------------------------

def test_an_unlabelled_reject_surfaces(conn):
    """The trickle from future syncs has to surface, not sit silent until someone looks."""
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                              award_amount="!!! nonsense !!!", source="odata"), overwrite=True)
    conn.commit()
    pending = unlabelled_amounts(conn)
    assert [(p["table"], p["raw"]) for p in pending] == [("award", "!!! nonsense !!!")]


def test_a_labelled_reject_does_not_surface(conn):
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                              award_amount="31.65/MT", source="odata"), overwrite=True)
    conn.commit()
    assert unlabelled_amounts(conn) == []


def test_a_parseable_amount_never_surfaces(conn):
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                              award_amount="$1,075.00", source="odata"), overwrite=True)
    conn.commit()
    assert unlabelled_amounts(conn) == []
