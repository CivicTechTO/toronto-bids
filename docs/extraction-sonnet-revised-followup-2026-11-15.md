# Revised Sonnet 5.5 prompt follow-up — 2026-11-15

**The candidate generalizes to the new source relationships, but repeat failures still prevent promotion.** One repeat assigns an award value to an unpublished bid, and another batch repeats tender participants in the prequalification-only array. Sync/batch behavior is not consistently equivalent. Production is unchanged.

This continues [#302](https://github.com/CivicTechTO/toronto-bids/issues/302) and the [model/effort/prompt comparison](extraction-model-effort-prompt-comparison-2026-11-13.md). All **68 requests** completed successfully with distinct response IDs and reconciled raw usage, at **USD 0.786792**: 0.523992 synchronous and 0.262800 batch. Successful execution does not mean correct extraction.

## Frozen design

Sonnet 5.5, high effort, the exact preceding revised prompt (`eval-8a5ebb8c14aa`) and schema were held constant. Seven new reports and ten regression cases each ran twice synchronously and twice in separate provider batches: 17 × 4 = 68 requests. No prompt tuning or request retries followed observation of the outputs.

The seven fresh sources were EP AV upgrade, EP perimeter fence, EP Gardiner procurement-status update, TRCA automatic gates, TRCA appraisal VOR, Zoo walrus modifications and Zoo energy retrofit. They contain 22 source-reviewed relationships, or 88 observations across the four passes. Their text hashes are disjoint from every document in the preceding comparison cohort, including the inputs not selected for that comparison. The ten regression cases have 83 relationships per pass, or 332 observations.

Labels, source text, source-local aliases and request membership were validated and frozen before API execution in the [manifest and cohort](ground-truth/extraction-qualification/followups/2026-11-15/). A draft evidence quotation was corrected before freezing or sending requests: the Rutherford table interleaves a price between parts of its legal name. Original PDF hashes and fresh `pdftotext -layout` renders subsequently matched all seven frozen held-out inputs. [Source verification](ground-truth/extraction-qualification/followups/2026-11-15/source-identities.json).

This is a purposive source-level holdout with agent-reviewed labels, not an independently human-adjudicated or random archive sample. Fresh sources cover EP, TRCA and Zoo; they do not establish held-out coverage for every corpus.

## Relationship results

| Partition and mode | Frozen exact correct / expected | Supplementary source-reviewed correct / expected | Source-reviewed exact documents |
| --- | ---: | ---: | ---: |
| Fresh sources, sync | 29/44 | 44/44 | 12/14 |
| Fresh sources, batch | 36/44 | 44/44 | 12/14 |
| Regression, sync | 163/166 | 164/166 | 18/20 |
| Regression, batch | 158/166 | 160/166 | 16/20 |

The primary frozen score is **386/420 exact relationships**, with 422 observed relationships. The supplementary spelling/reference review yields **412/420 correct**, with the same 422 observed relationships. Neither is an archive-wide accuracy estimate. [Complete summary](ground-truth/extraction-qualification/followups/2026-11-15/summary.json).

The supplementary review is explicitly retrospective and leaves the primary labels, scores and raw outputs unchanged. It accepts two printed reference forms: the EP fence report's full combined contract/RFT phrase and TRCA's `#10008808`. These explain all 23 held-out strict relation mismatches. It also accepts the already disclosed Zoo comma variant for the same firm. Every equivalence has literal evidence, is confined to its document, and establishes no production identity. [Source reviews and unresolved disagreements](ground-truth/extraction-qualification/followups/2026-11-15/source-reviews.json).

The four held-out structural failures are the same Gardiner status update. Its frozen negative expectation is `contracts: []`; all four outputs instead describe the ongoing, unnumbered Design-Build procurement with empty bid and award arrays. The source explicitly describes that procurement. This is an unresolved output-scope disagreement, not an invented supplier or price. The contract-array mismatch remains visible even though its relationship score is 0/0.

The six other fresh reports match every source-reviewed relationship and contract in all four passes. The AV tender and reduced award stay distinct; the walrus tender stays separate from the contingency-inclusive award; the appraisal VOR keeps its shared $300,000 estimate out of individual amounts; and the energy project does not turn its conditional $4.7m estimate into a fixed bid or award. The gates table retains three priced submissions and three explicit `no_bid` entries. Site visits and downloads do not become bids.

## Failures and mode differences

The regression set exposes failures that the previous two candidate-prompt trials did not show:

- **Unpublished price:** Zoo fence, synchronous repeat 2, copies the $1,639,615.00 award into the bidder's unpublished submitted price. Across four passes of the five protected unpriced participations, 19/20 remain correctly unpriced; one does not. All twenty names are retained.
- **Compliance inference:** D04, synchronous repeat 1, marks Bell Canada compliant where the frozen source label is `not_stated`. The source reports the highest evaluation ranking and satisfaction with subsequent services, rather than an explicit tender compliance finding.
- **Stage membership:** D01, batch repeat 2, places R&M and Bronte in `prequalified` even though both also appear as invited firms and actual tenderers. The schema defines that array as prequalification-only. These are two extra relationships, not additional companies or missing prices.
- **Tax interpretation dispute:** Both batch repeats use `unknown` for the D01 award and both D02 awards, producing six exact mismatches. The award clauses say “plus applicable taxes”; the matching bid tables say “Fee (Plus HST)”. Frozen labels use `plus_HST` based on the source context, while the revised instructions prohibit inferring a basis from another amount. These six differences require a source-label/prompt-policy decision; they are not classified here as definite factual tax errors. Primary labels and scores stay unchanged.

After the disclosed printed-form equivalences, all four relationship outputs agree on 13/17 documents, including every fresh source. Zoo fence, D01, D02 and D04 vary; their observed sync/batch variant sets differ. Two repeats per mode cannot establish population-level parity or a statistically reliable ranking of execution modes. The positive BD and negative WSIB controls pass in all four observations.

The candidate therefore fails the existing exact, repeatable relationship threshold even before resolving the tax-policy and negative-control scope questions. Clearer instructions remain useful, but the earlier result did not establish that they eliminated unsupported-price or stage-membership failures.

## Conservation and reproduction

All 68 ledger reservations are settled as reported; none remain uncertain. Charges reconcile to retained raw responses, with the provider batch discount applied. The production extraction cache has the same before/after digest and row count. No production prompt/version, archived fact, schema, registry, cache entry or monthly limit was changed. Costs are local usage estimates rather than provider invoices. [Conservation evidence](ground-truth/extraction-qualification/followups/2026-11-15/conservation.json).

Reproduce the reports from the repository root without credentials, network or the production database:

```sh
uv run --project scrapers python \
  docs/ground-truth/extraction-qualification/followups/2026-11-15/summarize.py
```

The artifacts retain exact requests, raw sync/batch responses, receipts, raw usage and primary/supplementary measurements. The summarizer rejects changed frozen labels, overlapping holdout inputs, changed model/effort/prompt/schema, and unsupported retrospective aliases. Experimental prompts still cannot confer production qualification.

Validation: **1,883 passed, 1 skipped** with `uv run --project scrapers pytest -q`; `git diff --check` passed. Four added checks cover reproducibility, frozen-label identity, source-bound supplementary equivalence and retention of the observed unsupported-price failure.

Before promotion, settle the award-tax basis and ongoing-procurement scope rules, then test a separately versioned candidate on additional untouched sources, including missing corpora. Failures involving unpublished prices and stage membership need a remedy measured across modes and repeats. This campaign remains a completed test of the frozen candidate, not a prompt deployment.
