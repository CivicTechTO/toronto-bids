# Selecting a model by measured ordering

How to pick an LLM for a specific task when general leaderboards do not reflect your workload.

General leaderboards (such as Artificial Analysis Intelligence Index or LMArena) combine many skills into one weighted score. If your task relies on only one or two of those skills, a general price/performance frontier can either overspend on unused capability or pick a model that fails on the skill you need.

Instead, use a general index only to pick a spread of test candidates, measure them on your actual task against independent ground truth, and look for a specific sub-benchmark that orders those models the same way.

## Steps

### 1. Build a sampling frontier priced for your token ratio

Compute a cost-vs-score Pareto frontier on a general index using your real input:output token ratio. Generic "$/M tokens" rankings distort comparisons because output tokens typically cost 5-10x more than input tokens; a long-input, short-output extraction task ranks models very differently from a generation task. Use the frontier to sample models across price and capability tiers, not as a final shortlist.

### 2. Measure candidates on your real task against ground truth

- **Keep ground truth independent** of every candidate and any incumbent parser. Scoring against an incumbent parser automatically gives the incumbent 100% and penalizes candidates whenever the incumbent was wrong.
- **Pick the right metric.** If human ground truth may miss items, precision penalizes accurate models; use recall and adjudicate disagreements against the source documents.
- **Slice results structurally.** Aggregate accuracy hides failure modes. Group results by input structure (such as single-contract vs multi-contract documents) to see where models separate.

### 3. Match your ordering to a specific sub-benchmark

Find a published benchmark (starting with the components of the composite index) that ranks your tested models in the same order and has a clear mechanistic link to the skill that separated them. Check how heavily that sub-benchmark is weighted in the general index; a low weight explains why the general index misled you.

### 4. Recompute the frontier on that benchmark

Use the matched benchmark's cost/score frontier to narrow future model releases down to a small shortlist, then verify those few candidates directly on your ground-truth set (which is cheap once labels exist). Re-validate periodically and whenever input formats change.

## Failure modes to watch for

- **Flat scores (no discrimination):** If every candidate scores near 100%, test cheaper or weaker models until performance drops; a flat line cannot validate a benchmark.
- **Single-model failure:** One outlier is not a ranking and leads to overfitting. You need multiple models spread across the score range.
- **Many ties:** With small samples and ties, benchmarks can match by chance. Require a mechanistic explanation, not just rank correlation.
- **Harness bugs masquerading as model failures:** A model scoring 0% due to schema rejection or 22% due to missing retry backoff looks like low capability. Guard against this by:
  - Failing loudly on API/parse errors instead of returning empty results.
  - Auditing suspicious scores (such as exact round counts or accuracy improving on worse input).
  - Handling provider quirks (some models reject `json_schema`, require the word `"json"` in the prompt, or wrap JSON in markdown).
- **Incomplete ground truth:** Adjudicate model "false positives" against source documents before trusting precision.

## Worked example: TRCA bid extraction (2026-07)

**Task:** Extract every bidding firm, amount, and contract reference from Conservation Authority board reports (~4,500 input tokens, ~700 output tokens, 5.9:1 ratio).

**Ground truth:** Two people labelled 15 documents (~92 companies) blind from scratch with per-contract attribution.

| Model | Intelligence Index | Recall | Multi-contract | Est. backlog cost |
|---|---:|---:|---:|---:|
| Incumbent regex parser | - | 59% | 44% | $0 |
| `nemotron-3-ultra:free` | 37.8 | 99% | 100% | $0 |
| `tencent/hy3-preview` | 41.2 | 99% | 100% | ~$2 |
| `deepseek-v4-flash` | 49.9 | 74% | 65% | ~$4 |
| `gpt-5.6-luna` | 51.2 | 100% | 100% | ~$4 |
| `gpt-5.6-terra` | 55.0 | 99% | 100% | ~$10 |
| `kimi-k3` | 57.1 | 100% | 100% | ~$86 |
| `claude-opus-5` | 60.7 | 100% | 100% | ~$290 |

**Findings:**

- **The general index did not predict performance.** Models from 37.8 to 60.7 scored 99-100%, except DeepSeek (49.9) at 74%. Long-context reasoning (AA-LCR) and instruction following (IFBench) together make up only 12.5% of the Intelligence Index; the rest measures skills this task does not use.
- **Multi-contract structure separated models.** Every model scored ~96% on single-contract reports; spread appeared on multi-contract packages (100% for most vs 65% for DeepSeek and 44% for regex).
- **Pricing bug on AA-LCR:** The initial AA-LCR frontier paired max-effort benchmark scores with base per-token prices, placing DeepSeek (0.657) above Nemotron. Pricing in reasoning tokens put Nemotron (0.67) above DeepSeek, matching our measurements. Always verify that benchmark score and price describe the same configuration.
- **Where this stopped:** Because only DeepSeek failed, we did not have enough spread to fully validate AA-LCR as a proxy (confirming it would require testing models expected to fail). Even so, direct measurement immediately showed that low-cost models matched a $290 backlog run.

## When to skip this protocol

- **One-off tasks:** Measure candidates directly and skip steps 3-4.
- **No ground truth:** Build a small human-labelled set first; without it, no comparison is reliable.
- **Negligible cost:** If the entire workload costs only a few dollars on a top model (determined in step 1), optimization is not worth the effort.
