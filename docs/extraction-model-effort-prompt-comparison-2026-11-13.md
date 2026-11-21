# Model, effort and prompt comparison — 2026-11-13

**The clearer prompt produced the strongest improvement in this test.** Higher effort and Opus recovered omitted bidder names, but assigned unsupported prices. The revised Sonnet/high prompt recovered those participations while leaving unpublished bid prices null. Production remains unchanged.

This follows the [paired qualification campaign](extraction-qualification-live-2026-11-13.md) for [#302](https://github.com/CivicTechTO/toronto-bids/issues/302). All **80 authorized new requests** completed with valid structured outputs and reconciled provenance, for **USD 2.058287** in recorded usage.

## Controlled design

Each configuration ran the same ten documents twice: eight known failure cases and two regression controls. Inputs were the Award Summary Form, composite, committee, Zoo, EP bus duct, TRCA D01/D02/D04, positive BD control and negative EP WSIB control. Each configuration has 20 responses and 166 expected observations from 83 unique visible source relationships.

| Configuration | Model | Effort | Prompt |
| --- | --- | --- | --- |
| Fresh control | Sonnet 5.5 | high | Original |
| More effort | Sonnet 5.5 | xhigh | Original |
| Model upgrade | Opus 5.5 | high | Original |
| Clearer instructions | Sonnet 5.5 | high | Original plus frozen clarifications |

Each arm changes one factor from the fresh control. Source text, schema property order, output limit and execution mode were held constant. Calls were synchronous, so this does not establish batch parity. The existing shared monthly guard applied; no production setting or budget limit was raised.

Opus pricing was checked before paid execution: USD 4/MTok input, 20 output, 5 for five-minute cache writes and 0.20 for cache reads. Existing Sonnet rates match the current table. [Official pricing](https://platform.claude.com/docs/en/about-claude/pricing). The selected models support these explicit effort settings. [Official effort guidance](https://platform.claude.com/docs/en/build-with-claude/effort). The Models API confirmed access to the current Opus 5.5 ID; no legacy model or provider fallback was used.

## Results

| Configuration | Exact relationships / 166 expected | Exact document observations / 20 | Correct unpriced participations / 10 | Recorded USD |
| --- | ---: | ---: | ---: | ---: |
| Sonnet 5.5/high, original | 121 | 7 | 0 | 0.349318 |
| Sonnet 5.5/xhigh, original | 139 | 11 | 0 | 0.533760 |
| Opus 5.5/high, original | 138 | 11 | 0 | 0.793876 |
| Sonnet 5.5/high, clarified | **164** | **18** | **10** | **0.381333** |

The revised prompt's strict relation recall is 164/166 (98.8%), versus 121/166 (72.9%) for the fresh control. These are measurements on deliberately selected known failure cases, not archive-wide accuracy estimates. The two remaining strict mismatches are the same Zoo punctuation variant in the repeats: the source prints both `Imperial Fence Inc.` and `Imperial Fence, Inc.`. Frozen strict labels and scores were not changed. A separate source-bound component adjudication accepts those printed alternatives only for the protected unpriced-participation check.

That component is critical. There are five independently reviewed unpriced participation facts per pass: four composite bidders and the named Zoo tenderer. Across two repeats:

- Original Sonnet/high omits all ten participations.
- Sonnet/xhigh and Opus/high return all ten names but attach prices without submitted-price evidence. Opus copies individual award values/caps. One xhigh response uses option-inclusive totals as both bid and award values, contrary to the archive's defined initial-term award projection.
- The clarified prompt returns all ten participations with null submitted prices, without padding the unpublished losing-bidder lists.

Name-only scoring would have mistaken the stronger-model/higher-effort variants for complete repairs. The summary distinguishes recovered names from unsupported price attribution.

Both positive BD and negative WSIB controls pass in both repeats of every configuration. No contracts appear in negative controls, and the revised prompt preserves the positive bid/award distinction without duplicating an already enumerated bidder.

## Prompt changes

The [frozen clarifications](ground-truth/extraction-qualification/comparisons/2026-11-13/prompt-clarifications.txt) add general rules without supplier names, call numbers, target prices or per-document instructions:

- Retain explicit competitive participation without a price table, using null for an unpublished submitted price.
- Separate submitted prices, initial-term awards, allocations, shared limits, forecasts, contingencies and renewals.
- Define tax-basis vocabulary and apply table/narrative qualifiers to the relevant values.
- Read explicit acceptance findings without assuming every listed bidder is compliant.
- Clarify prequalification-only versus invited/actual tender participation.
- Preserve distinct category participation as repeated entries instead of collapsing suppliers.
- Separate explicit footnote markers from names/prices while retaining source spelling and money formatting.

Both revised-prompt repeats match all 34 visible D04 relationships, withhold Bell's unallocated forecast value, retain repeated participation/statuses, and preserve committee/EP tax and price distinctions. D04 remains an excerpt, and the schema still lacks a category field: matching these representable observations does not establish whole-package recall or complete category semantics in storage.

## Evidence and reproduction

The [summary](ground-truth/extraction-qualification/comparisons/2026-11-13/summary.json) includes per-input results, variation, controls and protected participation observations. [Conservation](ground-truth/extraction-qualification/comparisons/2026-11-13/conservation.json) records 80 distinct response IDs, zero unsettled reservations and an unchanged production-cache digest. Arm directories retain exact requests, raw responses, parsed results and charges; no API key or request headers are included.

The [comparison cohort](ground-truth/extraction-qualification/cohort-comparison-2026-11-13.json), clarification text, inputs and factor settings were frozen before the calls. No labels changed in response to the study. The [component review](ground-truth/extraction-qualification/comparisons/2026-11-13/component-reviews.json) is a disclosed retrospective spelling adjudication; it does not affect strict scores or production identity.

Reproduce the summary from the repository root without credentials or network:

```sh
uv run --project scrapers python - <<'PY'
import json
from toronto_bids.qualification_comparison import comparison_summary
print(json.dumps(comparison_summary(
    'docs/ground-truth/extraction-qualification/cohort-comparison-2026-11-13.json',
    'docs/ground-truth/extraction-qualification/comparisons/2026-11-13',
), indent=2))
PY
```

Experimental prefixes have separate hash-bound evaluation versions and cannot receive production qualification or enter the production cache. The shared prompt and `v1-b63aa8c835ae` version are unchanged. Opus pricing is registered for explicit opt-in use; the default remains Sonnet 5.5. Every charge reconciles to its own raw usage and shared-ledger reservation. Costs are local usage estimates, not provider invoices.

## Decision and limits

Validation: `uv run --project scrapers pytest -q` passed with **1,879 passed, 1 skipped**. `git diff --check` passed. The seven comparison tests cover experimental-prompt isolation, factor control, pricing, reproducible scores, and unsupported-price and alias rejection.

For these observed failures, clearer instructions are the better first intervention: they outperform both more effort and Opus under the original prompt, at roughly the control's cost. This supports a candidate for further qualification, not immediate production promotion.

The error cases informed prompt design. Two repeats on ten familiar documents are not held-out accuracy or a statistical model ranking. Fable 5.2, max effort and Opus with the revised prompt were not tested. Independent held-out cases and batch replay are needed before promoting the prompt. Category representation, historical decision status and source/identity proofs still need schema/evidence work. No production prompt, cache, registry or archived fact was changed.
