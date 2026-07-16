"""#84: the losing bidders and bid prices rewrite spec §2.5.2 calls "Unrecoverable".

They are tabulated on every Bid Award Panel agenda. Fixtures are the real pages.
"""
import pathlib

import pytest

from toronto_bids.amount import parse_bid_price
from toronto_bids.models import Bid
from toronto_bids.sources.bid_award_panel import parse_bid_tables, store_bids
from toronto_bids.store import db

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "bid_award_panel"


def _fixture(name):
    return (FIXTURES / f"{name}.html").read_text()


def test_extracts_the_losing_bidders_from_a_real_agenda():
    """BA189.1: five bidders, one of whom won. The other four are what §2.5.2 says
    does not exist."""
    bids = parse_bid_tables(_fixture("2022.BA189"), "2022.BA189")
    item = [b for b in bids if b["reference"] == "2022.BA189.1"]
    assert [b["bidder_name_raw"] for b in item] == [
        "Aecom Canada Ltd.", "Black & Veatch Canada Company", "GHD Limited",
        "Hatch Ltd.", "RV Anderson Associates Limited",
    ]
    assert all(b["document_number"] == "3234668279" for b in item)


def test_captures_the_price_and_its_hst_basis():
    """A bare price is two incomparable things: 5,752 bids include HST, 4,083 exclude it."""
    bids = parse_bid_tables(_fixture("2022.BA189"), "2022.BA189")
    trevor = next(b for b in bids if b["bidder_name_raw"] == "Trevor Owen Ltd.")
    assert trevor["bid_price"] == "$163,799.50*"     # verbatim, footnote and all
    assert trevor["hst_basis"] == "excluding"
    assert trevor["price_header"] == "Bid Price (excluding H.S.T.)"


def test_financial_impact_tables_are_not_bids():
    """An item also carries WBS / cost-centre tables that look nothing like a bid."""
    bids = parse_bid_tables(_fixture("2022.BA189"), "2022.BA189")
    names = {b["bidder_name_raw"] for b in bids}
    assert not any(n.startswith(("CWW", "CTP", "TS6", "Total", "Cost Centre")) for n in names)
    assert "GHD Limited" in names


def test_pre_ariba_bids_are_kept_even_though_nothing_joins_them():
    """2017 agendas name no document number — Toronto had no Ariba yet. #77 wants these."""
    bids = parse_bid_tables(_fixture("2017.BA1"), "2017.BA1")
    assert bids, "the 2017 corpus is 2,012 bidder rows; dropping them would be the bug"
    assert all(b["document_number"] is None for b in bids)
    assert "MeteoGroup Weather Services Canada Inc." in {b["bidder_name_raw"] for b in bids}


@pytest.mark.parametrize("raw,expected", [
    ("$2,982,036.67*", 2982036.67),      # footnote: "includes contingency"
    ("$1,581,114.08 *", 1581114.08),     # ...sometimes spaced
    ("$3,181,107.70^", 3181107.70),
    ("$3,197,989.27+", 3197989.27),
    ("$163,799.50", 163799.50),
    # The City writes outcomes in the price column. Those are not amounts, and the raw
    # string is what records why a bid lost.
    ("Non-Compliant", None), ("No bid", None), ("N/A", None), (None, None),
])
def test_bid_price_strips_the_footnote_but_still_refuses_a_non_price(raw, expected):
    got = parse_bid_price(raw)
    assert got == pytest.approx(expected) if expected is not None else got is None


def test_a_rejected_bid_keeps_its_reason(conn):
    db.upsert_row(conn, Bid(reference="2022.BA1.1", bidder_name_raw="Acme",
                            bid_price="Non-Compliant", source="bid_award_panel"),
                  overwrite=True)
    conn.commit()
    row = conn.execute("SELECT bid_price, bid_price_numeric FROM bid").fetchone()
    assert row["bid_price"] == "Non-Compliant"   # why it lost
    assert row["bid_price_numeric"] is None      # ...and it is not a number


def test_bidders_without_a_price_do_not_duplicate_on_every_run(conn):
    """Scored RFPs list bidders with no price; 1,909 bids have none. SQLite treats NULLs as
    distinct in a UNIQUE index, so a bare key would re-insert every one of them each run."""
    for _ in range(3):
        db.upsert_row(conn, Bid(reference="2022.BA189.1", bidder_name_raw="Hatch Ltd.",
                                bid_price=None, source="bid_award_panel"), overwrite=True)
        conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0] == 1
    assert conn.execute("SELECT bid_price FROM bid").fetchone()[0] is None


def test_store_bids_is_idempotent(conn):
    agendas = {"2022.BA189": _fixture("2022.BA189")}
    first = store_bids(conn, agendas)
    assert first == 20
    store_bids(conn, agendas)
    assert conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0] == 20
    assert db.counts(conn)["bid"] == 20


def test_two_bidders_on_one_item_both_survive(conn):
    """The key must not collapse distinct bidders on the same solicitation."""
    store_bids(conn, {"2022.BA189": _fixture("2022.BA189")})
    n = conn.execute("SELECT COUNT(*) FROM bid WHERE reference='2022.BA189.1'").fetchone()[0]
    assert n == 5


def test_bids_reach_the_export_under_their_council_item(conn):
    from toronto_bids.export.document import build_export_document
    from toronto_bids.models import CouncilItem

    db.upsert_row(conn, CouncilItem(reference="2022.BA189.2", title="Award of ..."),
                  overwrite=True)
    db.upsert_row(conn, Bid(reference="2022.BA189.2", bidder_name_raw="Trevor Owen Ltd.",
                            bid_price="$163,799.50*", hst_basis="excluding",
                            source="bid_award_panel"), overwrite=True)
    conn.commit()
    doc = build_export_document(conn, generated_at="2026-07-16T00:00:00Z")
    item = next(c for c in doc["council_items"] if c["reference"] == "2022.BA189.2")
    assert len(item["bids"]) == 1
    assert item["bids"][0]["bid_price_numeric"] == pytest.approx(163799.50)
    assert item["bids"][0]["hst_basis"] == "excluding"
