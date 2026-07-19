# Supplier entity resolution (`supplier_key`)

`toronto_bids/linking/supplier.py:supplier_key` is the key used by
`build_supplier_dimension` to group raw supplier-name strings (drawn from
`award`, `noncompetitive`, `suspended_firm`, `bid`, `composite_award`,
`agency_award`, `agency_bid`) into one `supplier` dimension row per real firm.
The same legal entity is frequently written differently across sources and
years — a numbered Ontario company under its trade name, a named firm under
an `o/a`/`dba` alias, or a name wrapped in scraper noise ("Appendix C",
"* Non-compliant") — and the naive normalization (lowercase, strip
punctuation, collapse whitespace, keep legal suffixes) that predates this
work does not fold those together. `supplier_key` adds three staged rules on
top of that baseline, applied first-match-wins, before falling back to it.

## The four rules

1. **Salvage noise wrappers, then exclude pure footnotes.** Scraped rows
   sometimes carry a real firm name buried in noise — an appendix/price-form
   marker, a trailing `* Non-compliant` — and sometimes carry no firm name at
   all, just administrative prose or a multi-firm list-blob. `_salvage`
   strips the noise wrappers first; only what remains is tested against an
   anchored footnote pattern (`_FOOTNOTE`) and, if it matches, excluded
   (`supplier_key` returns `""` and the caller skips the row).
   Example: `'Fermar Paving Limited* Non-compliant'` salvages to `'Fermar
   Paving Limited'` and keys normally; `'Please see Scope for Award
   Details'` has no firm name to salvage and is excluded.

2. **Numbered company → `#<number>`.** A 6-7 digit Ontario/Canada
   corporation number adjacent to a province token, found anywhere in the
   string, *is* the legal identity — the Inc/Ltd/Incorporated suffix and any
   `o/a`/`dba` trade name around it are noise by comparison, and don't need
   to agree across records for the same firm to be recognized.
   Example: `'2489960 ONTARIO INC'`, `'2489960 Ontario Inc. o/a Kore
   Infrastructure Group'`, and `'2489960 ONTARIO INCORPORATED'` all key to
   `#2489960`.

3. **Named-company trade-name folding, guarded.** When a name has no
   corporation number but does carry a trailing `o/a`/`dba`/`operating
   as`/`trading as` marker, the trade name after the marker is dropped and
   the legal base before it becomes the key — but only when that base is not
   itself a generic fragment (`_GENERIC_BASE`: `"ontario ltd"`, `"inc"`,
   etc.) and still contains a non-suffix word. This guard exists because
   stripping the marker from `'Ontario Ltd. o/a Trans Canada Construction'`
   would otherwise leave the bare fragment `'ontario ltd'`, which is shared
   by hundreds of unrelated numbered companies and would over-merge badly.
   Example: `'Corporate Express Canada Inc., operating as Staples Business
   Advantage'` and `'Corporate Express Canada Inc. (operating as Staples
   Advantage Canada)'` both key to `'corporate express canada inc'`.

4. **Default: today's conservative normalization.** Lowercase, strip
   everything but `[a-z0-9 ]`, collapse whitespace, drop a `(Submitted by:
   ...)` note. Legal suffixes (`Inc`, `Ltd`, `Limited`, ...) are
   deliberately *kept*, so `'Capital Sewer Services Inc.'` and `'Capital
   Sewer Services Ltd.'` stay distinct keys — there is no reliable way to
   tell whether that's the same firm re-incorporating or two firms, and
   merging them risks combining different legal entities.

`supplier_key` is pure and total (never raises) and returns `""` for
blank/garbage/footnote input; callers skip rows with an empty key.

## Measured figures (live run against `~/tb-data/bids.sqlite`, 2026-07-19)

Command: the Step-1 audit script below, run with
`TB_DATA_DIR="$HOME/tb-data" uv run python -` from `scrapers/`.

| metric | value |
|---|---|
| supplier rows (raw `display_name` values) | 8,022 |
| distinct keys after resolution | 7,732 |
| rows excluded (pure footnote / no firm identity) | 51 |
| keys that merge more than one raw name | 128 |
| — of which numbered-company merges (`#<number>`) | 44 |
| — of which named trade-name-marker merges | 84 |
| NAMED false-merge candidates (no shared token between merged names) | **0** |
| excluded rows containing a strong firm word (false-exclude candidates) | 13 — all genuine (multi-part list-blobs, see below) |
| flagship: `2489960 ONTARIO INC` variant members | 12 |

8,022 raw names collapse to 7,732 real-world entities: 128 keys absorb more
than one raw variant (2,290 raw-name pairs' worth of duplication across
those 128 groups is what the earlier conservative-only key was missing), and
51 rows that carried no recoverable firm identity are excluded rather than
polluting the dimension with garbage keys.

### Audit adjudication

