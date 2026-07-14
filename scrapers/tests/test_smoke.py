from toronto_bids import __version__
from toronto_bids.cli import main


def test_version_is_a_string():
    assert isinstance(__version__, str)


def test_main_with_no_args_returns_zero():
    assert main([]) == 0
