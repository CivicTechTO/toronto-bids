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
