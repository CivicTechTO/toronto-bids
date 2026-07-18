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


def test_report_mentioning_award_but_extracting_nothing_returns_none():
    """A committee/info report that merely says 'award' — no winner, no amount, no
    confidential attachment — must not become an empty award row (#135). Live run
    surfaced 53 such contentless rows; the archive refuses rather than assert a
    hollow award (the project's 'a wrong record is worse than none' rule)."""
    text = ("STAFF REPORT\nSubject: Update on Capital Projects\n"
            "This report provides an update. Council previously approved the award "
            "of several contracts. No new decisions are recommended at this time.")
    assert parse_zoo_report(text, fallback_ref="2020.ZB5") is None


# --- Zoo winner/amount recall against the real corpus (the 26-is-too-low fix) ------

def test_amount_phrase_award_with_no_legal_suffix():
    """'awarded to Tri-Unite Systems in the amount of $410,563' — the winner has no
    Inc/Ltd suffix and the amount uses 'in the amount of', so the suffix-anchored winner
    regex and the 'total cost'-only amount regex both missed it and the report was dropped.
    """
    got = parse_zoo_report(_read("zoo_amount_phrase_2018.txt"), fallback_ref="2018.ZB23")
    assert got is not None                              # was dropped as 'empty'
    assert got["winner"] == "Tri-Unite Systems"
    assert got["confidential"] == 0
    from toronto_bids.amount import parse_amount
    assert parse_amount(got["amount"]) == 410563.00


def test_in_the_amount_of_is_parsed():
    got = parse_zoo_report(_read("zoo_no_suffix_2020.txt"), fallback_ref="2020.ZB9")
    assert got is not None
    assert "Midome Construction Services Ltd." in got["winner"]
    from toronto_bids.amount import parse_amount
    assert parse_amount(got["amount"]) == 638000.00


def test_european_decimal_million_shorthand_refused_not_stored_as_one_dollar():
    """'$1,25 million' (comma decimal + scale word) captures only '$1' once thousands
    grouping is enforced — refuse the amount rather than store a bogus $1, keep the winner."""
    text = ("Subject: Contract Award\nThe Board recommends the award of the tender "
            "to Acme Builders in the amount of $1,25 million to complete the work.")
    got = parse_zoo_report(text, fallback_ref="2021.ZB1")
    assert got is not None and got["winner"] == "Acme Builders"
    assert got["amount"] is None
