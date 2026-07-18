from toronto_bids.sources.bid_award_panel import discover_meetings
from toronto_bids.sources.ep_board import EP_TERM_STARTS


def test_ep_terms_probe_the_ep_committee_series():
    calls = []

    def fetch(ref):
        calls.append(ref)
        return "The published report was not found"     # every probe misses

    found = discover_meetings(fetch, term_starts=[("EP", 2023, "2022-2026", 1)],
                              stop_after_misses=2)
    assert found == {}
    assert calls[0] == "2023.EP1"                        # probes EP, not ZB/BA


def test_ep_terms_cover_the_confirmed_terms():
    series = {t[0] for t in EP_TERM_STARTS}
    years = {t[1] for t in EP_TERM_STARTS}
    assert series == {"EP"}
    assert 2023 in years                                 # 2022-2026 term (2025.EP18 seen live)


def test_ep_buyer_seeded():
    import sqlite3
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.store import db
    conn = db.connect(":memory:"); db.init_db(conn)
    ids = seed_buyers(conn)
    assert "exhibition-place" in ids
    row = conn.execute("SELECT kind, partnered FROM buyer WHERE slug='exhibition-place'").fetchone()
    assert row["kind"] == "agency" and row["partnered"] == 0
    conn.close()


import pathlib

from toronto_bids.sources.ep_board import parse_ep_bid_table, parse_ep_report

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _read(name):
    return (FIXTURES / name).read_text()


def test_competitive_award_winner_ref_amount():
    got = parse_ep_report(_read("ep_award_with_table_2023.txt"), fallback_ref="2023.EP1.1")
    assert got is not None
    assert got["native_ref"] == "EP110-2023"            # RFT No., not the Contract No.
    assert got["winner"] == "Powell Fence Limited"       # stops at " for ", not the project text
    from toronto_bids.amount import parse_amount
    assert parse_amount(got["amount"]) == 1484065.00
    assert got["confidential"] == 0


def test_2019_award_line_wrapped_winner_and_contract_ref():
    # 2019 EP reports differ (see SOURCES.md): the winner spans a pdftotext line break
    # ("Westbury National\nShow System Ltd.") and the ref is a Contract No., not RFT-EP.
    got = parse_ep_report(_read("ep_award_2019_sole_tender.txt"), fallback_ref="2019.EP2.1")
    assert got is not None
    assert got["winner"] == "Westbury National Show System Ltd."   # line-wrap collapsed, whole name
    assert got["native_ref"] == "19-085-98518"          # Contract No. (no RFT-EP in 2019)
    from toronto_bids.amount import parse_amount
    assert parse_amount(got["amount"]) == 969415.00     # "in the amount of $969,415.00" (not the bid)


def test_2019_award_strips_location_qualifier_from_winner():
    # "to Sutherland-Schultz Ltd. of Cambridge, Ontario for …" — strip the " of <City>, <Prov>"
    # qualifier so the firm keys consistently in the supplier dimension.
    got = parse_ep_report(_read("ep_award_2019_multi_bidder.txt"), fallback_ref="2019.EP6.1")
    assert got is not None
    assert got["winner"] == "Sutherland-Schultz Ltd."   # NOT "... of Cambridge, Ontario"
    from toronto_bids.amount import parse_amount
    assert parse_amount(got["amount"]) == 403854.47


def test_confidential_award_keeps_named_winner_nulls_amount():
    got = parse_ep_report(_read("ep_confidential_decision_2025.txt"), fallback_ref="2025.EP18.9")
    assert got is not None and got["confidential"] == 1
    assert got["amount"] is None
    assert "Coca-Cola" in got["winner"]


def test_confidential_with_redacted_counterparty_has_no_winner_but_is_kept():
    got = parse_ep_report(_read("ep_confidential_agreement.txt"), fallback_ref="2023.EP7.2")
    assert got is not None and got["confidential"] == 1
    assert got["winner"] is None                         # "a Consumer Show Client" is redacted, not a firm
    assert got["native_ref"] == "2023.EP7.2"             # no RFT/Contract ref -> fallback


def test_wsib_safety_report_is_refused_despite_dollar_amounts():
    assert parse_ep_report(_read("ep_non_award_wsib_report.txt"), fallback_ref="2023.EP1.4") is None


def test_procurement_status_update_is_refused():
    assert parse_ep_report(_read("ep_non_award_procurement_status.txt"), fallback_ref="2023.EP1.5") is None


def test_bid_table_extracts_all_three_bidders_with_prices():
    rows = parse_ep_bid_table(_read("ep_award_with_table_2023.txt"))
    assert rows == [
        ("Powell Fence Limited", "$1,484,065.00"),
        ("M.J.K. Construction Incorporated", "$1,619,001.00"),
        ("Clearway Construction Incorporated", "$1,851,100.00"),
    ]


def test_bid_table_2019_five_bidders_with_footnote_marker():
    # 2019 "Table 1: Tender Price Submissions" (plural), 5 bidders, a `*` revised-price marker on
    # one row — the price capture must stop before the `*`.
    rows = parse_ep_bid_table(_read("ep_award_2019_multi_bidder.txt"))
    assert rows == [
        ("Sutherland-Schultz Ltd.", "$418,854.47"),
        ("Ontario Electrical Construction Co. Ltd.", "$461,522.00"),
        ("Modern Niagara Toronto Inc.", "$470,700.00"),      # the trailing * is dropped
        ("Stevens & Black Electrical Contractors Ltd.", "$518,000.00"),
        ("Rogol Electric Company Limited", "$546,350.00"),
    ]


def test_bid_table_absent_returns_empty():
    rows = parse_ep_bid_table(_read("ep_non_award_wsib_report.txt"))
    assert rows == []                                    # no Table 1 -> no bids
