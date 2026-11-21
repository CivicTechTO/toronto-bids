# Frozen revised-prompt follow-up

See the [full follow-up report](../../../../extraction-sonnet-revised-followup-2026-11-15.md). All 68 Sonnet 5.5/high requests completed: seven fresh sources and ten regression cases, twice in each execution mode. The candidate still fails exact repeatable qualification. Production is unchanged.

- `campaign.manifest` and `cohort.json`: pre-call frozen plan and source-reviewed labels.
- `sources/` and `source-identities.json`: exact inputs and verification against original held PDFs and fresh text renders.
- `runs/`: exact requests, raw responses, usage, charges and complete batch results.
- `source-reviews.json`: disclosed retrospective printed-form equivalences and unresolved tax/scope disagreements; primary labels stay unchanged.
- `report.json`: complete primary qualification report.
- `summary.json`: separate held-out/regression and sync/batch measurements, with primary and supplementary scores.
- `conservation.json`, `cache-before.json`, `cache-after.json`: settled charges and unchanged production cache.
- `*.status.json`: provider batch completion receipts.

From the repository root, run `uv run --project scrapers python docs/ground-truth/extraction-qualification/followups/2026-11-15/summarize.py` to reproduce the reports offline. No credential or production database is needed.
