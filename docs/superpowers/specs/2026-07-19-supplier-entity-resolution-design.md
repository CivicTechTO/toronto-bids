# Supplier entity resolution (#160)

**Date:** 2026-07-19
**Status:** approved, not yet implemented
**Origin:** the 2026-07-19 multi-persona UX review of the frontend. The same legal entity appears
as many supplier rows, splitting its total and distorting the flagship "who won the most" ranking.

## 1. The problem, measured

`2489960 Ontario Inc.` — a single legal entity — appears as **12+ supplier rows**
(`2489960 ONTARIO INC`, `… O/A Kore Infrastructure Group`, `… operating as Kore …`,
`2489960 ONTARIO LTD`, `2489960 ONTARIO INCORPORATED`, JV combinations, …), so its ~$503M of
awards is split, and it ranks lower than it should. It is not alone: **41 numbered-company
numbers appear as more than one supplier row** — Trisan, Bronte, Trans Canada, Samson: the large
construction firms.

The current `supplier_key` (lowercase, strip non-`[a-z0-9 ]`, collapse whitespace, **keep legal
suffixes**) deliberately keeps `Inc`/`Ltd` so genuinely different named firms are not merged.
That is right for *named* firms but wrong for *numbered* ones, where the corporation number is the
identity and `Inc` vs `Ltd` vs `Incorporated` vs an `O/A` trade name is all noise.

The issue's suggested "strip O/A" is **insufficient**: it would not merge `2489960 ontario inc`
with `2489960 ontario ltd` (the suffix itself varies). Keying numbered firms on the **number**
does.

Two adjacent classes surfaced in the same probe:
- **Named-company trade names** with no number to anchor (`Corporate Express Canada Inc.,
  operating as Staples Business Advantage`) — foldable, but riskier.
- **Garbage supplier names** — ~115 rows that are footnote text, not names (`Please see Scope for
  Award Details`, non-compliance notes, appendix price-form blurbs).

All measurements below were run against the live plexbox DB (`bids.sqlite`, 8,022 supplier rows,
2026-07-19).

## 2. Architecture

One normalization pipeline in `linking/supplier.py`, replacing the current `supplier_key`:

```
canonical_supplier_key(raw) -> str
```

Ordered stages (first match wins):

1. **Garbage → `""`** (Rule 3). A blank/garbage key returns `""`, which `build_supplier_dimension`
   already skips — no supplier row created, the award keeps `supplier_id` NULL.
2. **Corp number adjacent to a province token, anywhere → `#<number>`** (Rule 1).
3. **Named trade-name marker, guarded strip → key on the legal base** (Rule 2).
4. **Otherwise → today's normalization** (lowercase, strip non-`[a-z0-9 ]`, collapse whitespace,
   keep legal suffix) — unchanged, conservative default.

`build_supplier_dimension` is otherwise untouched; it clears and rebuilds the dimension every
sync, so there is no migration and a rule change simply re-groups on the next run. The `variants`
array (already built from every raw name in a group) makes every merge auditable from the export.

## 3. Rule 1 — numbered companies (high confidence)

**Rule.** If a corporation number (`\d{6,7}`) appears immediately adjacent to a province token
(`ontario|ont|canada|quebec|…`) **anywhere** in the name, the key is `#<number>`.

- Leading form (`2489960 Ontario Inc. o/a Kore`) and reverse-order form
  (`TRISAN CONSTRUCTION O/A 614128 ONTARIO LTD`) both key to the same `#<number>`, so word order
  no longer splits a firm.
- **Measured:** 39 numbered-company groups collapse (94 duplicate rows removed); `2489960` →
  **$458.0M → $503.4M**; the 11 groups a crude check flagged as "different trade names" were all
  the **same** firm with typos/truncations (`transcanada`/`transcanadac`, `trisan`/`trisanconstr`,
  a `0/A` zero-for-O typo) — **effectively zero real false-merges.** The corporation number is a
  unique legal identifier; two different firms never share one.
- **Stated caveat (JV attribution).** A joint venture led by a numbered company
  (`2489960 … o/a Kore Infrastructure Group /JV Rabcon Contractors Ltd. and CG Construction`)
  folds into the **lead** numbered firm. This is a deliberate attribution choice — the lead firm —
  not a bug, and it is documented in the methodology (§6).
- **Guard.** Require the province token adjacent to the digits so a stray number (a contract
  number, a second corp number in a parenthetical like `(3059515)`) is not mistaken for the
  identity — the leading/adjacent corp number wins.

## 4. Rule 2 — named-company trade-name folding (medium, guarded)

**Rule.** For a name with **no corp number** (Rule 1 owns those), strip a trailing trade-name
marker and everything after it — `O/A` (and `0/A` typo), `operating as`, `d.b.a.`/`dba`,
`c.o.b. as`/`cob as`, `trading as`, `t/a` — and key on the legal base before it.

