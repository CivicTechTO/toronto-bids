# Supplier Entity Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the same legal entity splitting across many supplier rows — canonicalize numbered companies to their corporation number, fold guarded named-company trade names, salvage/exclude garbage names — and document the entity-resolution methodology with measured false-merge/false-exclude counts.

**Architecture:** Rewrite `supplier_key(raw)` in `linking/supplier.py` into an ordered staged pipeline (salvage → exclude → number → guarded-marker → default). `build_supplier_dimension` is unchanged and rebuilds the dimension each sync, so the rule change re-groups on the next run with no migration. A methodology doc records the live-measured figures.

**Tech Stack:** Python 3.12, `uv`, pytest (offline, fixture-based). The live-measurement gate runs against `~/tb-data/bids.sqlite` on the box.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-supplier-entity-resolution-design.md`.
- **No lint/format/typecheck exists** — the only check is `uv run pytest` from `scrapers/`.
- **`supplier_key` stays pure and total**: any input (None/blank/garbage/exotic) returns a string; `""` means "no supplier" and `build_supplier_dimension` already skips it. It must never raise (it runs inside an isolated linking pass).
- **Named companies keep their legal suffix by default** (the conservative existing behavior): `supplier_key("Capital Sewer Services Inc.") != supplier_key("Capital Sewer Services Ltd.")` MUST still hold. Only the numbered/marker/garbage rules deviate.
- **No length-based garbage rule** — legitimate consortium names run 100-184 chars.
- The public function name stays **`supplier_key`** (callers in `build_supplier_dimension` and tests import it); do not rename it.
- Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE
  ```
- Branch `feat-160-supplier-entity-resolution` (already checked out). Do not commit to `main`.

## File Structure

- `scrapers/toronto_bids/linking/supplier.py` — rewrite `supplier_key` + add rule helpers/regexes (Task 1).
- `scrapers/tests/test_supplier_key.py` — extend with per-rule tests incl. traps (Task 1).
- `docs/supplier-entity-resolution.md` — methodology doc with measured figures (Task 2).

---

## Task 1: Staged `supplier_key` pipeline (all rules)

**Files:**
- Modify: `scrapers/toronto_bids/linking/supplier.py` (`supplier_key` ~lines 7-24; add regexes/helpers above it)
- Test: `scrapers/tests/test_supplier_key.py`

**Interfaces:**
- Produces: `supplier_key(raw: str | None) -> str` — same signature, new staged behavior. `""` for garbage/blank (skipped by caller). `#<number>` for numbered firms. Legal base for guarded markers. Conservative normalization otherwise.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests** — replace/extend `tests/test_supplier_key.py`. Keep the existing tests (Submitted-by strip, Inc≠Ltd, blank→""). Add these, which encode the measured traps verbatim:

```python
from toronto_bids.linking.supplier import supplier_key


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_supplier_key.py -v`
Expected: FAIL — the new behavior isn't implemented.

- [ ] **Step 3: Rewrite `supplier_key` and add the rule regexes/helpers**

In `linking/supplier.py`, replace the current regex block + `supplier_key` with this (the regexes are the final measured patterns; do not weaken them):

