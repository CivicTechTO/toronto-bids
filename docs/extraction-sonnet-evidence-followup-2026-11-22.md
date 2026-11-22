# Evidence-backed Sonnet 5.5 candidate and expanded holdout — 2026-11-22

**The evidence-backed candidate eliminates the previously observed unpublished-price copy, `prequalified`-only overlap, and ongoing-procurement stub failures across all 72 observations, but remains unqualified for promotion.** Stricter prompt language for local tax basis and explicit compliance caused regressions on existing documents, and strict contiguous-substring `amount_evidence` validation caused false-positive rejections on multi-line table rows. Production is unchanged.

This continues [#302](https://github.com/CivicTechTO/toronto-bids/issues/302) and the [2026-11-15 revised-prompt follow-up](extraction-sonnet-revised-followup-2026-11-15.md). All **72 requests** completed successfully with distinct response IDs and reconciled raw usage, at **USD 0.985214**: 0.649629 synchronous and 0.335585 batch.

## Frozen design and schema limit

Sonnet 5.5, high effort, a separately versioned prompt (`eval-ba22775bee20`), and an experimental schema (`29ef423d8d1d`) adding `bids[].amount_evidence` were held constant. Eighteen documents — seven fresh untouched sources spanning the seven corpora absent from the 2026-11-15 holdout (`award_summary`, `ba_report`, `bd_report`, `committee`, `composite`, `ttc`, `waterfront`) plus eleven regression/control cases (the ten prior regression documents and `held-ep-status`) — each ran twice synchronously and twice in separate provider batches: 18 × 4 = 72 requests.

An initial preparation with `amount_evidence: ["string", "null"]` was rejected before execution (`HTTP 400`, `$0` charged) because Anthropic's structured-output grammar compiler enforces a hard limit of **16 union-typed parameters** and production `EXTRACTION_SCHEMA` already uses 16. Changing `amount_evidence` to plain `"string"` (empty `""` when `amount_raw` is `null`) resolved the limit cleanly without altering any production schema.

This cohort applies the two settled #302 policy rules (without modifying prior frozen cohorts):
- **Award tax basis:** strictly local (`unknown` when an award clause says *"plus applicable taxes"* without naming HST, or pre-HST *"net of G.S.T."*).
- **Ongoing-procurement scope:** `contracts: []` when a status update describes an ongoing unnumbered procurement with no bids, awards, prequalification, or invitations.

## Prompt-only vs. evidence-backed validation results

| Partition and mode | Prompt-only exact / expected | Prompt-only numeric | Evidence-validated exact / expected | Prompt-only exact docs | Evidence-validated exact docs |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fresh new corpora, sync | 47/58 | 51/58 | 47/58 | 7/14 | 7/14 |
| Fresh new corpora, batch | 48/58 | 52/58 | 48/58 | 8/14 | 8/14 |
| Regression, sync | 132/166 | 132/166 | 120/166 | 17/22 | 13/22 |
| Regression, batch | 150/166 | 150/166 | 139/166 | 15/22 | 12/22 |
| **Total** | **377/448** | **385/448** | **354/448** | **47/72** | **40/72** |

Fabrications: **0**. Duplicates: **0**. [Complete summary](ground-truth/extraction-qualification/followups/2026-11-22/summary.json).

## What improved

- **Unpublished bid prices (0 copied across 72 observations):** `zoo-fence` kept `Imperial Fence`'s bid `amount_raw = null` in **4/4** passes (previously failed in sync repeat 2). All unpriced bids in `held-ba-report`, `held-bd-report`, `held-committee`, `held-composite`, and `composite` remained `null`.
- **`prequalified`-only membership (0 violations across 72 observations):** `trca-D01` scored **10/10** exact in **4/4** passes (previously repeated R&M and Bronte in `prequalified` in batch repeat 2).
- **Settled policy alignment:** `trca-D02` scored **10/10** exact in **4/4** passes, and `held-ep-status` (Gardiner) returned `contracts: []` in **4/4** passes (previously 0/4). `ep-negative` passed **4/4**.
- **Fresh corpus generalization:** Three of the seven newly covered corpora — `held-ba-report` (`ba_report`), `held-ttc` (`ttc`, including `*`/`**` footnote stripping and `$235,100.00` bid vs. `$250,000` upset limit separation), and `held-waterfront-2017-04` (`waterfront`, 6 awards including accented and agent-awarded blocks) — scored **100% exact in all 4 passes**. `held-award-summary` matched **100% numerically** in all 4 passes (the 8 exact-string misses are PDF column-split `$ 50,777,563.11` / `50,777,563.11` formatting vs. `$50,777,563.11`).

## Remaining failures and why `evidence_validated` scored lower

1. **Contiguous-substring `amount_evidence` check caused 23 false-positive rejections:** `evidence_validated` dropped from 377/448 to 354/448 because **all 23 interventions** (`ep-bus-duct` 20/20 bids across 4 passes; `award-summary` 3/4 passes) rejected *valid* bid prices where the model concatenated a table's introductory sentence or header with a bidder's row across intervening column headers, so the combined string was not a single contiguous substring of the PDF text. Meanwhile, simply having `amount_evidence` in the schema/prompt already prevented all unpublished-price copying without needing contiguous-substring stripping.
2. **Over-strict `unknown` tax-basis spillover:** Strengthening the strictly-local tax rule fixed `trca-D01`/`trca-D02` (8/8 passes) but caused regressions where HST *was* explicitly printed: `committee` bids (`unknown` instead of `plus_HST` in 3/4 passes), `zoo-fence` award (`unknown` instead of `plus_HST` in 2/4 passes), and `bd-report` award (`unknown` instead of `net_of_taxes` in 1/4 pass).
3. **Over-strict `not_stated` compliance spillover:** Tightening compliance rules prevented the Bell Canada inference on `trca-D04` (3/4 passes 34/34; 1/4 had reference prefix `No. 10032967`), but caused `not_stated` regressions on `composite` (3/4 passes), `held-composite` (2/4 passes), and `held-committee` (4/4 passes on the two non-winning firms that passed the 75% technical threshold).
4. **Contingency-inclusive vs. base award amount:** `held-bd-report` (`$623,190.50 ($598,190.50 plus $25,000 contingency)`) split 2/4 exact (`$623,190.50`) and 2/4 base-only (`$598,190.50`).

## Conservation and reproduction

All 72 ledger reservations are settled as `reported`; none remain uncertain. Charges reconcile exactly to retained raw responses (`$0.649629` sync + `$0.335585` batch = `$0.985214`). The production `extraction_cache` has the identical before/after digest (`59e1cb00ced2449b02e7a322a044b1158172a8afc647ffa69dc5001deb015866`, 3,299 rows). [Conservation evidence](ground-truth/extraction-qualification/followups/2026-11-22/conservation.json).

Reproduce offline without credentials, network access, or the production database:

```sh
uv run --project scrapers python \
  docs/ground-truth/extraction-qualification/followups/2026-11-22/summarize.py
```

## Recommendation

Keep production on Sonnet 5.5 unchanged. The next iteration should:
1. Keep `bids[].amount_evidence` as a plain `"string"` in the schema (stays within Anthropic's 16-union limit and eliminated unpublished-price copying) while replacing contiguous-substring stripping with token/line-level amount-grounding + `prequalified`-only enforcement so multi-line tables are never falsely rejected.
2. Restore clear tax-basis and compliance examples in the prompt so explicit `"excluding HST"` / `"net of all applicable taxes"` and `"lowest bidder meeting specifications"` / `"met the 75% technical threshold"` clauses do not regress to `unknown`/`not_stated`.
3. Clarify whether contingency-inclusive contract award values (`$X ($Y plus $Z contingency)`) take the total award value `$X` with `contingency_raw = "$Z"`.
