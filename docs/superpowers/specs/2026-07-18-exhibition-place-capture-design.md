# Exhibition Place award-report capture (#130) — design

2026-07-18. Recover Exhibition Place's post-2019 procurement record from its Board of Governors
award reports on legdocs — a no-permission-gate slice that reuses the TRCA/Zoo board-report
machinery. The Bonfire portal (the current live venue) stays out of scope pending its terms of
use (#103/#134).

## Why now, and why this is cheap

EP left the City's PMMD feed in 2019 (its 72 spine rows stop at 2019-08-27) when it adopted its
own Bonfire portal, so everything since is invisible to the archive. But EP's awards are approved
by its **Board of Governors**, whose staff reports and decision letters are published on legdocs
as plain-HTTP PDFs — the same "board report" shape the Zoo/TRCA capture already parses. This is
the "Exhibition Place pattern" that recurred throughout #135: the *awards* are reachable
off-platform even when the live listing venue is a gated commercial portal.

## The finding that shapes it (probed live 2026-07-18)

EP is the **Zoo capture, verbatim structure**:

- TMMIS is Akamai-gated (agenda pages 403 to plain HTTP), so meeting discovery needs the headed
  browser — exactly as the Zoo's ZB series does.
- The EP committee reference format is `YYYY.EP<meeting>.<item>` (confirmed: `2025.EP18.9`,
  `2022.EP25.13`) — the same shape as the Zoo's `YYYY.ZB<n>.<n>`, so the `bid_award_panel` prober
  generalizes directly with an `EP_TERM_STARTS` list.
- Reports are plain-HTTP legdocs PDFs (`/legdocs/mmis/YYYY/<committee>/bgrd/backgroundfile-N.pdf`),
  fetchable without a browser — verified 200 `application/pdf`.

Two report shapes, both already handled elsewhere:

1. **Award staff reports** (e.g. `backgroundfile-240943`, EP110-2023): RECOMMENDATIONS name the
   winner and amount — "The Board approve the award of Contract No. 23-079-37912 (RFT No.
   EP110-2023) to Powell Fence Limited … in the amount of $1,484,065.00" (the Zoo's "in the amount
   of $X" phrasing) — plus a structured **"Table 1: Tender Price Submission"**:
   ```
   Bidder                              Base Bid Price   Recommended Contract Price
   Powell Fence Limited                $1,484,065.00    $1,484,065.00
   M.J.K. Construction Incorporated    $1,619,001.00
   Clearway Construction Incorporated  $1,851,100.00
   ```
   One row per bidder: name + first `$amount` (the base bid). The winner's row carries a second
   `$amount` (the recommended contract price); take the first.
2. **Decision letters with a confidential attachment** (e.g. `backgroundfile-258727`, EP18.9,
   Coca-Cola sponsorship): the winner is named publicly, the value is withheld behind a
   "Confidential Attachment … monetary value" — the Zoo's `value_confidential=1` shape, no bid
   table.

## Decisions (settled in brainstorming)

- **Scope: awards + bids with prices.** Parse the winner+amount (`agency_award`) AND the Table 1
  bidder rows with prices (`agency_bid`). EP is the first agency source with a clean structured
  price table — the archive's core "was it competitive?" value.
- **Reuse over rebuild.** New `sources/ep_board.py` reuses the `bid_award_panel`/`zoo_board`
  discovery prober, `trca_board._store_pending_pdfs` download, and the `agency_*` tables.
- **Shared pure helpers extracted.** The amount-phrase, `$`-money, and confidential-attachment
  regexes/helpers now used by both `zoo_board` and `ep_board` move to a new
  `sources/agency_report.py`; both import them. Light, test-covered, no behavior change.
- **On-demand, not nightly.** Discovery needs the headed browser (`council` extra + display), and
  nothing browser-bound is on the scheduled path — so this runs as `tb enrich-agencies --only ep
  --scrape`, like the Zoo, never in `tb nightly`.

## Architecture

### New buyer

