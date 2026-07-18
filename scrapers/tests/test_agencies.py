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
