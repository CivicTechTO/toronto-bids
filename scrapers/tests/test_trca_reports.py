import pathlib

from toronto_bids.sources.trca_board import parse_trca_report

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _read(name):
    return (FIXTURES / name).read_text()


def test_armour_stone_refs_and_title():
    items = parse_trca_report(_read("trca_armour_stone_2023.txt"))
    refs = {i["native_ref"] for i in items}
    assert refs == {"10039751", "10039753"}   # one row per ref (multi-ref item)
    assert all("ARMOUR" in i["title"].upper() for i in items)


def test_armour_stone_winners_and_amounts():
    items = {i["native_ref"]: i for i in parse_trca_report(_read("trca_armour_stone_2023.txt"))}
    w751 = dict(items["10039751"]["winners"])
    assert '1035477 Ontario Ltd. ("Glenn Windrem Trucking")' in w751 or \
           any("Glenn Windrem" in k for k in w751)
    assert "$1,193,040" in w751.values()
    w753 = dict(items["10039753"]["winners"])
    assert any("Gott Natural Stone" in k for k in w753)
    assert "$567,648" in w753.values()


def test_armour_stone_bidder_list_is_clean_bullets():
    items = parse_trca_report(_read("trca_armour_stone_2023.txt"))
    bidders = items[0]["bidders"]
    assert len(bidders) == 4
    assert "H.R. Doornekamp Construction Ltd." in bidders
    assert "Metric Contracting Services Corporation" in bidders
    # The fused results table must NOT be mined: no bidder is a mangled wrap fragment.
    assert all(len(b) > 5 and not b.startswith("$") for b in bidders)


def test_vor_report_names_both_winners_without_amounts():
    items = parse_trca_report(_read("trca_vor_appraisal_2021.txt"))
    assert len(items) == 1 and items[0]["native_ref"] == "10036307"
    names = [w[0] for w in items[0]["winners"]]
    assert any("D. Bottero" in n for n in names)
    assert any("Newmark Knight Frank" in n for n in names)
