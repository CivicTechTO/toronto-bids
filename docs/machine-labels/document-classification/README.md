# Document classification machine labels (2026-08-01)

These are machine-generated labels, not human ground truth. Keep them separate from `docs/ground-truth/document-classification/labels-alex.json`; whenever the two disagree, the human label takes precedence until verified.

## Overview

Stage-one classification (13-class `kind` + binary `contains_bid_or_award`) run over the 5,750 `background_pdf` documents outside the 125-document human sample. Subagents read full document text rather than the 2,500-character headers used in earlier API tests.

## Pipeline

1. **Calibration (`build_calib_batches.py`, `score_calibration.py`):** 3 models (`haiku`, `sonnet`, `fable`) x 2 blind passes over the 125 human-labelled documents plus 6 large unlabelled probe documents (66 agents, 17 min, `wf_86b439f2-e85`).
2. **Model selection (`haiku`):** Two independent `haiku` passes caught 22/22 procurement awards and 25/25 `contains_bid_or_award` positives across the union of both passes at much lower cost than `sonnet` or `fable`. Single-pass `kind` accuracy was similar and noisy across all three (~57-66%), motivating two passes plus a tie-breaker judge.
3. **Corpus run:** Every document was read twice by independent `haiku` subagents (980 agents, 4.0 hours, `wf_7340bd0f-d86`). Batches were size-balanced by character count and independently shuffled between passes.
4. **Honeypots:** 120 of the 125 human-labelled documents were mixed into the run under opaque IDs to measure end-to-end accuracy after judging.
5. **Judging:** 820 documents where the two passes disagreed on `kind` or `contains_bid_or_award` went to a blind third `fable` vote (69 agents + 1 patch batch for 3 missing votes, 15 min, `wf_c697e182-405`). The judge did not see the prior votes.

## Honeypot results (n = 120)

| Metric | Result |
|---|---|
| `kind` accuracy | 63% |
| Flag accuracy | 94% |
| Flag recall | 23/24 (96%) |
| Flag precision | 23/29 (79%) |

The single missed honeypot (`C088`) was a Vendor-of-Record extension ("Extension of Contract No. 10034756"), unanimously rated `procurement_other` / `flag = false` by both passes. Whether contract extensions should trigger the flag is an open schema question.

## Aggregation rule: OR for the flag, majority for `kind`

In the two-stage pipeline, `contains_bid_or_award` gates extraction while `kind` is descriptive metadata:

- **`contains_bid_or_award` uses OR (`any vote true -> true`):** Missing a real award drops it from extraction with no downstream warning, whereas a false positive only costs one extra extraction call. Switching from majority vote to OR in `assemble_labels.py` recovered 57 corpus documents where one reader spotted award language (27 with explicit `procurement_award` votes, naming suppliers and dollar figures, plus 30 correspondence/minutes/RFP items mentioning awards), moving flagged count from 1,238 to 1,295 at a cost of 2 extra false positives on honeypots (precision 23/27 -> 23/29).
- **`kind` uses majority vote (null on 3-way tie):** All 25 disputed-`kind` documents that had at least one `procurement_award` vote still reach extraction via the OR flag rule.

## Corpus summary (5,750 documents)

- **`kind` agreement:** 4,977 unanimous (87%), 637 judged to majority (11%), 136 disputed/null (2%).
- **Flagged `contains_bid_or_award = true`:** 1,295 (23%).
- **`kind` counts:** `governance_finance` 1,313, `procurement_award` 1,027, `status_update` 1,000, `attachment_or_map` 884, `agreement_or_mou` 302, `correspondence` 276, `minutes` 266, `permit_regulatory` 257, `land_property` 173, disputed 136, `procurement_other` 80, `empty_or_unreadable` 24, `meeting_package` 8, `agenda` 4.

## Cost

~93M subagent tokens across 1,116 agents (~4.6 hours total: calibration 6.35M / 66 agents / 17 min; corpus passes 80.0M / 980 agents / 4.0 h; judging 6.67M / 70 agents / 15 min).

## Files

| File | Purpose |
|---|---|
| `labels-machine.json` | Output for all 5,750 documents (`kind`, `kind_agreement`, `contains_bid_or_award`, `flag_agreement`, votes, evidence quote) |
| `export_snapshot.py` | Read-only export of `background_pdf` text under opaque IDs |
| `build_calib_batches.py` | Builds calibration batches (3 models x 2 passes x 11 batches) |
| `score_calibration.py` | Scores calibration against `labels-alex.json` and flags unanimous disagreements |
| `build_corpus_batches.py` | Builds corpus batches with honeypots injected |
| `score_corpus.py` | Joins passes and outputs patch/judge work lists |
| `assemble_labels.py` | Applies majority/OR rules and scores honeypots |

The workflow runner scripts (`wf_86b439f2-e85`, `wf_7340bd0f-d86`, `wf_c697e182-405`) are not committed; they were generated locally from the `.py` scripts above.

## Limits

- `kind` accuracy (~60-65%) was not optimized; most disagreements sit on the `governance_finance` vs `status_update` boundary, which does not affect extraction.
- Honeypot sample size is small (n = 120, 24 positives): one miss shifts recall by ~4 points.
- Not yet cross-checked against the pending 200-document human verification of the SVM predictions (`verify_public.json`).
