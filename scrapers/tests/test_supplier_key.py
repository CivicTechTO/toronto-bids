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


# --- Rule 1: numbered companies key to #<number> ---
def test_numbered_company_variants_collapse_to_the_number():
    for raw in ["2489960 ONTARIO INC",
                "2489960 Ontario Inc. o/a Kore Infrastructure Group",
                "2489960 ONTARIO LTD",
                "2489960 ONTARIO INCORPORATED",
                "2489960 Ontario Inc. operating as Kore Infrastructure Group"]:
        assert supplier_key(raw) == "#2489960", raw

def test_numbered_reverse_order_and_typo_still_key_to_the_number():
    # trade-name-first, number in the middle; and a 0/A zero-for-O typo
    assert supplier_key("TRISAN CONSTRUCTION O/A 614128 ONTARIO LTD.") == "#614128"
    assert supplier_key("614128 ONTARIO LTD, O/A TRISAN CONSTRUCTION") == "#614128"
    assert supplier_key("1568796 ONTARIO INC, 0/A RENOKREW") == "#1568796"

def test_a_stray_parenthetical_number_does_not_steal_the_key():
    # the leading corp number is the identity; the (3059515) is ignored
    assert supplier_key("614128 ONTARIO LTD O/A TRISAN CONSTRUCTION (3059515)") == "#614128"

def test_a_short_or_addressy_number_is_not_a_corp_number():
    # 3-digit / non-corp-length numbers must not trigger Rule 1
    assert supplier_key("123 Ontario Street Holdings Inc.") != "#123"

def test_numbered_company_with_marker_but_no_province_token_still_keys_to_the_number():
    # #171: the trade-name marker (o/a) itself proves the leading digits are the legal corp
    # number, so a numbered company keys to #<number> even without an adjacent province token.
    # Both raw variants below are the SAME firm and must not split into two supplier_ids
    # (which then collide as frontend slugs: '#1818620' and '1818620' both slugify to '1818620').
    assert supplier_key("1818620 Ontario Ltd. o/a Emission Tree") == "#1818620"   # Rule 1
    assert supplier_key("1818620 o/a Emission Tree") == "#1818620"                # was: bare "1818620"
    assert supplier_key("1818620 Ontario Ltd. o/a Emission Tree") == \
           supplier_key("1818620 o/a Emission Tree")

def test_marker_with_short_leading_number_is_not_a_corp_number():
    # guard: a marker after a non-corp-length (3-digit) leading number must NOT mint #123
    assert supplier_key("123 Main o/a Corner Store") != "#123"


# --- Rule 2: named-company trade-name folding, guarded ---
def test_named_trade_name_folds_to_the_legal_base():
    assert supplier_key("Corporate Express Canada Inc., operating as Staples Business Advantage") == \
           supplier_key("Corporate Express Canada Inc. (operating as Staples Advantage Canada)")
    assert supplier_key("R.O.M. Contractors Inc. o/a Ross Clair Contractors") == \
           supplier_key("R.O.M. Contractors Inc o/a Ross Clair Contractor")

def test_marker_strip_is_refused_when_the_base_is_generic():
    # stripping 'o/a Trans Canada' leaves the generic fragment 'ontario ltd' — must NOT merge there
    assert supplier_key("Ontario Ltd. o/a Trans Canada Construction") != "ontario ltd"


# --- Rule 3: salvage noise wrappers, exclude only pure footnote ---
def test_appendix_and_noncompliant_noise_is_salvaged_not_excluded():
    assert supplier_key('Fermar Paving Limited* Non-compliant') == supplier_key("Fermar Paving Limited")
    assert supplier_key('Appendix "C" Benson Group Inc.') == supplier_key("Benson Group Inc.")
    assert supplier_key('LTH Electric Inspection & Service Ltd. Appendix "C" Price Form') == \
           supplier_key("LTH Electric Inspection & Service Ltd.")

def test_pure_footnote_is_excluded():
    for raw in ["Please see Scope for Award Details",
                "See Prequalified List below in the Scope of Work",
                "Various ( see the link)",
                "1 Bidder was found non-compliant with mandatory requirements.",
                "Part A: Econolite Canada Incorporated Orange Traffic Tacel Ltd. Fortran Traffic"]:
        assert supplier_key(raw) == "", raw

def test_a_long_real_consortium_name_is_kept_not_excluded():
    # 100+ char legitimate multi-firm name must survive (no length rule)
    name = ("Alaimo Architecture Inc., Bortolotto Design Architects Inc., "
            "Paul Didur Architect Inc., Unlimited Design Studio Inc.")
    assert supplier_key(name) != ""
