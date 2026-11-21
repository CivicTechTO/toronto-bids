# Model, effort and prompt comparison

The completed comparison ran four configurations on the same ten inputs twice: **80 requests**, recorded cost **USD 2.058287**. Production remained unchanged.

See the [written results](../../../../extraction-model-effort-prompt-comparison-2026-11-13.md).

- `comparison.manifest` freezes selection, configurations, source-label hash and prompt clarification text.
- [summary.json](summary.json) contains arm results and protected unpriced-participation observations.
- [component-reviews.json](component-reviews.json) records the source-supported Zoo punctuation adjudication. Strict labels/scores are unchanged.
- [prompt-clarifications.txt](prompt-clarifications.txt) contains experiment-only instructions.
- Each arm directory has two synchronous trial files with requests, raw responses, results and usage. Unused offline batch manifests were never submitted.
- Each `*.report.json` is the complete source-reviewed report for an arm. These synchronous experiments are not a production qualification pass.
- [conservation.json](conservation.json) records the unchanged cache and 80 reconciled reservations.

The [frozen cohort](../../cohort-comparison-2026-11-13.json) reproduces the summary offline. These known failure cases and controls do not replace independent held-out and batch validation.
