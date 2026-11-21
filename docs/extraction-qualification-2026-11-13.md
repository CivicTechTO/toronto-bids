# Extraction qualification continuation — 2026-11-13

This page retains the offline preparation-stage findings for [#302](https://github.com/CivicTechTO/toronto-bids/issues/302). The user subsequently authorized the paid campaign, and all 48 requests completed for USD 0.670469. The [completed live report](extraction-qualification-live-2026-11-13.md) supersedes the pending-execution status below and retains frozen original labels alongside subsequent source adjudications.

At this offline stage, source review and the Waterfront evaluation path from the [November 12 baseline](extraction-qualification-2026-11-12.md) were complete. No new requests had been sent: automatic approval review had rejected external transfer and spending that were not yet explicitly authorized. Offline work continued independently while approval was pending.

## Completed evidence work

All **15 selected inputs** have independent source-reviewed labels: **114 relations** across nine active PDF corpora and Waterfront. Every relation has literal source evidence. The complete composite report, all eight formerly pending source reviews and existing human-label disagreements remain inspectable in the [cohort](ground-truth/extraction-qualification/cohort.json). None of the human labels or production identity registries changed.

Source review confirms several limits that higher name recall would not solve:

- The BA control rescinds a duplicated decision while retaining a valid earlier award. A raw `awards` array cannot express that decision distinction; admission belongs to [#282](https://github.com/CivicTechTO/toronto-bids/issues/282).
- TTC's six submitted tender totals differ from its two initial award allocations. The $261,000 combined ceiling is not an individual award value. The 2000 source's general tax statement does not establish an HST basis.
- The four composite appendices name recommended bidders and aggregate bid counts, without publishing the other bidder names. Expected initial-term net-of-taxes awards exclude option-year totals and HST-recovery amounts.
- TRCA D04 is explicitly an excerpt, omitting 59,023 leading and 267,742 trailing package characters. It cannot qualify whole-document recall. Its electrical/mechanical VOR categories also expose a schema limit: repeated firms can have different category statuses, but extraction has no category field. Bell Canada's $235,000 forecast service cost remains a source/old-label disagreement, not a submitted tender price.
- The March 2023 primary Waterfront publication omits a recipient, but the same publisher's already-reviewed dashboard page 27 names **WJ Groundwater** for TPBR182 / CM#2023-03. The held PDF/text hashes bind that supplementary evidence. The primary $313,750 amount remains distinct from dashboard estimates and contingency values.

Benchmark reference aliases enumerate source-reviewed type/No. typography only. Two supplier aliases accept D04's resolution/rationale spellings of Ainsworth and Black Creek, which the same source explicitly corroborates. Applied normalizations are reported and never link records across documents or change production identity. Exact raw responses remain unchanged.

## Historical replay

The [reproducible report](ground-truth/extraction-qualification/report.json) preserves all six historical trial sets and all **29 selected raw Claude responses**. Original responses reconcile to parsed extractions, reported token usage and reviewed charges. Initial batch request/usage details were recovered from held records; synchronous request/schema identities that were never retained remain unknown. Per-profile reporting separates model/effort changes from repeated observations and compares sync/batch only when their exact request identities match.

The production Waterfront verifier now has an optional observation trace. Its ordinary outputs are unchanged; qualification also sees every duplicate and rejected raw award, so source verification cannot erase false relationships from the benchmark denominator. Unexpected non-award roles remain scored. The initial batch preserves **0/17** supported relationships; the corrected batch preserves **17/17** with complete retained request provenance. These historical runs establish the path on two inputs, not a full paired campaign.

Three March 2023 historical responses encounter the production registry's changed extraction fingerprint and are `source_review_required`, not malformed JSON. The existing gate binds the reviewed recipient to one exact response. Evaluation does not transfer that review or weaken admission to make the old trials pass. Registry identity is included in the report.

Other exact relationship diagnostics aggregate across three configurations, not independent archive samples:

| Corpus | Expected / observed relations | Exact matches | Remaining diagnostics |
| --- | ---: | ---: | --- |
| EP | 18 / 18 | 10 | Eight tax/status attribute disagreements; negative WSIB checks agree |
| TRCA | 162 / 157 | 135 | 27 omissions, three role/stage false joins and 19 attribute errors |
| Waterfront path | 93 / 69 | 69 | 24 omissions across measured attempts; three additional responses require source-context review |

Wrong observations and absent correct relations are both reported, so these error categories are not an additive document-error total. Exact spelling, amount-format, tax/status and category discrepancies require source adjudication before being described as factual extraction errors. Review limited-scope and category cases before interpreting the aggregate TRCA scores as accuracy. No corpus is assigned a qualification pass.

Independent source counts now detect shortfalls even when an extracted declaration is absent. D01's high-effort response declares six prequalification submissions beside three actual tenders. The evidence-bound review explains this as a stage-count error; it does not approve it as a legitimate missing-bidder subset. Prior supported facts remain untouched.

Known historical selected-attempt cost is **USD 0.350571**, with no unknown charges among the 29 retained attempts. This is entirely historical spending. Sixty-one missing coverage slots across the six runs are reported separately rather than priced as zero or mistaken for unknown billing.

## Prepared campaign and execution

The new isolated runner preserves exact production requests, cohort snapshots, model/effort/schema identities, original responses, usage and shared-ledger reservations. It offers offline preparation, explicit synchronous execution, batch submission and single-poll collection. Durable state and file locks prevent duplicate dispatch; uncertain outcomes remain pending without automatic resends. Receipt membership is verified before settlement. Failed/truncated outputs retain their raw evidence and known billing. No procurement archive or production extraction cache is opened by the trial runner.

The prepared campaign contains 15 inputs per first mode pass and nine difficult inputs per repeat: **48 requests**. Conservative allowances total **USD 13.481825**; this is a maximum reservation calculation, not a forecast of actual token cost. At preparation, shared current-month charges plus outstanding reservations were USD 2.500473 under the existing USD 20 monthly guard. The requested authorization is capped at USD 14; no budget was raised or reset. These are dated planning measurements, not live budget guarantees.

Automatic approval review rejected the attempted submission before process execution. No document payloads were sent and no new reservations or charges were made. User approval of the Anthropic destination and spending is required before this campaign can execute. Offline preparation, source review, tests and report reproduction continued independently.

Validation: **1,868 passed, one skipped** in the complete offline suite, including 36 qualification/trial tests. The machine-readable historical report reproduces exactly. Request-builder parity, rejected and duplicate Waterfront observations, source-review versus malformed states, independent counts, aliases, raw-response/billing reconciliation, matched-request comparisons, immutable attempts, uncertain outcomes and receipt membership are covered. `git diff --check` passes.
