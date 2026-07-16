import pytest

from toronto_bids.linking.supplier import supplier_key


@pytest.mark.parametrize(
    "a,b",
    [
        ("Compugen Inc.", "Compugen Inc"),                                  # trailing period
        ("Direct Construction Company limited", "Direct Construction Company Limited"),  # case
        ("QRX TECHNOLOGY GROUP INC", "Qrx Technology Group Inc"),           # all-caps vs title
        ("Joe Pace & Sons Contracting Inc.", "JOE PACE & SONS CONTRACTING INC"),  # case + punct
        ("Acme  Co", "Acme Co"),                                            # collapsed whitespace
    ],
)
def test_variants_share_a_key(a, b):
    assert supplier_key(a) == supplier_key(b)
    assert supplier_key(a) != ""


def test_key_is_lowercase_alnum_spaces_only():
    assert supplier_key("QRX TECHNOLOGY GROUP INC") == "qrx technology group inc"
    assert supplier_key("Joe Pace & Sons Contracting Inc.") == "joe pace sons contracting inc"


def test_strips_submitted_by_suffix():
    assert supplier_key("SCA Office Solutions (Submitted by: Acme Reseller)") == \
        supplier_key("SCA Office Solutions")


def test_distinct_entities_stay_distinct():
    # Legal suffixes are NOT stripped, so Inc vs Ltd remain different keys.
    assert supplier_key("Capital Sewer Services Inc.") != supplier_key("Capital Sewer Services Ltd.")


@pytest.mark.parametrize("raw", [None, "", "   ", "()", "!!!"])
def test_blank_or_garbage_yields_empty_key(raw):
    assert supplier_key(raw) == ""
