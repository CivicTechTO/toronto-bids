# H1 — generality: can ONE extractor with no source-specific logic replace N bespoke parsers?

Qwen3-14B-Q4_K_M, llama.cpp, schema-constrained, 4 CPU threads. 30 documents per corpus,
scored against validated rows. **Input preparation deliberately generic** — pdfplumber tables
where present, text windows otherwise, a money+company-suffix prefilter, 4 blocks max. No
caption regexes, no bullet anchors: that code is what the architecture proposes to delete, so
using it would beg the question. 8,586 s total (~95 s/doc).

| corpus | structure | recall | precision |
|---|---|---|---|
| `ep_board` | short, one money table | **98%** | **98%** |
| `award_summary` | ruled end-to-end, 1 doc = 1 award | 71% | 78% |
| `trca_board` | meeting package, 1 doc = MANY awards | **29%** | **56%** |

TRCA is scored on **names only** — all 408 stored TRCA rows have `bid_price` NULL, so there is
no price ground truth. That is the *easier* task, and it still returns 29% recall with 44% of
emitted names wrong.

## Verdict: generality does NOT hold across structures

A 69-point recall spread between EP and TRCA, from an identical model, prompt and retrieval
path. What differs is document structure, not extraction difficulty.

## But the failure is retrieval, not extraction

The same model, same quantisation, scored **51/51 exact** on EP in H2 — when it was handed the
correct table, caption-anchored. Here it must find the span itself, and:

- **EP (98%)** — short documents, essentially one money table. Generic selection lands on it.
- **`award_summary` (71%)** — the form is ruled *end to end*, so "tables containing money"
  over-selects and the 4-block cap crowds out section 5. A retrieval-density problem.
- **TRCA (29%)** — a document is a 100-to-577-page meeting package holding many awards. Four
  generic blocks cannot cover it, and an extractor with no notion of *which* award it is reading
  merges bidders across items, which is what 56% precision looks like.

**Extraction is strong; generic retrieval is weak, and retrieval is precisely the code the
architecture wanted to delete.**

## Honest caveat on the two weak numbers

The 4-block cap and the money-mention ranking are heuristics chosen in about ninety seconds, and
`award_summary` is exactly the shape they penalise. Some of that 29-point gap is my retrieval
heuristic, not the architecture. TRCA's gap is unlikely to be — no block cap saves a 577-page
package with many awards.

## Where this leaves the architecture

Combined with H2 (drift robustness: −4% worst case vs regex's −88% to −100%):

- **Full replacement is not supported.** TRCA at 29%/56% would silently corrupt the archive.
- **The measured shape that IS supported**: deterministic *location* (captions, anchors — a
  small fraction of the 6,195 lines) feeding LLM *extraction* (where nearly all 90 regexes and
  the per-corpus lore live). Evidence: EP with deterministic location scored 51/51 exact and
  degraded only 4% under reformatting that took regex to zero.
- That hypothesis (**H3: deterministic locate + LLM extract**) is untested on `award_summary`
  and TRCA. It is cheap to test — locators already exist for both — and it is the architecture
  that would actually ship.

## Cost note

At ~95 s/doc with generic retrieval, a 6,150-document backlog is ~162 hours. With deterministic
location (one block per document, ~31 s) it drops to ~53 hours. Steady state is unaffected
either way — under a dozen documents a day is minutes.
