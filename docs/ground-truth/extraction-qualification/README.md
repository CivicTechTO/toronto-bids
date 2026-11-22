# Extraction qualification cohort (#302)

The [2026-11-22 evidence-backed candidate campaign](followups/2026-11-22/) is prepared offline (72 requests, 0 sent) with a separately versioned prompt (`eval-3e995e90436c`) and schema adding `bids[].amount_evidence`, applying the settled #302 policy rules (strictly local award tax basis and empty contract arrays for ongoing-procurement status updates) and expanding the untouched holdout across all seven previously absent corpora (`award_summary`, `ba_report`, `bd_report`, `committee`, `composite`, `ttc`, `waterfront`). Reproduce the offline pre-flight summary without credentials or network access:

```sh
uv run --project scrapers python docs/ground-truth/extraction-qualification/followups/2026-11-22/summarize.py
```

The [68-request revised-prompt follow-up](../../extraction-sonnet-revised-followup-2026-11-15.md) tests seven fresh sources and ten regression cases twice in both modes. New source relationships match after disclosed printed-reference review, but an unsupported bid price and stage/compliance repeat failures remain. Production is unchanged; the candidate is not qualified.

The subsequent [80-request model/effort/prompt comparison](../../extraction-model-effort-prompt-comparison-2026-11-13.md) improved the known-case result from 121/166 to 164/166 exact relations with general clarifications. Higher effort and Opus alone recovered names but assigned unsupported bid prices. Experimental prompts have separate hash-bound versions and never production cache identities; they do not confer production qualification.

**The completed paired campaign does not establish full-cohort qualification or archive-wide Claude accuracy.** All 15 selected inputs have independent, agent-reviewed labels spanning every entry in `extraction.CORPORA` and Waterfront: 114 relationships. All 48 paid executions completed for USD 0.670469. Existing human labels are unchanged. See the [completed live report](../../extraction-qualification-live-2026-11-13.md), which preserves frozen pre-trial and source-adjudicated measurements.

From the repository root, reproduce the committed report without credentials, network access, a production database or paid extraction:

```sh
uv run --project scrapers tb qualify-extraction \
  --cohort docs/ground-truth/extraction-qualification/cohort.json \
  --runs-dir docs/ground-truth/extraction-qualification/runs \
  --out docs/ground-truth/extraction-qualification/report.json
```

Exit 1 means the cohort is not qualified, including incomplete evidence. Exit 0 requires every supported corpus to qualify. Malformed manifests produce an explanatory error instead of a successful report. Source text hashes and literal evidence quotations bind the reviewed labels to the held text; changed source bytes require another review.

## Labels and scope

`cohort.json` records source URLs, original document SHA-256, exact input-text SHA-256, independent relation labels, literal source passages, review notes and unresolved adjudications. Source snapshots make evaluation portable. They came from existing repository fixtures, TRCA labelling contexts and the read-only held archive; no new source acquisition was performed.

Labels were written against source tables and narrative, not against stored derived bids or awards. Review notes document tax, contingency, spelling and stage decisions. Labels are provisional evidence that can themselves be disputed; source-review disagreements must remain visible.

The continued reviews cover the BA rescission control, TTC brake drums, the complete four-contract composite report, TRCA D02/D04 and three Waterfront months. The composite's old test fixture stopped after appendix 2; the selected snapshot instead preserves the complete archived report. D04 explicitly omits leading and trailing meeting-package text: its visible input is reviewed, but `scope_complete=false` prevents whole-document qualification. The Waterfront document hash identifies held publication HTML, while its text hash identifies the selected month input.