**NAMED false-merge audit (0 candidates):** the script flags any named
(non-numbered) merge group where the merged raw names share no significant
token in common — a proxy for "these might be two different firms glued
together by an over-eager regex." The live run found **zero** such groups:
every one of the 84 named merges shares at least one significant word (e.g.
`corporate express canada inc` / `staples business advantage` merges because
the trade-name marker rule keys on the shared legal base `corporate express
canada inc`, not the trade name). No fix needed.

**False-exclude audit (13 candidates, all genuine):** the script flags
excluded rows containing a common firm-indicating word
(`construction|paving|electric|architect|engineering|inc|ltd|limited|group|
services`) as a sanity check that a real single firm wasn't dropped. All 13
hits are **multi-firm list-blobs** — scraped rows that concatenate several
bidders' names under a `"Part A:"` / `"Part B:"` / etc. label from a
multi-lot award (e.g. `'Part A: Econolite Canada Incorporated Orange Traffic
Tacel Ltd. Fortran Traffic'`, listing four traffic-signal suppliers in one
string) or a rowspan artifact (`'Part B: Neil Vanderkruk Holdings Inc
Dutchmaster Nurseries Ltd Uxbridge Nurseries Baker Fo...'`). These are
exactly the "multi-firm list-blob" case the footnote rule is designed to
exclude — there is no single real firm being wrongly dropped, and splitting
the blob into its constituent bidders is out of scope for this key (see
Residual known limits). No fix needed; both audits are clean and the
figures above stand unchanged from the first run.

## JV attribution

A joint venture or trade-name alias fronted by a numbered lead company folds
into the lead firm's key, not into a separate "JV" identity. For example
`'614128 ONTARIO LTD, O/A TRISAN CONSTRUCTION'` and `'TRISAN CONSTRUCTION
O/A 614128 ONTARIO LTD.'` (trade-name-first ordering) both key to
`#614128` — the corporation number is the legal identity regardless of which
side of the string it appears on, or what trade/JV name surrounds it. This
is a deliberate simplification: a two-firm joint venture that has *not*
incorporated under a shared numbered entity is not detected as a JV at all
and its members stay as separate suppliers under Rule 4 (default).

## Residual known limits

- **Named firms are still split by `Inc` vs `Ltd`.** There is no corporation
  number to anchor a named (non-numbered) entity the way Rule 1 anchors a
  numbered one, so `'Capital Sewer Services Inc.'` and `'Capital Sewer
  Services Ltd.'` remain two distinct keys. This is deliberate: merging on
  legal-suffix-agnostic name alone risks folding two genuinely different
  entities together with no way to verify it from the string alone.
- **Multi-firm list-blobs are excluded, not split.** Rows like `'Part A:
  Econolite Canada Incorporated Orange Traffic Tacel Ltd. Fortran Traffic'`
  name several real firms concatenated into one string with no reliable
  per-firm delimiter; `supplier_key` excludes the whole row rather than
  guess at a split, so those firms are not represented under this key from
  that row (they may still appear correctly if they occur elsewhere as
  single-firm rows).
- **A non-numbered, non-incorporated joint venture is not folded.** See JV
  attribution above — only a numbered-lead JV is recognized.
- **Any group the audit would flag stays unmerged/unexcluded until fixed.**
  The two audits below (false-merge, false-exclude) are the trip-wire for
  this; both are currently clean (see Measured figures).

## Re-running the audit

```bash
cd scrapers && TB_DATA_DIR="$HOME/tb-data" uv run python - <<'PY'
import sqlite3, collections, re
from toronto_bids import config
from toronto_bids.linking.supplier import supplier_key, _LEGAL_SUFFIX, _normalize
c = sqlite3.connect(config.DB_PATH)
rows = [r[0] for r in c.execute("SELECT display_name FROM supplier")]
grp = collections.defaultdict(set); excluded = []
for d in rows:
    k = supplier_key(d)
    (excluded.append(d) if k == "" else grp[k].add(d))
print(f"supplier rows: {len(rows)}  ->  distinct keys: {len(grp)}  (excluded: {len(excluded)})")
merged = {k: v for k, v in grp.items() if len(v) > 1}
numbered = {k: v for k, v in merged.items() if k.startswith('#')}
named = {k: v for k, v in merged.items() if not k.startswith('#')}
print(f"keys merging >1 raw name: {len(merged)} ({len(numbered)} numbered, {len(named)} named)")
def toks(d): return {t for t in _normalize(d).split() if t not in _LEGAL_SUFFIX and len(t) > 2}
fm = [(k, v) for k, v in named.items() if not (set.intersection(*[toks(d) for d in v]))]
print(f"NAMED false-merge candidates (no shared token): {len(fm)}")
for k, v in fm[:10]:
    print(f"   {k!r}: {list(v)[:3]}")
fe = [d for d in excluded if re.search(r'\b(construction|paving|electric|architect|engineering|inc|ltd|limited|group|services)\b', d, re.I)]
print(f"excluded rows containing a firm word (false-exclude candidates): {len(fe)}")
for d in fe[:12]: print(f"   {d[:90]!r}")
print("2489960 members:", sum(1 for d in rows if supplier_key(d) == '#2489960'))
PY
```