Seed `exhibition-place` in `buyers.py`: `kind="agency"`, `partnered=0`, `platform="Bonfire"`,
notes recording that awards come from Board of Governors reports on legdocs and the portal is
gated (#134).

### `sources/agency_report.py` (new — shared pure helpers)

Extract from `zoo_board.py` (no behavior change): `_AMOUNT_PHRASE`, `_MONEY`,
`_amount_or_none(text, match)`, `_CONFIDENTIAL`, and the `_ZOO_AMOUNT` builder. `zoo_board`
imports them; `ep_board` imports them. Covered by the existing zoo tests plus a direct unit test
of `_amount_or_none` (the `$1,25 million` truncation guard).

### `sources/ep_board.py`

- **Discovery** (browser): `scrape_ep_agendas(virtual_display, log)` → `scrape_agendas(
  config.EP_AGENDAS_DIR, term_starts=EP_TERM_STARTS, …)`, and `cached_ep_agendas()`. `EP_TERM_STARTS`
  is the EP committee across its terms — the exact list (year ranges, first meeting numbers) is
  confirmed by probing at implementation time, exactly as `ZB_TERM_STARTS` was.
- **Download** (plain HTTP): index the `bgrd` PDF URLs from cached agendas via `parse_agenda_pdfs`,
  then `trca_board._store_pending_pdfs(conn, http, config.EP_REPORTS_DIR, "%/legdocs/%", …)` — the
  resilient loop (skips a dead URL, `%PDF` guard, sha256 queue).
- **Pure parsers**:
  - `parse_ep_report(text, fallback_ref) -> dict | None` → `{native_ref, title, winner, amount,
    confidential, report_url}`. Winner+amount via the shared amount phrase from a RECOMMENDATIONS
    "award … to WINNER … $AMOUNT" clause (bounded winner, no legal-suffix requirement — the #138
    lesson); `native_ref` from `RFT No. EP\d+-\d{4}` or `Contract No. <token>` (match the shape,
    not the vocabulary); `confidential=1` when a Confidential Attachment is present (amount then
    NULL, winner kept). Returns None when nothing meaningful is extracted (the Zoo empty-refusal
    rule).
  - `parse_ep_bid_table(text) -> list[tuple[str, str]]` → `(bidder, price)` per Table 1 row. Read
    the lines after the "Table 1: Tender Price Submission" header until a blank/section boundary;
    each row is `<bidder name> … <first $amount>`. Skip the column-header rows ("Base Bid Price",
    "Received", "Recommended Contract Price") and refuse a line without a clean name+price (the #94
    refuse-rather-than-guess rule).
- **Store** `store_ep_reports(conn, buyer_id) -> dict`: per held report, upsert one
  `AgencySolicitation` (overwrite=False), one `AgencyAward` per winner (with `value_confidential`),
  and one `AgencyBid` per Table 1 row (`bid_price` verbatim, `bid_price_numeric` via the model).
  Returns counts.

### CLI

Extend `_cmd_enrich_agencies`: `--only ep` runs EP; `--scrape` discovers EP agendas via the
browser (implies fetching the legdocs PDFs); default is offline (parses cached reports). Per-body
isolated exactly like trca/zoo — EP failing never stops the others. `build_supplier_dimension`
already spans `agency_award`/`agency_bid`, so EP winners and bidders enter the supplier dimension
with no change.

## Data flow

`scrape_ep_agendas` (browser, cached) → `parse_agenda_pdfs` (bgrd URLs) → `_store_pending_pdfs`
(plain-HTTP PDFs + `pdftotext`) → `parse_ep_report` + `parse_ep_bid_table` (pure) → `store_ep_reports`
(agency_solicitation/award/bid) → `build_supplier_dimension` → export's `buyers` section surfaces
EP with no export change.

## The 2011-2019 coexistence (#130's open question, answered)

The archive's 72 pre-2019 EP rows live in the City spine (`solicitation`, Client_Division
"Exhibition Place", keyed on the 10-digit `document_number`). These post-2019 Board-of-Governors
awards live in `agency_*`, keyed on the EP ref (`EP110-2023` / a contract number). **They are
separate keyspaces and coexist; no join is attempted** — the same discipline that keeps the agency
tables out of the City spine (#96/#135). Exhibition Place's record is therefore in two places by
era, which is accurate: it *was* a City-fed division through 2019 and *is* a self-procuring board
after.

## Error handling

- Per-body isolation in the CLI (EP failing never stops trca/zoo or supplier linking), mirroring
  the existing pattern.
- Download resilience via `_store_pending_pdfs` (one dead legdocs URL is skipped, not fatal).
- Parsers refuse rather than guess (return None / drop an ambiguous table row) — a wrong award or
  a mangled bidder is worse than none.

## Testing

Offline, fixture-based. Record **several real EP reports** as fixtures — an award-with-Table-1
(240943), a confidential decision letter (258727), and 3-4 more spanning years/shapes — captured
via legdocs (plain HTTP). TDD:

- `parse_ep_report`: award winner+amount, confidential-value case (winner kept, amount NULL),
  ref-shape extraction (EP###-YYYY and Contract No.), empty-refusal.
- `parse_ep_bid_table`: the Powell/M.J.K./Clearway rows extract as three (bidder, price) pairs;
  header rows skipped; a wrapped/short line refused.
- `store_ep_reports`: rows land, confidential award has NULL amount + kept winner, bids carry
  numeric prices.

**Per the #136/#138 lesson, before declaring done, run a live `tb enrich-agencies --only ep
--scrape` and measure award/bid extraction against a real sample of EP reports — the offline
fixtures are a starting point, not proof.** Browser discovery stays untested by unit tests, as
with the Zoo.

## Out of scope

- The EP Bonfire portal (gated, #134) and MERX cross-posts.
- Any join between the pre-2019 spine slice and the EP-series awards.
- Non-award EP reports (governance, budgets) — the parsers refuse them.

## Recording

On completion, comment on #130 with what landed (awards + bids recovered, the coexistence answer)
and close it; note the shared-helper extraction on #135's Zoo lineage if relevant.
