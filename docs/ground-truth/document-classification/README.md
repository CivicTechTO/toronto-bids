# Document classification ground truth (2026-07-31)

Blind human labels for 125 documents sampled across all archive sources, used to evaluate stage-one classification (deciding which PDFs contain procurement records before running extraction).

## Files

| File | Contents |
|---|---|
| `labels-alex.json` | 125 human-labelled documents (ground truth) |
| `documents.json` | Document header blocks shown to the labeller (source withheld) |
| `rules-baseline-guesses.json` | Rules-baseline predictions and true sources (withheld from the labeller) |
| `llm-predictions.json` | Saved per-document LLM predictions for offline re-scoring |
| `classify-rules.py` | Rules baseline script |
| `classify-models.py` | Scores LLM classifiers against ground truth |
| `bow-classify.py` | Cross-validated bag-of-words and linear baselines |
| `confusion.py` | Confusion matrices and archive-action accuracy |
| `labelling-tool.template.html` | Labelling UI (`__DATA__` replaced with `documents.json`) |

## Method

- **Stratified sample:** 3 documents per `(source, apparent type)` cell, shuffled. Because of stratification, class frequencies here do not represent overall corpus prevalence.
- **Blind labelling:** Source attribution and rules-baseline guesses were withheld so the labeller could not anchor on them.
- **Taxonomy check:** The free-text "Other" field came back empty on all 125 documents, confirming the 13-class taxonomy covers the sample.
- **Independent bid/award flag:** `contains_bid_or_award` was labelled separately from class to test whether classify-then-skip misses procurement data inside other document types.

## Results

`proc-recall` is the key metric because a missed procurement award is skipped silently, whereas most non-procurement classes share the same action (extract nothing).

| Approach | Class acc | Action acc | `proc-recall` | Cost | Deterministic |
|---|---:|---:|---:|---:|---|
| Majority class | 24% | - | 0/22 | - | Yes |
| Rules baseline | 36% | - | 12/22 | $0 | Yes |
| Char 3-5gram + LinearSVC | 65% | 70% | 22/22 (CV) | $0 | Yes |
| Word 1-2gram + LogReg | 58% | - | 21/22 (CV) | $0 | Yes |
| Nemotron 3 Ultra (free) | 61-64% | 79% | 19-22/22 (varies) | $0 | No |
| GPT-5.6 Luna (high) | 67% | - | 21/22 | $0.009 | No |
| GPT-5.6 Luna (xhigh) | 66% | - | 20/22 | $0.010 | No |
| GPT-5.6 Luna (max) | 63% | - | 20/22 | $0.013 | No |
| GPT-5.2-Codex (xhigh) | 62% | - | 21/22 | $1.37 | No |

### Key findings

- **A linear char n-gram SVM matched the LLMs.** In 2-fold CV (training on ~62 docs per fold), LinearSVC reached 22/22 `proc-recall` with zero false positives on `procurement_award`, learning standard procurement terms (`award`, `contract`, `tender`, `bid`, `request for`, `net of applicable taxes`, `bidder`).
- **Higher reasoning effort hurt classification.** Luna accuracy dropped from 67% (`high`) to 66% (`xhigh`) to 63% (`max`), with `proc-recall` falling from 21/22 to 20/22 and cost rising 50%.
- **Cost did not help.** GPT-5.2-Codex cost ~160x more than Luna and scored lower across the board.
- **LLM runs varied.** Nemotron scored 22/22 on one run and 19/21 on a repeat (with one API failure at concurrency 2). The SVM is deterministic.
- **Error modes differed.** The SVM tended to fall back to the majority class (`status_update`), while LLMs fell back to `attachment_or_map` and scored 0/7 on `empty_or_unreadable` (vs 5/7 for the SVM). Both methods confused `governance_finance` and `status_update` symmetrically (SVM 5+5, LLM 7+3), a blurry boundary where both classes extract nothing.
- **Escape-hatch check:** Of the 25 documents flagged `contains_bid_or_award = true`, 22 were `procurement_award`, 1 was `procurement_other`, and 2 were `minutes`. None were hidden in governance, status, or attachment documents (caveat: stratified sample of 125 out of 6,179).

## Limits

- **Small 2-fold CV:** Two classes had fewer than 5 examples, limiting CV to 2 folds (~62 training docs); differences under ~10 points are noisy.
- **Missing classes:** Only 11 of 13 classes appeared (`agenda` and `meeting_package` were absent and untested).
- **Free-tier rate limits:** 20 req/min; concurrency 6 triggered rate limits and produced an invalid run, so free models ran at concurrency 2 (~50 minutes for the full corpus).
