# Supplier entity resolution (`supplier_key`)

`toronto_bids/linking/supplier.py:supplier_key` maps raw supplier strings across `award`, `noncompetitive`, `suspended_firm`, `bid`, `composite_award`, `agency_award`, and `agency_bid` to one canonical `supplier` row per firm.

Raw names vary across sources and years (numbered Ontario companies under trade names, `o/a`/`dba` aliases, or scraper artifacts like `"Appendix C"` and `"* Non-compliant"`). `supplier_key` applies four staged rules in order (first match wins) and never raises; it returns `""` for blank or non-firm rows, which callers skip.

## Resolution rules

1. **Strip noise wrappers, then exclude pure footnotes.** `_salvage` strips administrative wrappers (appendix markers, trailing `* Non-compliant`). What remains is checked against `_FOOTNOTE`; if it matches, `supplier_key` returns `""`.
   - `'Fermar Paving Limited* Non-compliant'` -> salvaged to `'Fermar Paving Limited'` and keyed normally.
   - `'Please see Scope for Award Details'` -> excluded (`""`).
2. **Numbered companies -> `#<number>`.** A 6-7 digit Ontario/Canada corporation number adjacent to a province token (`Ontario`, `Ont`, `Canada`) anywhere in the string anchors the firm regardless of legal suffix or trade name.
   - `'2489960 ONTARIO INC'`, `'2489960 Ontario Inc. o/a Kore Infrastructure Group'`, and `'2489960 ONTARIO INCORPORATED'` -> `#2489960`.
3. **Named-company trade-name folding (guarded).** Without a corporation number, a trailing `o/a`, `dba`, `operating as`, or `trading as` clause is dropped so the legal base becomes the key, provided the base is not a generic fragment (`_GENERIC_BASE`, such as `"ontario ltd"` or `"inc"`) and contains a non-suffix word.
   - `'Corporate Express Canada Inc., operating as Staples Business Advantage'` and `'Corporate Express Canada Inc. (operating as Staples Advantage Canada)'` -> `'corporate express canada inc'`.
   - `'Ontario Ltd. o/a Trans Canada Construction'` is not folded to `'ontario ltd'`.
4. **Default normalization.** Lowercase, keep only `[a-z0-9 ]`, collapse whitespace, and strip `(Submitted by: ...)`. Legal suffixes (`Inc`, `Ltd`, `Limited`) are kept so `'Capital Sewer Services Inc.'` and `'Capital Sewer Services Ltd.'` stay distinct.

## Joint ventures

A joint venture or trade-name alias fronted by a numbered company folds into that corporation's `#<number>` key regardless of word order (for example, `'614128 ONTARIO LTD, O/A TRISAN CONSTRUCTION'` and `'TRISAN CONSTRUCTION O/A 614128 ONTARIO LTD.'` both map to `#614128`). Unincorporated multi-firm joint ventures without a shared corporation number fall through to Rule 4.

## Known limits

- **Legal suffixes stay separate on named firms:** `'Inc'` and `'Ltd'` are not merged without a corporation number to avoid combining unrelated companies.
- **Multi-firm list strings are excluded, not split:** Concatenated strings like `'Part A: Econolite Canada Incorporated Orange Traffic Tacel Ltd. Fortran Traffic'` have no reliable delimiter and return `""` (those firms still appear if present in single-firm rows elsewhere).
- **Non-numbered JVs are not folded:** Only numbered-lead JVs match Rule 2.
- **Numbered companies without a province token:** `'614128 o/a Trisan Construction'` lacks a province token and falls through to Rule 3 (`'614128'`) instead of `#614128`.

## Baseline audit (2026-07-19, `~/tb-data/bids.sqlite`)

| Metric | Value |
|---|---|
| Raw `display_name` rows | 8,022 |
| Distinct keys after resolution | 7,732 |
| Excluded rows (footnotes / multi-firm blobs) | 51 |
| Keys merging >1 raw name | 128 (44 numbered, 84 named) |
| Duplicate rows collapsed | 239 |
| Named false-merge candidates (no shared word) | 0 |
| Excluded rows containing a firm word | 13 (all multi-firm `Part A:` / `Part B:` blobs) |
| Variants merged into `#2489960` | 12 |

## Re-running the audit

Run from `scrapers/`:

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
