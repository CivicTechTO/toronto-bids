import pytest

from toronto_bids.linking.amount_labels import (
    VERDICTS, apply_amount_labels, backfill_numeric_amounts, load_labels, unlabelled_amounts)
from toronto_bids.models import Award, NonCompetitive
from toronto_bids.store import db


def test_the_shipped_labels_are_valid():
    labels = load_labels()
    assert len(labels) == 35
    assert all(v["verdict"] in VERDICTS for v in labels.values())


def test_every_string_the_parser_refuses_has_a_verdict():
    from toronto_bids.amount import parse_amount

    for raw, label in load_labels().items():
        assert parse_amount(raw) is None, (
            f"{raw!r} parses to {parse_amount(raw)} — a label would be dead weight, and if "
            f"the City fixed the string the label must stop matching, not override it")


@pytest.mark.parametrize("raw,value", [
    ("S2,035,000.00", 2035000.00),
    ("$982, 900", 982900.00),
    ("942467.", 942467.00),
])
def test_the_recoverable_typos_resolve_to_the_obvious_number(raw, value):
    label = load_labels()[raw]
    assert label["verdict"] == "amount"
    assert label["value"] == value


def test_a_rate_is_labelled_not_an_amount_rather_than_guessed():
    assert load_labels()["31.65/MT"]["verdict"] == "not_an_amount"


def test_the_usd_row_is_unknown_not_a_number():
    label = load_labels()["$1,311,936.00 USD"]
    assert label["verdict"] == "unknown"
    assert "value" not in label


def test_the_phantom_row_is_not_an_award_not_merely_unparseable():
    assert load_labels()["j"]["verdict"] == "not_an_award"


def test_an_unknown_verdict_is_rejected_loudly(tmp_path):
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
    p = tmp_path / "l.toml"
    p.write_text('["x"]\nverdict = "corrupt"\nvalue = 1.0\n')
    with pytest.raises(ValueError, match="must not"):
        load_labels(p)


_LABELS = {"S2,035,000.00": {"verdict": "amount", "value": 2035000.00},
           "31.65/MT": {"verdict": "not_an_amount"}}


def test_a_label_never_reaches_the_numeric_column(conn):
    db.upsert_row(conn, Award("1234567890", supplier_name_raw="Acme",
                              award_amount="S2,035,000.00", source="odata"), overwrite=True)
    conn.commit()
    apply_amount_labels(conn, _LABELS)
    row = conn.execute("SELECT award_amount, award_amount_numeric, award_amount_labelled, "
                       "award_amount_verdict FROM award").fetchone()
    assert row["award_amount"] == "S2,035,000.00"
    assert row["award_amount_numeric"] is None
    assert row["award_amount_labelled"] == 2035000.00
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


def test_backfill_fills_a_row_written_before_the_parser_existed(conn):
    conn.execute("INSERT INTO award (document_number, supplier_name_raw, award_amount, source) "
                 "VALUES ('1234567890', 'Acme', '776,022.90', 'odata')")
    conn.commit()
    assert backfill_numeric_amounts(conn) == 1
    assert conn.execute("SELECT award_amount_numeric FROM award").fetchone()[0] == 776022.90


def test_backfill_never_touches_a_string_the_parser_refuses(conn):
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


def test_an_unlabelled_reject_surfaces(conn):
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
