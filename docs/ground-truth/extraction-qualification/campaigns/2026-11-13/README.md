# Completed paired campaign

All 48 authorized Sonnet/high requests completed: 15 inputs in each mode plus nine difficult-case repeats per mode. Reconciled usage cost is USD 0.670469, with no outstanding reservations. [Conservation](conservation.json) records the unchanged production cache.

See the [live report](../../../../extraction-qualification-live-2026-11-13.md) for findings and limits.

- [Reviewed measurements](report.json) use [the current cohort](../../cohort.json).
- [Original measurements](report-pre-review.json) use [the frozen pre-trial cohort](../../cohort-2026-11-13-pretrial.json).
- [Source adjudications](source-adjudications.json) and [count explanations](count-adjudications.json) preserve material discrepancies.
- [Usage](usage.json) reconciles every request to its own operational reservation.
- `runs/` retains exact requests, raw responses, parsed extractions, provenance and batch receipts. No API key or request headers are included.

Reproduction instructions are in the [cohort guide](../../README.md). Report exit 1 is expected: the full cohort fails its predeclared thresholds. All executions are complete; four relationship evaluations require the existing Waterfront source-proof review.
