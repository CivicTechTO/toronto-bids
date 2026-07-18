import pathlib
import sqlite3

import pytest

from toronto_bids.buyers import DEFAULT_BUYERS, seed_buyers
from toronto_bids.models import AgencyAward, AgencyBid, AgencySolicitation
from toronto_bids.store import db


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    db.init_db(conn)
    yield conn
    conn.close()


def test_seed_buyers_is_idempotent(conn):
    ids = seed_buyers(conn)
    assert set(ids) == {"toronto-zoo", "trca"}
    again = seed_buyers(conn)
    assert ids == again
    assert conn.execute("SELECT COUNT(*) FROM buyer").fetchone()[0] == len(DEFAULT_BUYERS)


def test_trca_is_partnered_with_funding_share(conn):
    seed_buyers(conn)
    row = conn.execute("SELECT * FROM buyer WHERE slug='trca'").fetchone()
    assert row["partnered"] == 1
    assert row["funding_share"] == 0.626
    assert row["kind"] == "agency"


def test_agency_award_upsert_is_idempotent_with_null_amount(conn):
    ids = seed_buyers(conn)
    row = AgencyAward(buyer_id=ids["trca"], native_ref="10039751",
                      supplier_name_raw=None, award_amount=None,
                      value_confidential=1, award_date=None,
                      report_url="https://example.test/r.pdf", source="trca_board")
    db.upsert_row(conn, row, overwrite=True)
    db.upsert_row(conn, row, overwrite=True)   # NULLs must not duplicate (COALESCE key)
    assert conn.execute("SELECT COUNT(*) FROM agency_award").fetchone()[0] == 1


def test_agency_award_numeric_derived(conn):
    ids = seed_buyers(conn)
    row = AgencyAward(buyer_id=ids["trca"], native_ref="10039751",
                      supplier_name_raw='1035477 Ontario Ltd. ("Glenn Windrem Trucking")',
                      award_amount="$1,193,040", value_confidential=0,
                      award_date=None, report_url=None, source="trca_board")
    assert row.award_amount_numeric == 1193040.0
    db.upsert_row(conn, row, overwrite=True)
    got = conn.execute("SELECT award_amount_numeric FROM agency_award").fetchone()[0]
    assert got == 1193040.0


def test_agency_solicitation_backfill_never_overwrites(conn):
    ids = seed_buyers(conn)
    db.upsert_row(conn, AgencySolicitation(
        buyer_id=ids["trca"], native_ref="10039751", title="Portal title",
        status=None, posted_date=None, closing_date=None, portal_url=None,
        source="bids_tenders"), overwrite=True)
    db.upsert_row(conn, AgencySolicitation(
        buyer_id=ids["trca"], native_ref="10039751", title="Board title",
        status="awarded", posted_date=None, closing_date=None, portal_url=None,
        source="trca_board"), overwrite=False)
    row = conn.execute("SELECT title, status FROM agency_solicitation").fetchone()
    assert row["title"] == "Portal title"   # backfill only fills NULLs
    assert row["status"] == "awarded"       # ...but does fill them


def test_counts_include_agency_tables(conn):
    got = db.counts(conn)
    for table in ("buyer", "agency_solicitation", "agency_award", "agency_bid"):
        assert table in got


def test_supplier_dimension_spans_agency_tables(conn):
    from toronto_bids.linking.supplier import build_supplier_dimension
    ids = seed_buyers(conn)
    db.upsert_row(conn, AgencyAward(
        buyer_id=ids["trca"], native_ref="10039751",
        supplier_name_raw="Gott Natural Stone '99 Inc.", award_amount="$567,648",
        value_confidential=0, award_date=None, report_url=None, source="trca_board"),
        overwrite=True)
    db.upsert_row(conn, AgencyBid(
        buyer_id=ids["trca"], native_ref="10039751",
        bidder_name_raw="H.R. Doornekamp Construction Ltd.", bid_price=None,
        report_url=None, source="trca_board"), overwrite=True)
    n = build_supplier_dimension(conn)
    assert n == 2   # winner + losing bidder both in the dimension
    linked = conn.execute(
        "SELECT COUNT(*) FROM agency_bid WHERE supplier_id IS NOT NULL").fetchone()[0]
    assert linked == 1


def test_enrich_agencies_offline_parses_cached(conn, monkeypatch, capsys):
    """Offline default: no network, parses whatever background_pdf already holds."""
    from toronto_bids import cli
    ids = seed_buyers(conn)
    text = (pathlib.Path(__file__).parent / "fixtures" / "agencies"
            / "trca_armour_stone_2023.txt").read_text()
    conn.execute("INSERT INTO background_pdf (url, kind, sha256, text) "
                 "VALUES ('https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=14809',"
                 " 'agency_board', 'x', ?)", (text,))
    conn.commit()

    class _NoClose:
        """sqlite3.Connection is a C type — its methods can't be monkeypatched directly
        (see test_nightly.py's _CloseFails), so proxy everything except close."""
        def close(self):
            pass

        def __getattr__(self, name):
            return getattr(conn, name)

    monkeypatch.setattr(cli, "_open_db", lambda: _NoClose())
    rc = cli.main(["enrich-agencies", "--only", "trca"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "trca" in out and "awards" in out
    assert conn.execute("SELECT COUNT(*) FROM agency_award").fetchone()[0] >= 2
