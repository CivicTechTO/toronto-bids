# Asynchronous Claude extraction

The nightly uses batched Sonnet/high for uncached documents. Capture continues immediately; queued extraction is shown as pending in step history and the notification. Existing facts remain available. The hourly collector finishes work on weekends too, using held data rather than scraping sources again.

Standalone extraction stays synchronous. Set `TB_CLAUDE_MODE=sync` to run the nightly synchronously. Existing document caches and the shared prompt version are unchanged. The batch and synchronous paths share the same request builder, schema, output limit and effort settings. Request persistence preserves schema field order: alphabetic reordering changed constrained generation in live validation.

## Lifecycle

1. Capture archives source bytes as before. Eligible uncached PDF documents enqueue complete chunk manifests. Waterfront records the exact latest successfully parsed snapshot for later offline replay.
2. At the end of capture, the nightly submits up to 100 queued requests, with a 64 MiB payload bound. Identical request/version profiles share a job. Schema order is part of the profile. The budget can stop submission earlier.
3. Every request allowance and the complete submission manifest commit atomically to `llm-usage.sqlite` before one creation POST. No creation POST is automatically retried after an ambiguous response.
4. Collection matches the complete receipt by per-attempt custom IDs, independent of result order. Duplicate, missing or foreign IDs refuse the receipt. Raw receipts are retained before validation. Known unbilled errored/canceled/expired outcomes release their allowances. Successful outputs count their reported tokens, including rejected or truncated extractions, at the batch rate. Unknown usage retains its reservation.
5. Only complete, structurally valid document results enter the existing cache. One failed chunk keeps the whole document out. Current cache entries are preserved. Stale prompt versions are retained operationally and billed, but never imported into the current cache. A cache commit interrupted before its queue acknowledgement can be safely repeated.
6. Refresh applies the normal corpus derivation and Waterfront evidence checks to held data, rebuilds suppliers and exports. Supported partial results can publish while other failures remain visible. Source ambiguities and absent recipients do not become invented facts.
7. A publication handoff names exactly the processed documents. The runner publishes before acknowledging those IDs. A failed export or publish leaves work ready for the next attempt. Results arriving concurrently remain unacknowledged until their own refresh. Results no longer matching held source data are superseded operationally; held facts are preserved.

The private operational database holds requests, receipts, manifests and billing. It contains no API key and is excluded from procurement exports. Retain it across deployments. Do not copy or delete it as a way to retry jobs: it is the submission and spending authority for this installation.

## Commands

```bash
# Inspect local state without a network call.
uv run tb extraction-batches status

# Historical corpus work: normal classification/cache gate; exit 2 means pending.
uv run tb extract --batch --corpus award_summary --limit 50

# Submit another bounded group of already queued requests.
uv run tb extraction-batches submit --limit 100

# Poll once and cache completed documents. No waiting and no publication.
uv run tb extraction-batches collect

# Offline application/export and a durable handoff for a publisher.
uv run tb extraction-batches refresh --manifest /tmp/batch-publish.json
# After the normal publish command succeeds:
uv run tb extraction-batches acknowledge --manifest /tmp/batch-publish.json
```

`collect` and `refresh` return nonzero for failures, not for ordinary server processing. `status` lists job IDs and submission receipts. Refresh without new applicable results does not export or trigger a frontend build. Already queued bodies retain the model/effort/version with which they were created; a configuration change does not rewrite an in-flight request.

For failed requests, inspect the retained error first, then explicitly requeue a job:

```bash
uv run tb extraction-batches retry JOB_ID
uv run tb extraction-batches submit
```

Ready chunks are reused and prior attempts remain in the submission history. No automatic paid model fallback is added. A synchronous command can request immediate extraction even while an older batch is in progress; switching modes does not cancel remote work.

## Lost receipts

An interrupted POST can be accepted even when its response is lost. Such jobs remain reserved and are never automatically sent again. `status` exposes their local submission IDs. Find the server batch through the Console or the recent-batch listing, then attach it:

```bash
uv run tb extraction-batches list-remote
uv run tb extraction-batches attach LOCAL_SUBMISSION_ID msgbatch_REMOTE_ID
uv run tb extraction-batches collect
```

Attachment waits until the server batch has ended and proves the entire per-attempt custom-ID set before associating it with the local submission. A matching count alone is insufficient. If no remote receipt can be established, billing must be investigated before changing local reservation state.

## Budget and scheduling

Batched input/output receives Anthropic's [50% discount](https://platform.claude.com/docs/en/build-with-claude/batch-processing). The same USD 20 local guard covers synchronous and batched usage. Pending allowances carry across month boundaries; settlement is booked conservatively in the month it is received. The provider workspace limit remains the actual billing control.

Batch processing may take up to 24 hours; results must be collected before the provider's retention period ends. The collector polls hourly, including weekends, without waiting inside a process. It skips while the nightly owns the shared archive lock; the nightly waits for a short collector if necessary. Existing source rate limits and off-peak access conditions remain with the capture steps.

See `deploy/README.md` for installing the new collector service/timer. Merely merging the Python changes does not install a systemd unit. Until the collector is installed, the next nightly also polls completed batches and applies their cache entries through the normal pipeline; this can cause a longer weekend delay.
