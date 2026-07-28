"""Reading tables out of PDFs that HAVE tables (#151, #203).

The rule this module serves is #116's: **read cells where the PDF HAS cells** — never
"pdfplumber is better". Whether a given corpus qualifies is a per-corpus measurement
(CLAUDE.md, "Parsing discipline"); #83 measured a corpus where cells are *worse*. This module
is only the machinery for the corpora that qualify.

Split so the risky part needs no PDF to test: `choose_tables` and `zip_columns` are pure and
carry every structural rule; `all_tables`/`caption_tables` do the I/O and nothing else — the
same fetch/normalize seam `sources/base.py` draws for a Source.
"""
import re

# A published price, with or without cents, with a compliance marker on either side, and with
# or without a space after the sign. Every shape here is live-measured on the EP corpus: the
# old regex required `\.\d{2}` and so read `$4,365,534` (backgroundfile-229405) as "no bids".
_PRICE = re.compile(r"^[*\s]*\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?[*\s]*$")


def is_price(cell) -> bool:
    return bool(_PRICE.match((cell or "").strip()))


def is_continuation(rows) -> bool:
    """True when a table opens with DATA rather than a header — the signature of a table that
    broke across a page boundary and resumed at the top of the next page."""
    return bool(rows) and len(rows[0]) > 1 and is_price(rows[0][1])


def choose_tables(pages):
    """One table per caption, page-break continuations absorbed.

    `pages` is `[(caption_tops, [(table_top, rows), ...]), ...]` in document order — the
    geometry, with pdfplumber already out of the picture.

    A caption claims the first table BELOW it on its own page. Two page-break shapes then
    complicate that, and both are the same event so both are handled by one walk:

      - the caption sits at the foot of its page and the whole table is overleaf
        (backgroundfile-131331: caption y=705.8 page 1, table y=54.2 page 2);
      - the caption's table starts on its page and the remaining rows land at the top of the
        next page as a SEPARATE table object with no header row
        (backgroundfile-244929: 2 rows, then 7).

    Absorption requires the next page to have no caption of its own competing for the table,
    and the table to open with data rather than a header. Without this, 131331 loses its only
    bidder and 244929 seven of its nine.
    """
    out = []
    for i, (captions, tables) in enumerate(pages):
        for top in sorted(captions):
            below = [t for t in tables if t[0] > top]
            j = i
            if below:
                rows = list(min(below, key=lambda t: t[0])[1])
            elif i + 1 < len(pages) and pages[i + 1][1] and not pages[i + 1][0]:
                j = i + 1
                rows = list(min(pages[j][1], key=lambda t: t[0])[1])
            else:
                continue          # a caption with no table is absent, not someone else's rows
            while j + 1 < len(pages) and pages[j + 1][1] and not pages[j + 1][0]:
                nxt = min(pages[j + 1][1], key=lambda t: t[0])[1]
                if not is_continuation(nxt):
                    break
                rows.extend(nxt)
                j += 1
            out.append(rows)
    return out


def zip_columns(name_cell, price_cell):
    """Pair one row's bidder lines against its price lines.

    Usually a row is one bidder. A multi-package tender instead puts a whole column in a single
    cell, exactly as the BD agendas did (#94). Same rule, same reason: pairing is positional, so
    one stray line misattributes every bid after it. Zip the columns and REFUSE an unequal pair
    rather than guess. A package heading is dropped first — it ends in ':' and has no price.

    **THE PRICE CELL'S LINE COUNT SAYS HOW MANY BIDS THE ROW HOLDS.** A newline inside a name
    cell is otherwise ambiguous, and guessing costs real bids: pdfplumber wraps a long name
    within its own cell, so

        ['2489960 Ontario Inc.\\no/a Kore Infrastructure Group', '$3,198,000.00']

    is ONE bidder, and reading its two lines as two names refused the pair and silently dropped
    a bidder from each of 4 forms (#116). One price, one bid — join the name.
    """
    prices = [ln.strip() for ln in (price_cell or "").split("\n") if ln.strip()]
    names = [ln.strip() for ln in (name_cell or "").split("\n") if ln.strip()]
    names = [n for n in names if not n.endswith(":")]
    if len(prices) <= 1:
        name = " ".join(names)
        if not name:
            return []
        # An RFP publishes its proponents with NO price at all ("NOTE: Not applicable for
        # RFP"). #84 already stores those as bid_price NULL — requiring a price here dropped
        # every proponent on every scored RFP.
        return [(name, prices[0] if prices else None)]
    if len(names) != len(prices):
        return []
    return list(zip(names, prices))
