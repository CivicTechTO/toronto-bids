# Paired Claude qualification — 2026-11-13

**Sonnet/high does not meet the predeclared full-relationship thresholds on this bounded cohort.** All 48 approved requests completed with valid structured responses and reconciled provenance. Recorded local usage cost is **USD 0.670469**. Production extraction-cache hashes before and after are identical. No shared prompt, default model, identity registry or backfilled fact changed.

This completes the prepared synchronous/batch campaign for [#302](https://github.com/CivicTechTO/toronto-bids/issues/302). It establishes concrete failure cases and source limits, not an archive-wide accuracy estimate or a reason to discard supported partial results. Procurement identity and decision-proof changes remain with [#282](https://github.com/CivicTechTO/toronto-bids/issues/282).

## Cohort, execution and reproduction

The 15 source-reviewed inputs span all nine active PDF corpora and Waterfront, with 114 expected source relationships. Each mode ran the full cohort once, then repeated its nine difficult inputs: 24 synchronous and 24 batch executions. All 48 response IDs are distinct. Source text, prompt version, model, effort, ordered schema and request identities agree across paired executions. All 15 inputs meet execution coverage, including every required repeat.

Four March 2023 Waterfront executions require production source-context review instead of automatic relationship scoring. Their execution and provenance are complete; their JSON is not malformed. The other 44 responses were scored. Twelve unselected repeat slots are explicit `not_selected` records. There are no missing or pending requests.

Retained evidence includes:

- [Exact requests, raw responses and receipts](ground-truth/extraction-qualification/campaigns/2026-11-13/runs/).
- [Frozen pre-trial cohort](ground-truth/extraction-qualification/cohort-2026-11-13-pretrial.json) and [original-label report](ground-truth/extraction-qualification/campaigns/2026-11-13/report-pre-review.json).
- [Reviewed cohort](ground-truth/extraction-qualification/cohort.json) and [source-adjudicated report](ground-truth/extraction-qualification/campaigns/2026-11-13/report.json).
- [Input-by-input source adjudications](ground-truth/extraction-qualification/campaigns/2026-11-13/source-adjudications.json), [count explanations](ground-truth/extraction-qualification/campaigns/2026-11-13/count-adjudications.json), [usage](ground-truth/extraction-qualification/campaigns/2026-11-13/usage.json) and [conservation](ground-truth/extraction-qualification/campaigns/2026-11-13/conservation.json).

Reproduce the reviewed report without credentials, network access or a production database:

```sh
uv run --project scrapers tb qualify-extraction \
  --cohort docs/ground-truth/extraction-qualification/cohort.json \
  --runs-dir docs/ground-truth/extraction-qualification/campaigns/2026-11-13/runs \
  --out /tmp/qualification-302.json
```

Exit 1 is expected because the cohort does not qualify. Use the frozen cohort to reproduce the original-label report. Post-trial source adjudications do not overwrite the original labels, responses or thresholds. Original human labels are unchanged.

## Source-reviewed results

Thresholds remain precision 1 and recall 1 on supplier/contract/stage/role/raw-amount/basis/status relations, with matching mode coverage and difficult repeats. A wrong attribute produces both an incorrect observation and an absent correct relation. Denominators count repeated observations, not unique archive facts. Negative controls additionally require an empty contract array.

| Corpus/path | Exact reviewed matches / expected observations | Finding |
| --- | ---: | --- |
| Award Summary Form | 2 / 4 | Values preserved; bid tax-basis coding differs from the reviewed source label. |
| BA report | 2 / 2 | Historical award preserved; rescinded-duplicate decision admission remains separate. |
| BD report | 16 / 16 | Seven HST-inclusive bids and the net-of-taxes award survive both modes. This selected corpus case meets thresholds. |
| Committee | 4 / 12 | Bid/award values remain distinct; bid tax bases are unknown despite the published HST exclusion. |
| Composite | 16 / 32 | Four initial-term awards survive every run; four known, unpriced bidder participations disappear every time. |
| Exhibition Place | 10 / 24 | Bid/award values remain distinct; batch loses tax bases. A printed footnote affects raw matching. Both WSIB negative controls pass. |
| TRCA | 174 / 216 | Stage/status differences, collapsed category participation and different treatment of an annual expense forecast. D04 is an excerpt. |
| TTC | 6 / 16 | Prices and allocations survive; HST basis is unsupported. Reference uncertainty affects this score, so it is not a factual false-join rate. |
| Waterfront | 68 / 68 scored | Eight September 2017 and nine October 2021 relationships survive all four runs. Four March responses require source-context review. |
| Zoo | 0 / 4 | Named bidder participation is omitted; award tax basis is unknown. |

Raw-format diagnostics are separate from numeric-value diagnostics, which reuse the archive's existing strict amount and bid-footnote parsers. Currency spacing and printed `*` markers do not establish different prices. Rates, foreign currencies and malformed values are not silently converted. Supplemental numeric diagnostics do not relax qualification thresholds or erase tax/status errors.

Source review establishes these distinctions:

- **Known participation is lost without inventing a price.** Composite reports establish competitive participation and name recommended bidders, but publish no individual tender prices. Every response correctly preserves four award values while returning zero bids. Zoo similarly names Imperial's submitted participation without publishing its bid price. The missing named participants are extraction omissions; unpublished losing firms are source limits.
- **Price qualifiers are separate facts.** Every EP response preserves the $418,854.47 tender versus $403,854.47 recommended award. Sync retains HST bases; batch returns unknown for all six price relationships. Committee retains bid amounts excluding contingency and the distinct recommended award, but loses bid tax-basis specificity. These are not changed numeric prices.
- **Stages and categories matter.** D01's batch responses put three actual tenderers into the schema's prequalification-only list as well. D04 collapses repeated electrical/mechanical participation and a category-specific disqualification; extraction has no category field. A supplier appearing once cannot establish complete category relationships.
- **A forecast is not an allocated award.** Both synchronous D04 responses put Bell Canada's $235,000 annual ongoing-service estimate in the award role. Batch withholds it. The original old human bid-price label disagreement remains recorded; this new discrepancy concerns the award role.
- **The oracle needed correction too.** Eight agent-written VOR winner statuses originally said `not_stated` despite explicit statements that they meet qualifications and requirements. Reviewed labels consistently use `compliant`, lowering some sync scores. Frozen labels and original reports remain available. Full printed call prefixes, MCI spellings and the committee footnote were source-reviewed instead of being declared fabricated suppliers or new procurement joins.
- **TTC's reference is ambiguous.** Sync uses the unlabelled footer `9-118-80`; batch withholds it. The footer's identifier role was already unresolved before trial, so the strict mismatch is not eight established wrong joins. Both modes nevertheless encode allocations as `including_HST` where the 2000 source states only applicable taxes.
- **The March input omits a recipient.** Manual inspection finds the four primary-source named relationships in all responses. Three withhold the dewatering recipient; one batch adds the literal placeholder `unknown`. None invents Ellis Don as recipient. WJ Groundwater is independently corroborated by a publisher dashboard, but that supplementary text was not sent to the model. The production proof binds another historical response fingerprint. No proof was transferred or gate weakened to score these outputs.

## Mode comparison and variability

Among the 14 automatically scorable inputs, nine have one identical relationship output across modes/repeats; five differ: TTC, EP bus duct, TRCA D01, D02 and D04. Two repeats per mode measure variation, not a statistical mode ranking.

Composite's participation omissions and committee's missing bid bases repeat consistently. Waterfront's nested supplier lists also repeat consistently, with all 68 scored relationships conserved. D02 varies in acceptance status and raw amount formatting. D04 varies in statuses, repeated participation and expense/award treatment. Batch is not uniformly better or worse: it withholds the unsupported Bell amount but loses EP tax qualifiers. Matching request identity does not establish output parity or correctness.

## Persistent count review

All **18 campaign count warnings** have response-bound source explanations. They remain unresolved because they mix unpublished bidder lists with omitted named participation. Source subsets do not excuse omission of the known bidder.

Separately, the unchanged production cache has four legitimate count-only subsets in the selected documents: three composite calls and Zoo. Their named bidders are present; other names are unpublished. [Persistent dispositions](ground-truth/extraction-qualification/runtime-count-reviews.json) resolve only those count gaps and explicitly retain warnings that cached bid prices copy award values without submitted-price evidence. A reviewed D01 prequalification-versus-tender declaration error remains unresolved. Provider provenance for these old cache entries is unknown; they are not Claude qualification inputs.

```sh
uv run --project scrapers tb extraction-counts \
  --reviews docs/ground-truth/extraction-qualification/runtime-count-reviews.json \
  --out /tmp/runtime-count-review.json
```

Against the dated held archive, 1,506 of 1,510 warnings remain unresolved. This is a review workload, not 1,506 proven model failures. Keys bind source hash, prompt version, complete response and discrepancy. The command opens the archive read-only. Resolving a count does not approve prices, remove facts, change admission or invalidate compatible caches.

## Usage and conservation

| Execution | Requests | Recorded USD |
| --- | ---: | ---: |
| Sync full cohort | 15 | 0.249549 |
| Sync difficult repeat | 9 | 0.196279 |
| Batch full cohort | 15 | 0.123228 |
| Batch difficult repeat | 9 | 0.101413 |
| Total | 48 | **0.670469** |

Reported totals are 139,452 input, 5,732 cache-write input, 131,836 cache-read input and 58,865 output tokens. Every charge reconciles to its own reservation and shared-ledger usage; none is unsettled. Costs use reviewed local rates and the batch discount, not provider invoices. No extra trials or budget increase were needed. The original USD 13.481825 conservative allowances were reconciled normally.

The production cache digest before and after is `584490b7a696d40cea1a39c42fe77b3d311c960b02c825e3d1d9726b8130adb6`. All results stayed isolated. This live campaign had no API or extraction-shape failures; receipt membership checks passed. Offline regressions cover failed, malformed and pending preservation behavior.

Final validation: **1,872 passed, one skipped**, including 40 qualification/trial tests. Historical, frozen-label and source-adjudicated reports reproduce exactly. The real count-only source subsets are exercised read-only in fixtures, and `git diff --check` passes.

## Consequence for #302

The selected configuration has bounded successes and reproducible full-relationship failures. Do not assign archive-wide parity or reject every count shortfall automatically. Further work should address generic unpriced participation, stage/category representation and price-versus-expense/tax-status conservation, then requalify affected behavior. Whole-meeting-package recall and existing recipient/decision proof cases remain explicit limits. This campaign changes neither the shared prompt nor the default model and triggers no blanket paid re-extraction.
