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
→ prose, regex correctly kept).

A fourth round, run while writing this spec, completed the rule set and measured it against the
corpus's **own** ground truth rather than against the regex. Four rules — caption anchor,
page-break walk, row = name + price-or-outcome, normalize — give 47/47 tables found, 0 junk
rows, 0 duplicates, 32/35 exact agreement with declared bid counts, and **0 real bidders lost**.
It also corrected the earlier framing: the switch is not a net removal of 17 contaminated rows
but a removal of 19 alongside a **recovery of 14+ real bidders the regex was silently dropping**.
Details in §2.

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
- **A compliance FIELD on `AgencyBid`.** A non-compliant bidder is stored as a bid with
  `bid_price` NULL (§2), which is #94's existing treatment; recording *why* the price is NULL
  would need a new column and is a separate issue.
- **Chasing edge cases.** Governed by CLAUDE.md's "Parsing discipline" section: the question is
  whether a small stated rule set extracts a corpus cleanly, and if it cannot, the answer is to
  stop and report — not to add rules. See §4's stop-and-ask gate.

## Governing principle

**Future maintainability, not past compatibility.** Backwards compatibility is not merely
unneeded here — it is actively undesirable. Concretely, in this change:

- No shim, no deprecated alias, no old-signature wrapper. `parse_ep_bid_table` changes shape and
  every caller changes with it.
- **No regex fallback when the cell path finds nothing.** A "try cells, fall back to the old
  parser" arrangement would quietly reimport the exact contamination this change removes, and
  would make the contaminated path permanent by making it unreachable to testing.
- Dead code goes, rather than being left inert: `_EP_BID_ROW` and the 1,500-char window are
  deleted, not retained behind a flag.
- Where a rule now has two implementations, they are **collapsed to one** rather than allowed to
  drift — see §1.
- No one-off migration script. State that has to be corrected once is a sign the derivation
  should be re-run every time; see §3.

## Design

### 1. `sources/pdf_tables.py` (new)

Three corpus-independent primitives. The split exists so the anchoring rule — the genuinely new
risk here — is testable without pdfplumber and without a PDF.

```
all_tables(path) -> list[list[list[str]]]                      # I/O only
caption_tables(path, caption_re) -> list[list[list[str]]]      # I/O only
choose_tables(caption_tops, tables) -> list[rows]              # PURE
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

  Within a page, a caption with no table below it yields nothing rather than reaching sideways to
  some other table — a missing table must read as absent, never as another table's rows.

  **A bid table breaks across pages in two shapes, and they are one event.** Measured on the
  corpus:

  - the caption sits at the foot of its page and the whole table is overleaf
    (`backgroundfile-131331`: caption at y=705.8 on page 1, table at y=54.2 on page 2);
  - the caption's table starts on its page and its remaining rows land at the top of the next
    page as a **separate table object with no header row**
    (`backgroundfile-244929`: 2 rows on page 1, then 7 more on page 2; `backgroundfile-167548`
    likewise).

  Both are handled by one walk: take the caption's table, then keep absorbing the next page's
  first table for as long as that page has **no caption of its own** competing for it and its
  first row is a **headerless continuation** (its price column already holds a price). Without
  this, `131331` loses its only bidder and `244929` loses seven of nine.
- **`zip_columns`** is #94's rule verbatim, for the reason #94 and #116 both give: pairing is
  positional, so one stray line misattributes every bid after it.
  - One price line → **one bid**, joining the name's wrapped lines. `#116` measured that reading
    a wrapped name's two lines as two names silently dropped a bidder from each of 4 forms.
  - Many price lines → the row is a multi-package column: zip positionally and **refuse an
    unequal pair** rather than guess.

  It also drops a multi-package heading line (a name line ending in `:`, with no price beside
  it), which is part of the rule as #116 measured it rather than a form-specific quirk.

**`award_summary.py` is rewired onto these primitives.** Its `_zip_cell` is deleted and it
imports `zip_columns`; its `form_rows` becomes a thin call to the shared reader's
all-tables variant. Only what is genuinely specific to that form stays behind in
`award_summary.py` — the section-5 state machine, the `Range:`/`NOTE` skips, the numbering
strip, the declared-count check.

