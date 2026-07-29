# H2 — drift robustness: does a general LLM extractor survive reformatting that breaks regex?

The load-bearing experiment for the general-extractor architecture. The maintainability argument
("when the City changes formatting, bespoke parsers break and a general model adapts") is
normally assumed. Here it is measured.

**Setup.** 15 EP reports, 51 ground-truth rows (ground truth = `parse_ep_bid_table` on unmutated
cells, validated in #151: 0 junk, 0 duplicates, 32/35 vs declared counts). Both extractors get
byte-identical input. Model: Qwen3-1.7B, bf16, CPU (4 threads), **schema-constrained decoding**
via outlines. ~23 s/doc. Mutations applied to the cell grid, each mimicking a plausible City
reformat rather than an adversarial trick.

| condition | regex | LLM | degradation (regex / LLM) |
|---|---|---|---|
| baseline | 100% | 84% | — |
| `rename_header` (control) | 100% | 90% | +0% / +6% |
| `reorder_columns` | **0%** | 69% | **−100%** / −16% |
| `currency_suffix` | **12%** | 78% | **−88%** / −6% |
| `extra_column` | **0%** | 84% | **−100%** / +0% |

## Result: H2 passes clearly

Three of four reformats destroy the parser — and it is the **hardened** cell parser built in
#151, not a strawman regex. The same reformats cost the model 0–16 points. Inserting a single
column takes regex from 100% to zero and costs the model nothing.

## Caveats, both load-bearing

- **Baseline accuracy is not archive-grade.** 84% recall, ~83% precision (8 of 51 rows missed,
  ~9 emitted rows wrong). Too high an error rate for a public record. This is a model-size
  finding, not an architecture finding: 1.7B was chosen to fit an existing venv.
- **One invariant wobbled.** LLM recall ROSE 6 points on `rename_header`, and a rise under
  mutation is exactly the signature that invalidated the first run. Benign explanation, and it
  fits: the baseline error was the model reading the empty `Recommended Contract Price` column
  instead of `Base Bid Price Received`, and renaming both to one unambiguous `Submitted Amount`
  removes that ambiguity. 43→46 rows, not 12→41. Accepted, but recorded rather than forgotten.

## Two invalid runs preceded this one — both caught by invariant, not inspection

1. **Unconstrained JSON.** Qwen3-1.7B emitted malformed JSON (`["Powell Fence Limited",$1,484,065.00",...`),
   the harness discarded it, and recall appeared to rise 18%→75% under corruption. Impossible,
   therefore diagnostic. **Constrained decoding is a baseline requirement here, not an
   optimisation** — the very first, simplest table produced broken quoting. With thinking mode
   on, the same model read the table correctly in prose: comprehension was never the bottleneck,
   serialisation was.
2. **A patch that silently failed** while the relaunch fired anyway, re-running the old
   unconstrained code.

Plus, earlier in the same evaluation, a GLiNER2 harness bug returning `0/101` (144 rows for 144
documents — the round number gave it away).

**Three apparatus failures, zero of them the model's fault, each caught only because a number
was impossible rather than merely wrong.** That is the most transferable finding here: a general
extraction layer needs ground-truth invariants attached to every stage, and they are needed at
least as much for the pipeline as for the model.

## The decisive open question

Robustness is established. Accuracy is a model-size problem, and the volume numbers say we can
buy our way out: steady state is **under a dozen documents/day** (Award Summary Forms ~30/month,
agency board reports ~400/year), so a 14B Q4 (~9 GB, fits 15 GB RAM) costs ~10 minutes a night.
Backlog is 6,150 documents, one-time, resumable.

- If a 14B reaches ~98% with 0–16% drift degradation → replacing ~2,000 lines of parser is
  justified.
- If it plateaus near 85% → hybrid: model for robustness-critical and long-tail sources,
  deterministic parsers where they already measure clean (EP cells 100%, Award Summary Forms
  229/229).

Testing that needs GGUF + llama.cpp; the transformers path will not run a 14B at usable speed.

## Addendum: the same experiment at 14B (2026-07-29)

Identical harness, identical 15 docs / 51 rows, so directly comparable. Qwen3-14B-Q4_K_M via
llama.cpp, 4 threads, schema-constrained. ~31 s/doc, 2,792 s total.

| condition | regex | 14B | vs baseline |
|---|---|---|---|
| baseline | 100% | **100%** (51/51) | — |
| `rename_header` | 100% | **100%** | +0% |
| `reorder_columns` | **0%** | 96% | −4% |
| `currency_suffix` | **12%** | **100%** | +0% |
| `extra_column` | **0%** | **100%** | +0% |

**Both hypotheses pass at 14B.** Accuracy is archive-grade on this corpus (51/51 exact, 52
emitted against 51 truth — precision essentially clean), and worst-case drift degradation is
−4% against regex's −88% to −100%.

This confirms the 1.7B reading: the 84% ceiling was model size, not the approach. It also
confirms the volume arithmetic — 31 s/doc against under a dozen documents a day is roughly
**5 minutes a night**.

**What this does NOT establish.** The scope is one corpus (EP), 15 documents, 51 rows — ruled
tables, one document per award. The two structurally harder cases are untested at 14B:
`award_summary` (212 docs / 1,120 rows) and above all TRCA, where a document is a *meeting
package* holding many awards and the incumbent's bidders live in prose. H1 (generality across
corpora) was planned alongside H2 and has not been run. An architecture decision on the strength
of EP alone would be generalising from the easiest case.
