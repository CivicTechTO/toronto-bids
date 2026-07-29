# H3 — deterministic LOCATION + LLM EXTRACTION

The architecture H1's failure pointed at: keep cheap deterministic location (captions, anchors),
replace extraction (where nearly all 90 regexes live). Qwen3-14B-Q4_K_M, llama.cpp,
schema-constrained, 30 documents per corpus, grouped PER DOCUMENT.

| corpus | incumbent | H1 (generic locate) | H3 (deterministic locate) | `noloc` |
|---|---|---|---|---|
| `ep_board` | 100% | 98% / 98% | **99% / 98%** | 0 |
| `award_summary` | 100% | 71% / 78% | **60% / 92%** | 0 |
| `trca_board` | 100% | 29% / 56% | **45% / 49%** | 0 |

(Ground truth is the incumbent parser's own validated output, so the incumbent is 100% by
construction. TRCA is scored on names only — its 408 stored rows have no prices.)

**`noloc` is 0 everywhere: every remaining failure is extraction, not retrieval.**

## Verdict: LLM extraction matches the incumbent on ONE corpus of three

- **EP (99%/98%)** — parity. A short document with one caption-anchored table.
- **`award_summary` (60%/92%)** — precision improved over H1 as the span tightened, but recall
  FELL to 60%. With the correct span, no truncation (5.4 rows/doc against a 40-row window), the
  model misses 40% of the rows the existing cell parser gets.
- **TRCA (45%/49%)** — better than H1 once the double-counting artifact was fixed, but **half
  the emitted names are still wrong**, with the correct span in hand.

## The likely reason, and it is the important part

The value in these parsers is not the regex — it is the **documented exceptions**, each of which
cost real measurement to discover:

- #116: the City leaves numbered row `5.` blank; an RFP lists proponents with **no price at
  all**; a multi-package cell holds a whole column and must be zipped positionally or refused.
- #94: an OUTCOME in the price column (`Non-Compliant`) is still a bid.
- #87/#116: `2489960 Ontario Inc.` is a real firm, not a leaked price.

A general prompt does not reproduce these, and cannot: there is no way for a model to know that
the City leaves a numbered row blank rather than omitting the bidder. Capability does not fix
this; corpus knowledge does, and corpus knowledge is what the parsers ARE.

**Unverified:** whether `award_summary`'s missing 40% actually clusters on those shapes. Cheap
to check by diffing missed rows against the incumbent's, and worth doing before any migration —
it separates "buy a bigger model" from "the exceptions do not transfer".

## Where the whole evaluation lands

Across H1/H2/H3 plus the GLiNER2 bake-off:

- **Accuracy: the incumbent parsers win**, decisively on 2 of 3 corpora. Replacing the
  extraction layer would degrade the archive.
- **Drift robustness: the model wins, decisively.** H2: reformatting that takes the hardened
  parser from 100% to 0-12% costs the 14B 0-4 points.

Those are not in conflict; they describe different regimes. The parsers are better *until the
format changes*, at which point they produce nothing and the model still works.

## Recommended architecture (not full replacement, not hybrid-by-source)

1. **Keep the existing parsers as the primary path.** They measure better and encode discoveries.
2. **LLM as a drift FALLBACK**: when a parser returns zero rows, or fails its own ground-truth
   check (a declared bid count it cannot satisfy), fall back to LLM extraction and flag the row
   set for review. Cost is near zero — it fires only on failure — and it converts a City
   reformat from a silent outage into a degraded-but-working night. This directly addresses the
   drift risk, which was the actual motivation.
3. **LLM as the FIRST pass for NEW sources.** This is where the O(n)-per-source cost lives: every
   new agency has needed a bespoke parser. Start new sources on LLM extraction plus a
   ground-truth check, and write a parser only where it measurably underperforms.

(3) delivers the maintainability win going forward; (2) delivers the robustness win now. Neither
regresses the 99-100% accuracy the current parsers deliver. **Honest limitation: this does not
reduce the existing 6,195 lines** — the original goal — because the measurement does not support
deleting them.
