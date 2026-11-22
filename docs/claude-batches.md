# Asynchronous Claude batch extraction

By default, `tb nightly` submits uncached documents to Anthropic's Message Batches API (Sonnet, `high` effort) so capture finishes without waiting on LLM responses. Queued work appears as pending in `sync_run` and Slack notifications, and an hourly systemd collector imports completed batches, derives rows, exports, and publishes without re-scraping sources.

Standalone `tb extract` commands remain synchronous unless `--batch` is passed. Set `TB_CLAUDE_MODE=sync` to run nightly extraction synchronously. Both paths share the same prompt, schema, output limits, and effort settings. Request serialization preserves JSON schema field order, because alphabetical reordering affected constrained generation in live testing.

## Lifecycle

1. **Enqueue**: Capture archives source files as usual and queues chunk manifests for uncached eligible PDFs (Waterfront pins its latest parsed HTML snapshot for offline replay).
2. **Submit**: At the end of capture, nightly submits up to 100 queued requests (capped at 64 MiB per batch), grouping requests with identical model/version/schema profiles. Budget limits can stop submission early. Every budget reservation and submission manifest commits atomically to `llm-usage.sqlite` before the `POST`; ambiguous responses are never retried automatically.
3. **Collect**: The hourly collector polls completed batches and drains the next queued group as budget frees up. Receipts are matched by per-attempt custom IDs (regardless of order), saved raw before validation, and rejected if IDs are missing, duplicated, or foreign. Errored, cancelled, and expired items release their budget reservations; completed outputs settle at the batch rate (including malformed or truncated outputs); uncertain outcomes keep their reservations.
4. **Cache**: Only complete, structurally valid documents enter `extraction_cache`. A single failed chunk keeps the whole document out, and results from older prompt versions are billed and kept in operational logs without entering the current cache. Interrupted cache commits can be re-run safely.
5. **Refresh and publish**: Applies normal corpus backfill and Waterfront checks to held data, rebuilds suppliers, exports, and writes a publication handoff manifest listing the exact processed documents. Publishing runs before those IDs are acknowledged; a failed export or publish leaves them queued for the next attempt.

All batch state, receipts, and spend tracking live in `<TB_DATA_DIR>/llm-usage.sqlite` (contains no API keys and is excluded from public exports). Keep this file across deployments; never copy or delete it to retry jobs.

## Commands

```bash
# Inspect local queue and spend status offline
uv run tb extraction-batches status

# Queue a corpus batch (exit code 2 means pending work was queued)
uv run tb extract --batch --corpus award_summary --limit 50

# Submit another group of queued requests
uv run tb extraction-batches submit --limit 100

# Poll once and import completed documents into extraction_cache
uv run tb extraction-batches collect

# Derive rows, export, and write a handoff manifest (skips export if nothing changed)
uv run tb extraction-batches refresh --manifest /tmp/batch-publish.json
# Acknowledge processed documents after publish-data.sh succeeds
uv run tb extraction-batches acknowledge --manifest /tmp/batch-publish.json
```

`collect` and `refresh` exit non-zero on actual errors, not while batches are still running on Anthropic's side. Already-queued requests keep the model, effort, and prompt version they were created with.

To retry a failed job after inspecting its error:

```bash
uv run tb extraction-batches retry JOB_ID
uv run tb extraction-batches submit
```

Completed chunks are reused. Switching to synchronous mode does not cancel in-flight batches.

## Recovering lost receipts

If a batch `POST` was interrupted after Anthropic accepted it, the local job stays reserved and is never resubmitted automatically. Use `status` to get its local submission ID, locate the batch on Anthropic, and attach it:

```bash
uv run tb extraction-batches list-remote
uv run tb extraction-batches attach LOCAL_SUBMISSION_ID msgbatch_REMOTE_ID
uv run tb extraction-batches collect
```

`attach` waits until the remote batch finishes and verifies the full set of custom IDs (not just the count) before linking it. If no remote batch exists, check Anthropic Console billing before altering local reservations.

## Budget and scheduling

- Batches receive Anthropic's [50% batch discount](https://platform.claude.com/docs/en/build-with-claude/batch-processing) and share the same $20 local monthly guard (`TB_LLM_MONTHLY_USD`) with synchronous runs. Pending reservations carry across UTC month boundaries, and settlements record in the month received. Keep the Anthropic Console workspace limit as the real spending cap.
- Batches can take up to 24 hours to finish and must be collected before provider retention expires.
- The hourly collector (`tb-batch-collect.timer`) runs daily including weekends and coordinates on `.pipeline.lock` (skipping if nightly is running; nightly waits briefly for a short collector). See [`deploy/README.md`](../deploy/README.md) for systemd installation. If the collector is not installed, the next nightly polls and applies completed batches instead.