**Guards (both required, or the strip is refused and stage 4 runs instead):**
- The stripped base must **not be generic**: a denylist of fragments that are not firms —
  `ontario ltd`, `ontario limited`, `ontario inc`, `ontario incorporated`, bare `inc`/`ltd`/
  `limited`, `canada inc`, etc. (This blocks the `'Ontario Ltd. o/a Trans Canada'` → `ontario ltd`
  over-merge.)
- The base must retain **at least one meaningful token** after legal-suffix removal (a length /
  token-count floor).

**Measured:** ~195 named-marked rows; ~22 groups collapse (Corporate Express→Staples,
R.O.M.→Ross Clair, Holcim→Dufferin, Bell Canada). A **false-merge audit** (do any two distinct
legal bases collapse together?) is part of the implementation gate, not assumed.

## 5. Rule 3 — garbage-name exclusion (safe)

**Rule.** A supplier name that is footnote/non-name text keys to `""` (skipped — no supplier row,
award keeps `supplier_id` NULL: an honest "unknown", not an invented firm).

**Detection** is **pattern-first**, length only as a weak secondary signal (real firm names reach
~71 chars via `d.b.a.`/`operating as` aliases, so length alone false-excludes): phrases like
`please see`, `see the link`, `refer to`, `as per`, `non-compliant`, `bidder was found`,
`scope of work`/`scope for award`, `prequalified`, `appendix … price form`,
`corrected for mathematical`. The exact pattern set is finalized against the flagged rows with a
**false-exclude audit** (eyeball the flagged set for any real firm name).

**Measured impact:** ~115 flagged rows, but only **12 awards / $2.9M** link to them — excluding
them costs essentially nothing in the aggregates, and they are genuinely non-firms.

## 6. Methodology documentation (required deliverable)

Reviewers flagged that firm-level spend aggregation is currently **unassessable** because the
matching method is unstated. A methodology note
(`docs/supplier-entity-resolution.md` or a section the export/docs reference) states:

- Each rule, in plain language, with an example.
- The **measured numbers**: rows collapsed per rule, recall, **false-merge count**,
  **false-exclude count** — the actual figures from the live audit, not estimates.
- The **JV attribution** choice (Rule 1 folds a numbered-lead JV into the lead firm).
- The **residual known limits**: named firms still split by `Inc` vs `Ltd` (deliberate — no number
  to anchor), and any groups the audit leaves unmerged.

This doc is as much the deliverable as the code; a firm total is only trustworthy if the reader
can see how variants were matched and what the false-merge risk is.

## 7. Data flow

`canonical_supplier_key` (pure) → `build_supplier_dimension` groups raw names by it, clears and
re-backfills `supplier_id` FKs each run → export's `suppliers[]` carries the canonical
`display_name` + full `variants[]` → the frontend supplier pages show one correct total per firm.
No schema change; the dimension is rebuilt, not migrated.

## 8. Error handling

- `canonical_supplier_key` is pure and total: any input (None, blank, garbage, exotic Unicode
  already stripped by the non-`[a-z0-9 ]` pass) returns a string; `""` means "no supplier",
  handled by the existing skip.
- A rule must never raise inside `build_supplier_dimension` (which runs as an isolated linking
  pass); the function stays exception-free over arbitrary strings.

## 9. Testing

- **Unit tests per rule**, with **real examples including the traps**:
  - Rule 1: leading + reverse-order (`614128 … O/A Trisan` and `Trisan O/A 614128 Ontario Ltd`)
    → same `#614128`; `Inc`/`Ltd`/`Incorporated` variants of one number → one key; a stray
    parenthetical number does not steal the key.
  - Rule 2: `Corporate Express … operating as Staples` folds; `Ontario Ltd. o/a Trans Canada`
    is **refused** (generic base → stays split, not merged into `ontario ltd`).
  - Rule 3: footnote strings excluded; a **real long name** (~71 chars with `d.b.a.`) is **kept**.
- **Mandatory live-measurement gate** before "done": rerun the collapse count, the false-merge
  audit (numbered + named), and the false-exclude audit on the real DB, and record the figures in
  the methodology doc. Per #136/#138, the offline fixtures are a starting point, not proof.

## 10. Out of scope

- Cross-firm resolution beyond exact-number / marker rules (e.g. fuzzy name matching, parent/
  subsidiary rollups) — a separate, higher-risk effort.
- Salvaging a real name buried inside a garbage string (Rule 3 excludes; it does not repair).
- Any change to how awards/amounts are parsed — this is purely the supplier *dimension*.

## 11. Recording

On completion, comment on #160 with the measured before/after (rows collapsed, flagship firm
corrected, false-merge/false-exclude counts) and link the methodology doc. Note the sibling
garbage-name observation is resolved here rather than left to a separate issue.
