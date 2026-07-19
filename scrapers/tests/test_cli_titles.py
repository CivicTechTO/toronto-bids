import pytest

from toronto_bids import cli


def test_enrich_titles_no_longer_accepts_scrape():
    # The Bid Award Panel is abolished; the scrape path is removed. argparse must reject it.
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["enrich-titles", "--scrape"])


def test_scrape_agendas_requires_explicit_term_starts():
    from toronto_bids.sources.bid_award_panel import scrape_agendas
    with pytest.raises(TypeError):
        scrape_agendas("/tmp/whatever")  # no term_starts -> TypeError, not a BA/BD default
