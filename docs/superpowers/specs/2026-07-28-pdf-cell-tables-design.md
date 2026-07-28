# Reading cells where the PDF has cells — EP bid tables (#151) and the parser audit (#203)

Date: 2026-07-28
Issues: [#151](https://github.com/CivicTechTO/toronto-bids/issues/151), [#203](https://github.com/CivicTechTO/toronto-bids/issues/203)

## Background

`#151` began as "the 1,500-char window in `parse_ep_bid_table` will silently truncate a large
tender". Two rounds of measurement inverted that premise:

- **Nothing is truncated today.** Longest real first table: 732 chars against a 1,500 cap;
  0 of the 47 EP reports carrying a `Table 1` header are cut off.
- **The active defect is the opposite — the window over-reaches.** It pulls in `Table 2`
  (6 duplicate bidders on `backgroundfile-254716`, a 6-bidder tender yielding 12 rows), and it
  accepts prose as rows (~11 reports store a bidder literally named
  `The recommended construction price of`, scraped from
  `The recommended construction price of $X will be funded from…`).
- Four regex fixes were tried and each failed in a *different* structural place — the named
  signature of an architectural problem rather than a patchable one.

The third round answered the fork: **EP board reports carry ruled tables in 47/47**, the Award
Summary Form profile (#116: 229/229 → cells) rather than the staff-report profile (#83: 13–20/229
→ prose, regex correctly kept). Corpus-wide, the regex produces 143 rows and the first ruled
table produces 126; the 17-row difference is exactly the contamination already identified. On the
44 reports where the regex finds bids, its first bidder appears in the cell-derived table in
**44/44**.

`#203` observes that this conclusion has now been reached twice by identical methodology, and
asks whether other regex-over-flattened-text parsers in this codebase are sitting on the same
win — as an **audit**, explicitly not a mandate to rewrite.

## Goals

1. Switch EP bid-table extraction from regex-over-`pdftotext` to pdfplumber cells (#151).
2. Remove the contamination the regex era already wrote into `agency_bid`.
3. Audit the remaining candidates (TRCA, Zoo, committee awards) by #151's methodology and switch
   each one the measurement endorses — documenting the ones it does not, so they are not
   re-litigated (#203).

## Non-goals

- **A blanket "pdfplumber is better" rule.** CLAUDE.md is explicit that this is a per-corpus
  finding: #83 measured a corpus where cells are *worse* (4/120 via the text strategy). Each
  candidate is decided on its own measurement.
- **Council staff reports (`bgrd`).** #83 already measured them as prose. That is a complete
  answer for a candidate, not a gap.
- **Capturing bid compliance markers.** Cells put `*Non-compliant` in its own column for the
  first time, but adding a column to `AgencyBid` is out of scope here; noted as follow-up.
- **Refactoring `award_summary.py`.** It is working, tested, heavily documented code carrying
  form-specific behaviour. It is not touched.

## Design

### 1. `sources/pdf_tables.py` (new)

Three corpus-independent primitives. The split exists so the anchoring rule — the genuinely new
risk here — is testable without pdfplumber and without a PDF.

```
caption_tables(path, caption_re) -> list[list[list[str]]]     # I/O only
choose_tables(caption_tops, tables) -> list[rows]             # PURE
zip_columns(name_cell, price_cell) -> list[(name, price|None)] # PURE
```

- **`caption_tables`** opens the PDF and, per page, locates each caption match's y-position and
  each table's bounding box, delegating the pairing to `choose_tables`. This is the only function
  that touches a file, mirroring `award_summary.form_rows` and the `Source` protocol's
  fetch/normalize split.
- **`choose_tables`** takes caption y-positions and `(top, rows)` pairs and returns the first
  table below each caption, in document order. Verified against `backgroundfile-254716`:

  | | y |
  |---|---|
  | caption `Table 1: Tender Price Submission` | 195.2 |
  | its table | 219.8 |
  | caption `Table 2: Tender Separate Price Submission` | 386.5 |
  | its table | 411.1 |

  A caption with no table below it on its page yields nothing rather than reaching onto the next
  table — a missing table must read as absent, never as some other table's rows.
- **`zip_columns`** is #94's rule verbatim, for the reason #94 and #116 both give: pairing is
  positional, so one stray line misattributes every bid after it.
  - One price line → **one bid**, joining the name's wrapped lines. `#116` measured that reading
    a wrapped name's two lines as two names silently dropped a bidder from each of 4 forms.
  - Many price lines → the row is a multi-package column: zip positionally and **refuse an
    unequal pair** rather than guess.

  This duplicates `award_summary._zip_cell`'s core deliberately. The alternative — rewiring
  `award_summary.py` onto the shared copy — modifies correct #116/#177 code as a side effect of
  an issue that did not ask for it. If a third caller confirms the rule is identical everywhere,
  collapsing them later is mechanical and by then covered by tests on both sides.

### 2. #151 — EP reads cells

**`parse_ep_bid_table` changes signature from `(text)` to `(rows)`** and becomes pure over
cells. A new `ep_bid_tables(path)` does the I/O via `caption_tables`.

- The caption regex `_EP_TABLE_HEAD` is **unchanged**. It already excludes
  `Table 2: Tender Separate Price Submission` (after `Table 2`, the text reads
  `Tender Separate Price Submission` — there is no `Tender Price Submission` to match), which is
  what makes "Table 1" identifiable without hardcoding the digit `1`. Where several captions
  match, the **first** is taken.
- Column 0 is the bidder, column 1 the base bid price — the current "take the FIRST `$`"
  semantics, now structural rather than positional-within-flattened-text. The header row
  (`Bidder`, `Bid Price Received`, `Recommended\nContract Price`) is skipped by its own cells,
  not by a denylist of header strings.
- Wrapped names arrive whole inside one cell
  (`Enercare Home and\nCommercial Services Limited Partnership`), so the intra-cell newline is
  collapsed rather than treated as a row boundary.
- **The 1,500-char window disappears entirely.** There is no window to size, because there is no
  window — which is what makes #151's original framing moot rather than merely fixed.
- `store_ep_reports` resolves the PDF by **basename under `config.EP_REPORTS_DIR`**, not by the
  stored `local_path`. `_store_pending_pdfs` bakes in an absolute path on whichever machine
  fetched the report, and this archive is designed to migrate (server becomes primary); the file's
  identity is its content-addressed `<sha256>.pdf` name and its location is deterministic under
  the current data dir. Same reasoning, same shape as `store_award_summary_bids`.
- A PDF pdfplumber cannot read is **logged and skipped**, never silent — the award report itself
  still stores, only its bids are absent.

Expected outcome, from the measurement: 143 rows → 126, the difference being 6 `Table 2`
duplicates and ~11 prose phantoms. **Those are rows *parsed*, not rows *stored*:** `agency_bid`
upserts on its key, so the store currently holds 115 EP rows against 143 parsed. Verification
must compare like with like — parsed against parsed, or stored against stored — or the switch
will read as a larger loss than it is. The 2 reports whose ruled table holds no bid-shaped rows
(`backgroundfile-139154`, `backgroundfile-229405`) produce 0 bids under the current regex too —
no regression.

Unrelated and deliberately not "fixed": `backgroundfile-254543` yields 0 bids because the City's
own PDF carries a malformed price (`$1,479,386,.57`, stray comma before the decimal).

### 3. Rebuilding `agency_bid` for a switched source

Switching the parser stops *new* contamination. The 13 phantom
`The recommended construction price of` rows and the truncated fragments
(`Triumph Roofing & Sheet`, `Vanguard Mechanical` beside `Vanguard Mechanical Inc.`) are already
in the store, and this archive never deletes rows.

`rebuild_agency_bids(conn, source)` is a **narrow, sanctioned exception**, in the shape of
`build_supplier_dimension` and `enrich-ariba-attachments --reindex`: `agency_bid` is *derived
from held PDFs*, so it is rebuilt from the bytes rather than diff-upserted.

**Derive first, delete only on success.** The full new row set is collected in memory; only if
derivation completed without exception *and* produced rows does the delete + insert run, inside
one transaction. A machine that does not hold the PDFs re-derives nothing and therefore deletes
nothing — the failure mode that would otherwise turn a missing corpus into data loss.

Scoped to one `source` at a time, so switching EP cannot touch TRCA's or the Zoo's rows.

### 4. #203 — the audit

Per candidate, #151's four steps, unchanged because they are what made that measurement
trustworthy rather than suggestive:

1. Ruled-table coverage on the **specific table type the parser targets** — not "any table on
   the page".
2. Corpus-wide comparison of cell-derived rows against the current parser's output: count
   agreements, and look hard at every disagreement to establish which side is contamination and
   which is real loss.
3. Validate that cells land on the **right** table (#151's 44/44 check), since a
   first-table-with-money heuristic could in principle pick up something unrelated.
4. Decide per-source.

**The switch criterion is fixed here, in advance, so the numbers decide rather than preference:**

> Switch iff (a) ruled-table coverage holds on the specific target table across the reports the
> current parser finds rows in, (b) the right-table check reproduces the current parser's first
> bidder with no unexplained mismatch, and (c) every disagreement resolves as
> contamination-removed or data-added rather than real loss. Otherwise: document the corpus as
> prose, keep the regex, and record the measurement so it is not re-litigated.

Candidates:

| source | corpus (held, with text) | current approach |
|---|---|---|
| TRCA eSCRIBE | 3,411 | regex; results table "fused pdftotext and never mined" |
| Zoo ZB legdocs | 859 | regex, shared `agency_report.py` primitives |
| committee award reports | 8 | regex, `_BID_TABLE_ANCHORS` |

**TRCA carries an anticipated sub-fork, named now rather than discovered mid-implementation.**
Its 408 `agency_bid` rows carry **zero prices** — bidders come from the bullet list and the
results table is never mined. So a successful measurement there is not a de-contamination but a
data *gain*, and the outcome may be a **union** (cells where the results table is ruled, the
bullet list as fallback for reports where it is not) rather than a replacement. That is the
measurement's call. If the numbers land ambiguously — coverage good but the two sources
disagreeing about *who bid* rather than merely about price — the choice returns to the user
rather than being settled by the implementer.

The committee corpus is 8 reports, of which #164 measured that 6 of 8 yield nothing because
**RFTs/RFQs tabulate bids while RFPs narrate them**. A ruled-table finding there is likely to be
bounded by the same ceiling; the audit records that rather than treating 8 reports as a
statistical result.

### 5. Testing

- **Pure parsers get JSON-rows fixtures**, so tests need neither pdfplumber nor a PDF — the
  `award_summary.py` discipline, and the reason its fixtures survive a dependency change.
- **`choose_tables` gets real unit tests** over synthetic `(caption_top, table_top)` geometry,
  including the no-table-below-this-caption case. This is the new logic and the one place a
  silent mis-anchor could hide.
- **`zip_columns` gets the #94/#116 cases**: wrapped name with one price (one bid), multi-package
  column (positional zip), unequal columns (refused).
- **An integration test over a held EP report**, skipped when the corpus is absent — the existing
  pattern for council tests without `pdftotext`.
- Existing EP bid-table tests are updated to the new `(rows)` signature; their *assertions* about
  what should be extracted stay, since the point is that cells extract at least as much.

### 6. Sequencing

`pdf_tables` → EP switch + rebuild → audit TRCA → audit Zoo → audit committee → CLAUDE.md +
issue comments on #151/#203.

Work lands on `fix-151-203-pdf-cells`, off `main`. This checkout is production: `main` deploys to
live systemd timers.

## Risks

| risk | mitigation |
|---|---|
| Caption anchoring picks the wrong table on an unmeasured report | `choose_tables` is pure and unit-tested; step 3 of the audit re-runs #151's right-table check corpus-wide after the switch, not just before |
| The rebuild deletes rows it cannot re-derive | Derive-first, delete-only-on-success, in one transaction, scoped to one source |
| pdfplumber is slower than regex over cached text | Measured concern from #177 (~41s over 229 forms). EP is 47 reports with a table; if the nightly cost is material, the same "already stored, skip without opening the PDF" guard #177 added applies — recorded as a follow-up if measurement shows it matters |
| The audit finds a corpus that is *partly* ruled | The criterion admits this: a union outcome is legitimate (see TRCA), but it must be measured and stated, not assumed |
