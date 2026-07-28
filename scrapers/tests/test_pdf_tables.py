from toronto_bids.sources.pdf_tables import choose_tables, is_continuation, is_price

HDR = ["Bidder", "Bid Price Received", "Recommended\nContract Price"]
R1 = ["Powell Fence Limited", "$1,484,065.00", "$1,484,065.00"]
R2 = ["M.J.K. Construction Incorporated", "$1,619,001.00", ""]


def test_is_price_accepts_the_shapes_the_city_publishes():
    assert is_price("$1,484,065.00")
    assert is_price("$4,365,534")            # no cents (backgroundfile-229405)
    assert is_price("*$792,900.00")          # leading marker (backgroundfile-244900)
    assert is_price("$470,700.00*")          # trailing marker (backgroundfile-137241)
    assert is_price("$ 449,000.00")          # space after the sign
    assert not is_price("*Non-compliant")
    assert not is_price("Bid Price Received")
    assert not is_price("")
    assert not is_price(None)


def test_choose_tables_takes_the_first_table_below_each_caption():
    # backgroundfile-254716: two captions, two tables, on one page.
    pages = [([195.2, 386.5], [(219.8, [HDR, R1]), (411.1, [HDR, R2])])]
    assert choose_tables(pages) == [[HDR, R1], [HDR, R2]]


def test_choose_tables_ignores_a_table_above_its_caption():
    # backgroundfile-139154: an unrelated cost-breakdown table sits above the caption.
    decoy = [["Item", "Amount", "Comments"], ["Exterior Windows", "373,000", "..."]]
    pages = [([427.6], [(54.3, decoy), (452.6, [HDR, R1])])]
    assert choose_tables(pages) == [[HDR, R1]]


def test_caption_at_page_foot_finds_its_table_overleaf():
    # backgroundfile-131331: caption at y=705.8 on page 1, whole table at y=54.2 on page 2.
    pages = [([705.8], []), ([], [(54.2, [HDR, R1])])]
    assert choose_tables(pages) == [[HDR, R1]]


def test_a_table_broken_across_a_page_absorbs_its_continuation():
    # backgroundfile-244929: 2 rows on page 1, 7 more on page 2 as a headerless table.
    cont = [["Crawford Roofing Corporation", "$1,660,000.00", "$1,720,000.00"]]
    pages = [([624.7], [(635.5, [HDR, R1])]), ([], [(54.2, cont[0:1])])]
    assert choose_tables(pages) == [[HDR, R1, cont[0]]]


def test_a_next_page_with_its_own_caption_is_not_absorbed():
    # The next page's table belongs to that page's caption, not to ours.
    pages = [([624.7], [(635.5, [HDR, R1])]), ([100.0], [(120.0, [HDR, R2])])]
    assert choose_tables(pages) == [[HDR, R1], [HDR, R2]]


def test_a_next_page_opening_with_a_header_is_a_new_table_not_a_continuation():
    pages = [([624.7], [(635.5, [HDR, R1])]), ([], [(54.2, [HDR, R2])])]
    assert choose_tables(pages) == [[HDR, R1]]


def test_a_caption_with_no_table_anywhere_yields_nothing():
    assert choose_tables([([100.0], [])]) == []


def test_is_continuation_needs_a_price_in_the_second_column():
    assert is_continuation([["Crawford Roofing Corporation", "$1,660,000.00", ""]])
    assert not is_continuation([HDR])
    assert not is_continuation([])