```python
import json
import re

from toronto_bids.models import Supplier
from toronto_bids.store import db

_SUBMITTED_BY = re.compile(r"\(\s*submitted by:.*?\)", re.IGNORECASE)
_NON_KEY = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")

# Rule 3 salvage: strip appendix / price-form / trailing non-compliant noise wrappers.
_NOISE = re.compile(
    r'\(?\s*appendix\s*["“‘\']?\s*[a-z]?\d*\s*["”’\']?\s*[–-]?\s*(?:price\s*form)?\s*\)?',
    re.IGNORECASE)
_NONCOMPLIANT = re.compile(r'\*?\s*non-?compliant.*$', re.IGNORECASE)

# Rule 3 exclude: pure footnote (no firm identity). Anchored — matches only strings that START
# with a footnote phrase, so a real firm name is never excluded.
_FOOTNOTE = re.compile(
    r"^(?:please see|see prequalif|see the|refer to|as per|various\b|bid prices|"
    r"\d+\s*/?\s*bidder was found|the scope of work|\d+\s+bidder\b|part [a-z]:|"
    r"corrected for mathematical|award amounts have been)", re.IGNORECASE)

# Rule 1: a corporation number (6-7 digits) adjacent to a province token, anywhere in the name.
_CORP = re.compile(r"\b(\d{6,7})\s+(?:ontario|ont|canada|quebec|qc)\b", re.IGNORECASE)

# Rule 2: a trailing trade-name marker (and everything after it).
_MARK = re.compile(
    r"\s*(?:,\s*)?(?:\bo/?a\b|\b0/a\b|\boperating as\b|\bc\.?o\.?b\.?(?:\s*as)?\b|"
    r"\bd\.?b\.?a\.?\b|\btrading as\b|\bt/a\b)\b.*$", re.IGNORECASE)
_LEGAL_SUFFIX = {"inc", "ltd", "limited", "incorporated", "corp", "corporation", "co",
                 "company", "canada", "ontario", "ont", "llp", "lp", "the", "and"}
_GENERIC_BASE = {"ontario ltd", "ontario limited", "ontario inc", "ontario incorporated",
                 "ontario", "inc", "ltd", "limited", "incorporated", "canada inc",
                 "canada ltd", "canada"}


def _normalize(text: str) -> str:
    """Today's conservative key: drop a Submitted-by note, lowercase, strip non-[a-z0-9 ],
    collapse whitespace. Legal suffixes are intentionally kept so different named firms don't
    merge."""
    text = _SUBMITTED_BY.sub(" ", str(text))
    text = _NON_KEY.sub(" ", text.lower())
    return _WS.sub(" ", text).strip()


def _salvage(text: str) -> str:
    """Rule 3 salvage: strip appendix/price-form/non-compliant noise wrappers so a real firm
    wrapped in scraped footnote noise (e.g. 'Fermar Paving Limited* Non-compliant') survives."""
    text = _NONCOMPLIANT.sub(" ", str(text))
    text = _NOISE.sub(" ", text)
    return _WS.sub(" ", text).strip(' *"')


def supplier_key(raw: str | None) -> str:
    """Deterministic entity-resolution key for a raw supplier name.

    Staged (first match wins), see docs/supplier-entity-resolution.md:
      1. salvage noise wrappers, then exclude pure footnote -> "" (caller skips).
      2. a corporation number adjacent to a province token, anywhere -> "#<number>"
         (the number IS the legal identity; Inc/Ltd/Incorporated/O-A/JV are noise).
      3. a trailing trade-name marker on a NON-numbered name -> the legal base, but only when
         the base is not generic (guards the 'ontario ltd' over-merge).
      4. otherwise today's conservative normalization (legal suffix kept).
    Returns "" for blank/garbage. Pure and total; never raises.
    """
    if raw is None:
        return ""
    salvaged = _salvage(raw)                                   # Rule 3 salvage
    if not salvaged or _FOOTNOTE.search(salvaged):             # Rule 3 exclude
        return ""
    m = _CORP.search(salvaged)                                 # Rule 1
    if m:
        return f"#{m.group(1)}"
    if _MARK.search(salvaged):                                 # Rule 2 (guarded)
        base = _normalize(_MARK.sub("", salvaged))
        if base and base not in _GENERIC_BASE and any(t not in _LEGAL_SUFFIX for t in base.split()):
            return base
    return _normalize(salvaged)                                # Rule 4 default
```

Leave `build_supplier_dimension` and everything below it unchanged.

- [ ] **Step 4: Run tests**

