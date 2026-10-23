# Claude extraction: cost and quality options, 23 October 2026

The amount-format discrepancy was a verifier inconsistency, now fixed. Source evidence and claim identity are invariant to whether the response copies the source's display parentheses. Seven saved Haiku outputs then reproduce all 44 held supported Waterfront relationships. Fresh requests show why replay alone is insufficient to qualify a model: recipient omissions and verbatim-name failures still occur.

Production remains on merged `main`. These experiments use an isolated PR worktree, the unchanged shared prompt and the operational budget ledger. No output was imported into the production extraction cache. The default in PR #268 remains Sonnet/high. `TB_CLAUDE_EFFORT` now supports `low`, `medium`, `high`, `xhigh` and `max`; unset retains Sonnet/high and Haiku/medium. [Anthropic effort guidance](https://platform.claude.com/docs/en/build-with-claude/effort) confirms those five settings and recommends measuring quality as well as latency and cost.

## Complete, comparable 12-document passes

Costs are estimates from each response's reported usage and the [reviewed price schedule](https://platform.claude.com/docs/en/about-claude/pricing). Times are measured API response times, excluding queue delay. Inputs are seven Waterfront months, three human-labelled TRCA documents, one Exhibition Place procurement report and one non-procurement report.

| Configuration | Cost for 12 documents | Median seconds/request | Supported Waterfront relationships | Automatic document checks |
|---|---:|---:|---:|---:|
| Sonnet/high | $0.226494 | 8.42 | 44/44 | 11/12 |
| Fresh Haiku/medium, verifier fixed | $0.017168 | 8.43 | 42/44 | 9/12 |
| Haiku/xhigh, verifier fixed | $0.028893 | 14.53 | 43/44 | 10/12 |

The remaining Sonnet automatic mismatch is the already documented Bell Canada label: the source describes annual service spending rather than a submitted tender price. All models returned NULL for that bid price. The source still lacks the March 2023 Waterfront dewatering recipient; conservation of supported relationships does not count that missing fact as resolved.

Haiku/medium missed the City of Toronto recipient of $88,950 and copied an attribution's spelling `EllisDon Civil Ltd.` instead of the primary-recipient spelling without the period. Haiku/xhigh recovered the City recipient but the exact-name failure recurred in the complete pass. These are small samples, not population accuracy estimates. The verifier refuses unsupported literal claims and reports incomplete coverage.

## Effort sweep on the two difficult months

| Haiku setting | Cost for the same two months | Median seconds/request | Exact relationship checks |
|---|---:|---:|---:|
| high | $0.002784 | 9.98 | 1/2 |
| xhigh | $0.004383 | 16.24 | 2/2 |
| max | $0.018796 | 65.16 | 2/2 |

The focused xhigh pass succeeded, but its later full pass again failed the exact-name check. Max showed no extra quality gain on these two cases and was substantially slower and more expensive. It has not been validated on the complete set. Effort increases are useful experiments rather than proof that every failure is repaired.

## Multiple views

Three full Haiku/medium views would cost about **$0.0515** for this 12-document sample if token usage repeats, compared with Sonnet/high's measured $0.2265. That three-view figure is a projection, not a measured three-view batch. Serial views multiply latency; parallel views use more concurrent capacity.

Repeated views share errors. The initial medium run found the City of Toronto recipient, while fresh medium and high runs omitted it; majority voting across those samples would discard the correct claim. A union can preserve omissions recovered by another view, but literal co-occurrence is not sufficient to prove every supplier/amount association, especially in multi-supplier blocks. Blind voting or union is not a safe production policy.

A different, narrower view was also tested: the unchanged prompt received only the unresolved literal source block and its original publication header. Three such calls cost **$0.001184** in total. The two known failures remained unresolved. A missing-recipient control remained unsupported as required, although the model proposed the construction manager and the verifier rejected it. Context narrowing alone did not fix these cases.

The most useful next multi-model experiment is a gated sequence: Haiku first, a genuinely different review where verifiable coverage is incomplete, and Sonnet only for remaining ambiguity. Coverage and contradictions must drive escalation; model confidence alone is insufficient. This experiment has not been implemented in the production pipeline.

## Asynchronous batch processing

Anthropic's [Message Batches API](https://platform.claude.com/docs/en/build-with-claude/batch-processing) discounts input and output tokens by **50%**. Most batches finish within an hour, but requests may remain pending until the 24-hour expiration. Results are processed asynchronously and collected later. [Batch pricing](https://platform.claude.com/docs/en/about-claude/pricing#batch-processing) applies to both reviewed models.

Prompt caching can stack with the batch discount, but asynchronous cache hits are best-effort. At identical token usage and cache behavior, the 12-document Sonnet sample projects to **$0.113247** in batch and Haiku/xhigh to **$0.014447**. These are price projections; no batch was submitted, and actual cache behavior may differ.

Routine archive extraction and historical backfills can tolerate this delay. A production integration needs durable submission IDs, per-document/version identifiers, budget reservations held until reconciliation, and collection of successful, rejected, expired or canceled items. Submission is pending work, not successful extraction. Fresh source capture can continue while results wait; imports should use existing validation and conservation rules. This requires a submit/collect queue rather than changing a flag on the synchronous nightly client.

## Recommendation

For the next cost reduction, **batch the already qualified Sonnet configuration for work that can wait**. This provides a documented discount without choosing a weaker extraction result or adding unvalidated voting logic. Keep synchronous processing available for time-sensitive work.

Haiku/xhigh plus selective Sonnet escalation is a promising subsequent experiment: this pilot costs roughly one eighth as much as synchronous Sonnet but is slower and still has one source-verification failure. Validate escalation and contradiction handling on more held-out source cases before making it the default. Max effort and blanket three-view voting do not currently justify their extra latency or complexity.

Full evidence is under `/home/alex/tb-data/operations/claude-validation-2026-10-23/haiku-optimization/`. All 79 live Messages attempts across initial and follow-up work reconcile to an estimated **$0.659832** with no unsettled reservations. The production extraction-cache digest is unchanged. Per-trial estimates initially affected by concurrent prototype runs were corrected by matching response usage to ledger entries; original interim figures are retained in the affected reports.