Waterfront is scored through the actual production source verifier, using an optional observation trace that leaves its existing return values unchanged. Every rejected or duplicate raw award remains an observation in the benchmark, so verifier refusal cannot hide a fabrication or duplicate. Other raw roles are also retained. The initial and corrected batch outputs are both measured. The current production recipient-proof gate refuses March 2023 results with changed fingerprints; these are `source_review_required`, not malformed model JSON. The existing publisher dashboard independently establishes WJ Groundwater as recipient; its held PDF excerpt and text hashes bind that additional label evidence. No production identity registry or gate was changed. D04 retains the original Bell Canada price-label disagreement; do not change the human labels merely to make a score pass. Procurement identity/decision admission remains in [#282](https://github.com/CivicTechTO/toronto-bids/issues/282).

## What the scorer measures

Each relation contains supplier, printed contract reference, procurement stage, role (`bid`, `award`, `prequalified`, `invited_to_tender`), raw amount, tax basis and status. The multiset comparison preserves repeated claims. Supplier case, whitespace and apostrophe typography are ignored; other fields retain exact whitespace-normalized values. A document may enumerate source-reviewed `reference_aliases` for its own printed type/No. prefix and bare identifier, and `supplier_aliases` for explicitly corroborated spellings within that source. Applied aliases are reported explicitly; they never establish a cross-document or production identity join. There is no fuzzy supplier matching, identifier inference, amount correction or numerical-price tolerance. Bid and award prices are separate roles. Contingency and confidentiality are retained in trial artifacts but are not additional scored relation fields in this implementation.

Surplus observations are classified once, in this order:

1. Duplicate of an expected complete relation.
2. Attribute error on an otherwise matching supplier/contract/stage/role.
3. False join: a known supplier attached to a different contract, stage or role.
4. Fabrication: an unsupported supplier relationship.

Every missing complete relation is also an omission. Attribute errors therefore produce both a wrong observation and an absent correct relation. Contract-array membership is scored separately, detecting entirely omitted contracts and invented empty contracts, including on negative documents. In the Waterfront path, contracts are source publication-block identities rather than generated model reference strings; tax/status fields discarded by that production path are not invented for scoring. Unreviewed reference variations and tax/status differences require source review before interpreting exact-match diagnostics as factual errors. Independent `source_counts` check declarations and detect shortfalls even when the model omits its declaration. A source-reviewed stage-count error can explain a warning while remaining unresolved.

Per-corpus thresholds were fixed before replay: precision 1, recall 1, both synchronous and batch coverage using identical request/configuration, and two distinct response IDs per mode for difficult inputs. Every retained failed, pending, skipped, missing or unreviewed input prevents a blanket pass. Historical failures remain in the report; qualification does not select only a later successful attempt. A future qualification campaign should retain its own clearly identified run set and link earlier campaigns rather than deleting them. Unresolved source adjudications and count discrepancies also prevent a pass. These are deliberately strict bounded-cohort thresholds, not statistical estimates of archive-wide accuracy.

## Trial artifact contract

Each `runs/*.json` has a unique `id` and an `attempts` mapping keyed by selected document ID. Any required document absent from a trial is explicitly reported `missing`; never omit it from coverage. New campaigns can predeclare `selected_documents` so difficult-case-only repeats report other inputs as `not_selected`; overall mode/repeat coverage still requires every cohort input. An attempt records `state`, explanatory `reason`, `extraction`, `raw_response`, reported `usage`, measured `charged_microdollars`, and `provenance`. Repeats are separate runs, and reusing a response ID cannot satisfy repeat coverage. Per-profile comparisons separate configuration changes from repeat variability and report mode agreement only for identical known requests.

Provenance contains document/text hashes, prompt version, model, effort, execution mode, ordered request/schema identities, the exact request, response identity and response ID. JSON identities use UTF-8, `ensure_ascii=False` and compact separators without key sorting; schema property order is material. The exact current request prompt and text must agree with the held input. Unsupported historical prompt versions remain unverified. Do not reconstruct an unknown old request and present it as a retained request.

The October synchronous trials did not save exact requests/schema identities, so those fields remain null and cannot qualify. All 29 selected historical attempts now include original raw responses and reconciled usage/charges. Exact initial and corrected batch requests were recovered from held operational records, preserving schema order. The scorer checks raw response identity/model, parsed output, usage and billed charge against reviewed provider rates. Medium-effort Sonnet and Haiku are separate trials. Unknown usage is reported rather than priced as zero; missing coverage and offline prepared work are counted separately. Known costs cover only selected retained attempts, not every historical API call.

The completed campaign is separate from these historical trials. Reproduce it with the same command, changing `--runs-dir` to `docs/ground-truth/extraction-qualification/campaigns/2026-11-13/runs`. `cohort-2026-11-13-pretrial.json` preserves the labels used at preparation. Post-trial source adjudications add literal full call headings and corroborated supplier spellings, and correct eight agent-written VOR status labels; they do not alter original responses, human labels or thresholds. The reviewed report also includes supplemental numeric-value comparisons using the archive's existing strict parsers. Exact raw qualification remains unchanged.

Execution coverage is distinct from relationship evaluation: a provenance-complete `source_review_required` response satisfies executed-mode/repeat coverage while still preventing qualification. Profile summaries report raw extraction variants separately when the production source gate prevents relationship comparison.

## New isolated trials

Preparation uses the production request builder without credentials or network access. It preserves the cohort snapshot and four exclusive-create trial files; repeating preparation cannot overwrite paid evidence:

```sh
uv run --project scrapers python -m toronto_bids.qualification_trials prepare \
  --cohort docs/ground-truth/extraction-qualification/cohort.json \
  --out-dir /tmp/qualification-302 --campaign qualification-302
```

After authorization for the API payloads and spending, explicitly execute the desired action with the existing shared operational ledger and configured `ANTHROPIC_API_KEY`:

```sh
uv run --project scrapers python -m toronto_bids.qualification_trials sync \
  /tmp/qualification-302/qualification-302-sync-1.json --ledger /path/to/llm-usage.sqlite
uv run --project scrapers python -m toronto_bids.qualification_trials submit \
  /tmp/qualification-302/qualification-302-batch-1.json --ledger /path/to/llm-usage.sqlite
uv run --project scrapers python -m toronto_bids.qualification_trials collect \
  /tmp/qualification-302/qualification-302-batch-1.json --ledger /path/to/llm-usage.sqlite
```

`sync` sends only prepared attempts, without automatic retries. `submit` durably records reservations/custom IDs before one POST; `collect` polls once, preserves the raw receipt and verifies its complete membership before settling results. Ambiguous submissions remain pending and cannot be resent automatically. Definite rejections release allowances; truncated or invalid outputs are billed when usage is known. File locks prevent concurrent duplicate execution. These functions never open or backfill the procurement archive. The existing shared monthly guard remains authoritative for local spending; do not create a fresh ledger to bypass it. Exit 2 means pending, 1 means failed work, and 0 means completed trial execution, not qualification success.

## Persistent runtime count review

```sh
uv run --project scrapers tb extraction-counts --out /tmp/extraction-counts.json
uv run --project scrapers tb extraction-counts \
  --reviews /path/to/reviewed-counts.json --out /tmp/extraction-counts.json
```

This command opens the configured archive read-only, without migrations. It recomputes shortfalls from current cached results, so synchronous and batch results are covered without changing cache compatibility or backfill admission. Malformed cached entries remain explicit failures. It never removes existing facts or rejects an otherwise supported extraction solely because fewer bidders were published.

Reviews map each emitted `review_key` to an object with `disposition` (`published_subset` or `unpublished_bidders`), a concrete `reason`, and an `evidence` array of literal source quotations. Resolution requires nonempty evidence present in the source. The key binds document SHA-256, prompt version, entire cached response and exact count discrepancy; changed extraction cannot inherit an old disposition. Unused review keys remain visible. Human review must establish that the quoted text actually justifies the reason; substring matching is not an automated semantic adjudicator.

The [reviewed runtime examples](runtime-count-reviews.json) resolve four count-only source subsets and leave a stage-count error unresolved. Their [held evidence](runtime-count-review-evidence.json) explicitly flags unsupported cached bid prices. Count resolution is not validation of the rest of an extraction. Campaign omissions of known participants have a mixed-source-limit disposition and remain unresolved; all 18 warnings have retained explanations.

Cohort count reviews use the same dispositions and quotation checks but bind the input text, response and discrepancy. Their keys live under each selected document's `count_reviews` mapping. Registry files are evidence artifacts; do not add case-specific exceptions to extraction code or the shared prompt.

No shared prompt, production cache version, historical extraction result or backfill rule was changed by this workflow. Paired trials and difficult-case repeats are complete. The live report records the failed full-relationship qualification and remaining source/schema/identity limits; no blanket archive-wide pass is assigned.