Run: `cd scrapers && uv run pytest tests/test_supplier_key.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Full suite**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS. If `tests/test_supplier_dimension.py` asserts a specific pre-existing key string that a rule now changes, update that assertion to the new key (only if it is genuinely a numbered/marker/garbage case; a plain named firm must be unaffected).

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/linking/supplier.py scrapers/tests/test_supplier_key.py
git commit -m "feat(supplier): staged entity-resolution key (numbered / marker / garbage)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 2: Live-measurement gate + methodology doc

**Files:**
- Create: `docs/supplier-entity-resolution.md`
- (No code change; runs the new `supplier_key` against the real DB and records figures.)

**Interfaces:** none.

This task is the #136/#138 discipline: prove the rules against the real 8,022-row dimension and write the methodology reviewers asked for. It runs on the box (`~/tb-data/bids.sqlite` exists).

- [ ] **Step 1: Run the live audit** — this script applies the *new* `supplier_key` and reports the figures the doc must cite. Run it and capture the output:

```bash
cd /home/alex/toronto-bids/scrapers && TB_DATA_DIR="$HOME/tb-data" uv run python - <<'PY'
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
# FALSE-MERGE audit: named merges with no shared significant token
def toks(d): return {t for t in _normalize(d).split() if t not in _LEGAL_SUFFIX and len(t) > 2}
fm = [(k, v) for k, v in named.items() if not (set.intersection(*[toks(d) for d in v]))]
print(f"NAMED false-merge candidates (no shared token): {len(fm)}")
for k, v in fm[:10]:
    print(f"   {k!r}: {list(v)[:3]}")
# FALSE-EXCLUDE audit: excluded rows that contain a strong firm word
fe = [d for d in excluded if re.search(r'\b(construction|paving|electric|architect|engineering|inc|ltd|limited|group|services)\b', d, re.I)]
print(f"excluded rows containing a firm word (false-exclude candidates): {len(fe)}")
for d in fe[:12]: print(f"   {d[:90]!r}")
# flagship
print("2489960 members:", sum(1 for d in rows if supplier_key(d) == '#2489960'))
PY
```

- [ ] **Step 2: Adjudicate the audits.** For every NAMED false-merge candidate, confirm it is the same firm (typo/truncation) or a real over-merge; for every false-exclude candidate, confirm it is genuine footnote or a wrongly-dropped firm. If either audit shows a *real* defect (a true false-merge, or a real firm excluded), STOP and fix the regexes in `supplier.py` (Task 1 file) before writing the doc — the doc must report the corrected, true figures. Record the final counts.

- [ ] **Step 3: Write `docs/supplier-entity-resolution.md`** with, at minimum:
  - The four staged rules in plain language, each with one example (from the spec).
  - The **measured table**: rows before → distinct keys after; per-rule collapse counts (numbered groups collapsed, named groups folded, rows excluded); the **false-merge count** and **false-exclude count** from Step 1-2 (the actual audited figures).
  - The **JV attribution** choice: a numbered-lead joint venture folds into the lead firm.
  - The **residual known limits**: named firms still split by Inc vs Ltd (deliberate — no number to anchor); multi-firm list-blobs excluded, not split; any group the audit leaves unmerged.
  - A one-line note on how to re-run the audit (the Step 1 script).

- [ ] **Step 4: Commit**

```bash
git add docs/supplier-entity-resolution.md
git commit -m "docs: supplier entity-resolution methodology + measured audit (#160)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Post-implementation

Comment on #160 with the measured before/after (rows collapsed, flagship `2489960` $458M→$503M, false-merge/false-exclude counts) and link the methodology doc.

## Self-Review

**Spec coverage:** Rule 1 numbered → Task 1 (`_CORP`, tests) ✓; Rule 2 guarded marker → Task 1 (`_MARK`+guards, tests) ✓; Rule 3 salvage-then-exclude → Task 1 (`_salvage`/`_NOISE`/`_NONCOMPLIANT`/`_FOOTNOTE`, tests) ✓; no length rule ✓ (none present); named Inc≠Ltd preserved → Global Constraint + existing test kept ✓; methodology doc w/ measured figures + JV note + residual limits → Task 2 ✓; live-measurement gate → Task 2 Steps 1-2 ✓; pure/total/never-raises → Global Constraint + `supplier_key` structure ✓.

**Placeholder scan:** none — every regex is the final measured pattern; the doc's numbers come from the Step 1 script (real figures, not placeholders).

**Type consistency:** `supplier_key(raw: str | None) -> str` unchanged signature; helpers `_normalize`/`_salvage` and the module regexes are defined in Task 1 and consumed by the Task 2 audit script (imports `supplier_key`, `_LEGAL_SUFFIX`, `_normalize` — all defined in Task 1).
