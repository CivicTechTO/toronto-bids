# Batched Sonnet validation — 2026-10-23

The corrected live two-document batch recovered all 17 expected Waterfront award claims: eight from September 2017 and nine from October 2021. Claim identities and numeric values matched the held source evidence. These are bounded sample results, not a guarantee of completeness across every document or future model response.

## Live requests and cost

| Run | Requests | Outcome | Estimated USD |
| --- | ---: | --- | ---: |
| Initial batch | 2 | Ended; empty award output failed source checks | 0.009763 |
| Corrected batch | 2 | Ended; 8/8 and 9/9 expected awards, no source-check errors | 0.021774 |
| Two synchronous diagnostics | 2 | Schema-order controls; incomplete outputs retained | 0.020662 |
| Total | 6 | All usage reconciled; no unsettled test reservations | 0.052199 |

Batch charges were reconciled against reported token usage at the discounted rate. The successful batch cost about 2.2 cents. Costs above are local estimates, not an invoice.

The first run exposed a request-persistence bug: sorting nested JSON properties alphabetized the structured-output schema. Persisted batch requests therefore differed from the qualified synchronous request. Persistence now preserves the exact request and schema property order; job identity includes that ordered representation. A regression compares serialized requests across restart and submission, since dictionary equality alone would miss reordering.

The synchronous sorted-schema diagnostic returned zero claims; the original-order diagnostic returned only one of eight expected claims. These incomplete controls remain recorded. The corrected batch then returned all 17 expected claims. Field order matters to request parity, but it does not remove model variability. Neither the shared prompt nor its cache version changed.

## Apply and export round trip

The corrected live results were replayed in an isolated copy of the archive and operational store, using held HTML and the existing Waterfront evidence checks. HTTP was disabled for this replay.

- Two documents applied; 17 source-verified award facts derived.
- Supplier rebuild and normal export completed, producing 29 files.
- Publication handoff and acknowledgement succeeded in the isolated copy.
- A repeated refresh performed no duplicate export.
- No source network calls, real publication, or production extraction-data writes occurred.

Offline regressions cover durable submission and restart, concurrent submitters, uncertain POST responses, exact receipt membership, chunk admission, stale versions, invalid/billed outputs, cross-month reservations, source routing and conservation, publication failures, lock coordination, and draining the queued outbox without blocking completed work when its budget is exhausted. The full suite passes 1,249 tests. Bash syntax and systemd unit validation also pass.

## Evidence and deployment state

Private local evidence is retained under `/home/alex/tb-data/operations/claude-batch-validation-2026-10-23/`: `validation-summary.json`, `receipts.json`, initial and corrected collection reports, both diagnostic outputs, the offline-export report, and validation scripts. The operational ledger retains the test manifests and billing receipts; no cleanup mutation was performed.

The collector service and hourly timer are provided for deployment but were not installed or enabled during validation. The production checkout remains on merged `main`. PR #269 is stacked on #268; deploying requires both changes and installation of the collector units as described in `deploy/README.md`.
