from toronto_bids.sources.bid_award_panel import discover_meetings
from toronto_bids.sources.zoo_board import ZB_TERM_STARTS


def test_discover_meetings_accepts_custom_term_starts():
    calls = []

    def fetch(ref):
        calls.append(ref)
        return "The published report was not found"          # every probe misses

    found = discover_meetings(fetch, term_starts=[("ZB", 2019, "2018-2022", 1)],
                              stop_after_misses=2)
    assert found == {}
    assert calls[0] == "2019.ZB1"          # probes the ZB series, not BA/BD


def test_zb_terms_cover_the_evidenced_years():
    series = {t[0] for t in ZB_TERM_STARTS}
    years = {t[1] for t in ZB_TERM_STARTS}
    assert series == {"ZB"}
    assert 2019 in years and 2023 in years   # ZB1.06 is 2019; redpanda is 2025
