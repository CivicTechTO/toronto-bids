import pathlib

from toronto_bids.buyers import seed_buyers
from toronto_bids.sources.bid_award_panel import discover_meetings
from toronto_bids.sources.zoo_board import ZB_TERM_STARTS, parse_zoo_report, store_zoo_reports

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
    assert got["winner"] is None            # negotiate the award: no firm named


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


def test_store_zoo_reports_preserves_public_winner_on_confidential_award(conn):
    # Value-confidential (only the dollar amount is withheld) must not discard a
    # publicly-named winner: Imperial Fence, Inc. is named in plain body text.
    ids = seed_buyers(conn)
    text = _read("zoo_perimeter_fence_2025.txt")
    conn.execute(
        "INSERT INTO background_pdf (url, kind, reference, sha256, text) "
        "VALUES (?, 'agency_board', ?, 'x', ?)",
        ("https://www.toronto.ca/legdocs/mmis/2025/zb/bgrd/backgroundfile-1.pdf",
         "2025.ZB17.2", text))
    store_zoo_reports(conn, ids["toronto-zoo"])
    row = conn.execute(
        "SELECT supplier_name_raw, value_confidential, award_amount_numeric "
        "FROM agency_award WHERE native_ref='RFT-42'").fetchone()
    assert "Imperial Fence" in row["supplier_name_raw"]
    assert row["value_confidential"] == 1
    assert row["award_amount_numeric"] is None
