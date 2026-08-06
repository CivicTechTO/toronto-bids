# Document classification — human ground truth (2026-07-31)

Stage one of the two-stage extraction design: decide what a document IS, so that extraction runs
only on the procurement subset. 125 documents from every source the archive collects, labelled
blind.

## Files

| file | what it is |
|---|---|
| `labels-alex.json` | 125 documents, human-labelled — the ground truth |
| `documents.json` | the header blocks as shown to the labeller (source attribution withheld) |
| `rules-baseline-guesses.json` | what the rules classifier said, and the true source — **withheld from the labeller** |
| `llm-predictions.json` | per-document LLM predictions, so re-scoring never needs a re-run |
| `classify-rules.py` | the rules baseline |
| `classify-models.py` | scores LLM classifiers against the labels |
| `bow-classify.py` | bag-of-words / linear baselines, cross-validated |
| `confusion.py` | confusion matrices, and accuracy grouped by what the archive DOES with each class |
| `labelling-tool.template.html` | the labelling UI (`__DATA__` ← `documents.json`) |

## Method

- **Stratified across (source × apparent type)**, 3 per cell, then shuffled. Tests the range of
  the classifier, so the label distribution does **not** estimate real prevalence.
- **Source attribution withheld.** Knowing a document came from `bgrd` gives away that it is
  probably a contract award — and a deployed classifier will not have that hint either.
- **The rules guess was never shown**, since the rules are wrong in ways a labeller would anchor
  on.
- **"Other" was free text.** It came back **empty across all 125 documents** — the 13-category
  taxonomy covers this corpus, which is the strongest single result here.
- A separate `contains_bid_or_award` flag, independent of class, to measure the escape hatch:
  does classify-then-skip silently discard procurement data?

## Results

`proc-recall` is the metric that governs the design: a missed procurement award is never
extracted and **nothing downstream notices**. Everything else is mostly harmless, because the
schema treats most other classes identically (extract nothing).

| approach | class acc | acc on *what the archive does* | proc-recall | cost | deterministic |
|---|---|---|---|---|---|
| majority class | 24% | — | 0/22 | — | yes |
| rules baseline | 36% | — | 12/22 | free | yes |
| **char 3-5gram + LinearSVC** | **65%** | 70% | **22/22** (CV) | **free** | **yes** |
| word 1-2gram + LogReg | 58% | — | 21/22 (CV) | free | yes |
| Nemotron 3 Ultra (free) | 61–64% | 79% | **19–22/22, varies** | free | **no** |
| GPT-5.6 Luna (high) | 67% | — | 21/22 | $0.009 | no |
| GPT-5.6 Luna (xhigh) | 66% | — | 20/22 | $0.010 | no |
| GPT-5.6 Luna (max) | 63% | — | 20/22 | $0.013 | no |
| GPT-5.2-Codex (xhigh) | 62% | — | 21/22 | **$1.37** | no |

### Findings

**A bag-of-words linear model matches the LLMs.** Cross-validated on 125 documents (2-fold, so
each model trained on ~62), a character n-gram SVM gets 22/22 procurement awards with **zero
leakage in either direction** — nothing else was called a procurement award, and no award was
called anything else. The features it learns are the procurement idiom this archive already
documents: `award, contract, tender, bid, request for, net of applicable taxes, bidder`.

**Reasoning effort makes classification worse, monotonically.** Luna high 67% → xhigh 66% → max
63%, proc-recall 21→20→20, cost +50%. AA-LCR ranks those variants in the opposite order
(0.69 / 0.696 / 0.74) — a long-context reasoning benchmark does not transfer to a 2,500-character
header. More deliberation talks the model out of correct first answers.

**The most expensive model is the worst.** GPT-5.2-Codex at 160× Luna scores lowest on every
column.

**LLM results vary run to run.** Nemotron scored 22/22 on one run and 19/21 on a repeat — same
model, same documents, same prompt, and one call failed outright even at concurrency 2. A single
run is not a measurement. The SVM's 22/22 is cross-validated and repeats exactly.

**The two methods fail differently, which was not the expectation.** The SVM collapses toward the
majority class (`status_update`); the LLM collapses toward `attachment_or_map` and scored **0/7**
on `empty_or_unreadable`, where the SVM got 5/7. A near-blank page reads as "an attachment with
little text" to a model and as a distinctive n-gram signature to the SVM. The errors are
complementary, not shared.

**One confusion is shared and is probably definitional.** `governance_finance` ↔ `status_update`
(SVM 5+5, LLM 7+3). Two independent methods disagreeing symmetrically on the same pair suggests a
genuinely fuzzy boundary — a budget update is both. Both classes extract nothing, so it costs the
archive nothing, and chasing it would be chasing a definitions problem.

**Classify-then-skip did not silently discard procurement data.** Of 25 documents flagged as
containing bid/award information, 22 are `procurement_award`, 1 is `procurement_other` and **2 are
`minutes`** — exactly as the schema predicts, which is why minutes are a cross-check source rather
than a skip. No governance, status or attachment document hid an award. *Caveat: stratified
sample, so this does not bound the rate across the full 6,179.*

## Limits

- **125 documents, 2-fold CV.** Two classes have <5 examples so `k` collapsed to 2; each model
  trained on ~62. Pessimistic, and noisy — gaps under ~10 points are not meaningful.
- **11 of 13 classes appeared.** `agenda` and `meeting_package` are untested by either method.
  That is exactly where a supervised model would fail confidently and an LLM would likely cope.
- **The free tier throttles.** 20 requests/minute; `CONC=6` trips it and produced a run the
  harness correctly reported as INVALID rather than scoring as 0%. Free models run at
  concurrency 2 here, which puts the full corpus at ~50 minutes rather than a few.