This reverses the initial instinct to leave that file alone on the grounds that it is working,
tested, hard-won code. That instinct optimises for not disturbing the past. The cost it accepts
is two copies of #94's positional-zip rule that are correct today and will diverge the first time
one of them is fixed — and the rule has already been re-derived independently three times
(#94's BD tables, #116's forms, now EP), which is the signal that it is one rule, not three
similar ones. One canonical implementation, with both corpora's cases in one test file, is the
maintainable shape.

### 2. #151 — EP reads cells

**`parse_ep_bid_table` changes signature from `(text)` to `(rows)`** and becomes pure over
cells. A new `ep_bid_tables(path)` does the I/O via `caption_tables`.

- The caption regex `_EP_TABLE_HEAD` is **unchanged**. It already excludes
  `Table 2: Tender Separate Price Submission` (after `Table 2`, the text reads
  `Tender Separate Price Submission` — there is no `Tender Price Submission` to match), which is
  what makes "Table 1" identifiable without hardcoding the digit `1`. Where several captions
  match, the **first** is taken.
- Column 0 is the bidder, column 1 the base bid price — the current "take the FIRST `$`"
  semantics, now structural rather than positional-within-flattened-text.
- **The header row is rejected structurally, never by a denylist of header strings.** Column 0
  is variously `Bidder` and `Tenderer`, and column 1 variously `Bid Price Received`,
  `Base Bid Price\nReceived`, `Tender Price\nReceived` and `Initial Base Bid\nPrice Received`.
  A row qualifies because its price column *holds a price*, not because its name column avoids a
  known word.
- **A price cell that holds an OUTCOME instead of a number is still a bid**, stored with
  `bid_price` NULL. The City writes `*Non-compliant` / `** Non-Compliant` in the price column —
  the same practice #94 documented on the BD agendas, where the raw string is kept and
  `bid_price_numeric` is NULL for exactly those. This is not a new rule; it is an existing one
  carried over. **It is worth 16 rows and it is what makes the corpus agree with its own declared
  counts** — without it, `238906` yields 6 against a declared 8 and `285781` yields 7 against 9,
  in both cases because the missing bidders are the non-compliant ones.
- **Prices come with and without cents, and with markers on either side.** `$4,365,534` and
  `$2,619,221` are real prices (`139154`, `229405` — the old regex required `\.\d{2}` and so
  found neither, reporting those reports as bid-free). The compliance marker attaches before the
  `$` (`*$792,900.00`) or after the amount (`$470,700.00*`); it is stripped from the stored
  price. It is **not** captured as its own field — that would need a column on `AgencyBid` and is
  a separate issue.
- Wrapped names arrive whole inside one cell
  (`Enercare Home and\nCommercial Services Limited Partnership`), so the intra-cell newline is
  collapsed rather than treated as a row boundary.
- **The 1,500-char window disappears entirely, along with `_EP_BID_ROW`.** There is no window to
  size, because there is no window — which is what makes #151's original framing moot rather
  than merely fixed. Both are deleted outright: no flag, no fallback, no inert copy. A report
  whose ruled table yields nothing yields **no bids**, and says so in the log; it does not fall
  back to the regex, which would reimport precisely the prose contamination this removes.
- `store_ep_reports` resolves the PDF by **basename under `config.EP_REPORTS_DIR`**, not by the
  stored `local_path`. `_store_pending_pdfs` bakes in an absolute path on whichever machine
  fetched the report, and this archive is designed to migrate (server becomes primary); the file's
  identity is its content-addressed `<sha256>.pdf` name and its location is deterministic under
  the current data dir. Same reasoning, same shape as `store_award_summary_bids`.
- A PDF pdfplumber cannot read is **logged and skipped**, never silent — the award report itself
  still stores, only its bids are absent.

**Measured outcome of the completed rule set**, over all 1,200 held EP reports:

| | |
|---|---|
| reports carrying a `Table 1` caption | 47 |
| of those, reports where the anchor found the table | **47/47** |
| rows extracted | **153** (regex: 143) |
| rows failing a name sanity check | **0** |
| duplicate rows within a report | **0** |
| agreement with the reports' own declared bid counts | **32/35 exact** |
| real bidders lost against the regex | **0** |

The row count *rises* rather than falls, which inverts the earlier reading of this switch as
pure removal. Both things happen at once: 13 prose phantoms and 6 `Table 2` duplicates go, and
**14+ real bidders the regex silently dropped are recovered** — firms whose name begins with a
digit (`1214592 Ontario Limited o/a Colonial Building Restoration`, `965046 Ontario Inc. o/a
Quality Allied Elevator` — the #87/#116 lesson that a numeric-leading name is a real firm),
firms whose price carries a leading marker (`*$792,900.00`, which cost `244900` its *winning*
bidder), and prices without cents.

**Those are rows *parsed*, not rows *stored*:** `agency_bid` upserts on its key, so the store
currently holds 115 EP rows against 143 parsed. Verification must compare like with like — or
the change will read as a loss where it is a gain.

**The 3 residual count disagreements are the documents, not the parser, and no rule will be
added for them** (the parsing discipline in CLAUDE.md: a disagreement with ground truth is often
the document's own defect). `238908` and `244923` say outright that they tabulate only the
compliant subset — *"four (4) submissions were received, three (3) of which were compliant"* —
and `254543` carries a malformed price in the City's own PDF (`$1,479,386,.57`, stray comma
before the decimal), which rule 3 refuses rather than guessing at. Each is logged, never silent.

### 3. `agency_bid` becomes derived-every-run, per source

Switching the parser stops *new* contamination. The 13 phantom
`The recommended construction price of` rows and the truncated fragments
(`Triumph Roofing & Sheet`, `Vanguard Mechanical` beside `Vanguard Mechanical Inc.`) are already
in the store, and this archive never deletes rows.

The tempting fix is a one-off migration that removes those known-bad rows. **That is the wrong
shape**, and the principle above says why: a table whose contents have to be corrected once by
hand is a table whose derivation should simply be re-run. A migration also encodes today's list
of known contamination, so the next parser improvement needs another one.

So `rebuild_agency_bids(conn, source)` is not a migration but the **permanent contract**:
`agency_bid` for a given source is rebuilt from the held PDFs on every store pass, exactly as
`build_supplier_dimension` rebuilds the supplier dimension from scratch every sync and
`enrich-ariba-attachments --reindex` rebuilds `ariba_attachment` from the on-disk zips. It is a
sanctioned exception to "rows are never deleted" for the same reason those are: the table is
*derived*, and the bytes it derives from are what the archive actually holds.

The existing contamination then disappears as an ordinary consequence of the first run, with no
list of bad rows written down anywhere — and every future parser fix self-heals the same way.

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
2. Find the corpus's own **ground truth** (a declared bid or item count in the report text) and
   measure the candidate rules against *that*. The incumbent parser is not a baseline; it is the
   thing under suspicion. EP's ground truth is "N submissions were received", present on 35 of
   47; each corpus needs its own equivalent located before the comparison means anything.
3. Scan the output for **junk and duplicates** — rows that are not (bidder, price) at all.
4. Compare against the current parser only to classify *disagreements*: which side is
   contamination, which is real loss, which is the document's own defect.
5. Decide per-source.

**The switch criterion is fixed here, in advance, so the numbers decide rather than preference:**

> Switch iff (a) the target table is found on effectively every report that has one, (b) the
> output carries no junk and no duplicates, (c) it agrees with the corpus's own declared counts
> except where the disagreement is traceable to the document, and (d) **no real rows are lost**
> against the incumbent. Otherwise: document the corpus as prose, keep the regex, and record the
> measurement so it is not re-litigated.

**The stop-and-ask gate (CLAUDE.md, "Parsing discipline").** This audit is explicitly *not* a
licence to chase edge cases across three more corpora. Each candidate gets a measurement pass
and **one** pass of rule refinement, with the **rule count** as the governing signal:

- **Convergent** — each new wrinkle collapses into a rule already written, and the count holds
  flat. That is what EP did: four rules in, four rules out, with the two page-break shapes
  becoming one walk and the `Non-compliant` case turning out to be #94's existing rule. Proceed.
- **Divergent** — the count climbs and each fix reveals a wrinkle elsewhere. **Stop. Do not
  write rule five. Report back to the user**, with the measurement, and default to keeping the
  regex.

"This corpus cannot be cleanly extracted" is a complete and acceptable answer for any candidate
— #83 reached exactly that for staff reports and it stands. A corpus abandoned on measurement is
a success of this audit, not a failure of it.

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

**A union is justified only by reports cells cannot reach, never as a safety net.** Keeping the
bullet-list path "just in case" would be the same past-facing instinct the governing principle
rules out: two extraction paths for one corpus, one of them unmeasured and permanent. If a union
is what the measurement supports, it ships as a **single code path with a documented
precedence** — cells where the results table is ruled, bullets where it is not, stated as a rule
someone can read — rather than two parsers whose interaction has to be reconstructed later.

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
- **The EP bid-table tests are rewritten, not adapted.** They currently feed `.txt` pdftotext
  fixtures to `parse_ep_bid_table(text)`; the new pure parser takes rows, so they get new
  JSON-rows fixtures extracted from the real PDFs. Their assertions are re-derived from the
  cells rather than carried over — some encode regex-era artifacts (a price capture "stopping
  before the `*`" describes a flattened-text hazard that does not exist when the marker has its
  own cell). The `.txt` fixtures stay, because `parse_ep_report` still reads prose and is
  correctly regex.
- **`store_ep_reports` takes its table reader as a parameter**, defaulting to `ep_bid_tables`.
  A real EP report is ~113 KB, too heavy to commit as a fixture, and the alternative — a test
  that inserts `background_pdf.text` and expects bids — would be asserting against a path that
  no longer exists. The parameter is an explicit I/O seam of the kind `Source.fetch`/`normalize`
  already draws, not a test shim: it is what lets the store pass be exercised without a PDF.
- **An integration test over the held EP corpus**, skipped when it is absent — the existing
  pattern for council tests without `pdftotext`.

### 6. Sequencing

`pdf_tables` → EP switch + rebuild → audit TRCA → audit Zoo → audit committee → CLAUDE.md +
issue comments on #151/#203.

Work lands on `fix-151-203-pdf-cells`, off `main`. This checkout is production: `main` deploys to
live systemd timers.

## Risks

| risk | mitigation |
|---|---|
| Caption anchoring picks the wrong table on an unmeasured report | `choose_tables` is pure and unit-tested; the junk/duplicate scan and the declared-count check (§4 steps 2–3) are re-run corpus-wide after the switch, not just before. Already exercised: the anchor is what keeps `139154`'s unrelated page-4 cost-breakdown table (`Item`/`Amount`/`Comments`) out of the bids, which a "first table containing money" heuristic would have taken |
| The rebuild deletes rows it cannot re-derive | Derive-first, delete-only-on-success, in one transaction, scoped to one source |
| pdfplumber is slower than regex over cached text, and §3 re-derives every run | Real tension, resolved deliberately. #177 measured ~41s per night re-parsing 229 forms and fixed it with a "already stored, skip without opening the PDF" guard — **that guard is incompatible with the rebuild contract** and must not be copied here, since skipping is what lets stale rows persist. Measured during the audit: 6.5s to open and extract all 47 caption-bearing EP reports, so the ~107 that parse as awards land around 15s per run — acceptable, and to be confirmed on the real store pass. If the cost is material — and it is far likelier to be for TRCA's 3,411 than EP's — the answer is a cache keyed on the PDF's **`sha256` *and* a parser-version stamp** — the hash alone is not enough, since it does not change when the parser is fixed, which is exactly when re-derivation matters. Bumping the stamp invalidates the whole corpus, preserving the contract. What must *not* be copied is a skip keyed on "rows already exist", which breaks it |
| The audit finds a corpus that is *partly* ruled | The criterion admits this: a union outcome is legitimate (see TRCA), but it must be measured and stated, not assumed |
