import pathlib

from toronto_bids.sources.bid_award_panel import discover_meetings
from toronto_bids.sources.zoo_board import ZB_TERM_STARTS, parse_zoo_report

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _read(name):
    return (FIXTURES / name).read_text()


def test_energy_retrofit_names_public_winner():
    got = parse_zoo_report(_read("zoo_energy_retrofit_2019.txt"), fallback_ref="2019.ZB1.6")
    assert got["native_ref"] == "RFP 18 (2018-03)"
    assert got["confidential"] == 0
    assert "Ecosystem" in got["winner"]


def test_red_panda_is_confidential_award():
    got = parse_zoo_report(_read("zoo_red_panda_2025.txt"), fallback_ref="2025.ZB15.3")
    assert got["confidential"] == 1
    assert got["amount"] is None            # value withheld, not unpublished
    assert got["native_ref"] == "RFP38"     # the report writes it unspaced


def test_fallback_ref_when_report_names_none():
    text = ("REPORT FOR ACTION WITH\nCONFIDENTIAL ATTACHMENT\n"
            "Subject: Widget Tender Award\n"
            "This report recommends the award of the widget contract.")
    got = parse_zoo_report(text, fallback_ref="2025.ZB9.1")
    assert got["native_ref"] == "2025.ZB9.1"


def test_perimeter_fence_extracts_rft_ref():
    got = parse_zoo_report(_read("zoo_perimeter_fence_2025.txt"), fallback_ref="2025.ZB17.2")
    assert got["confidential"] == 1
    assert got["native_ref"] == "RFT-42"


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
