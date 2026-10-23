# Direct Claude validation, 23 October 2026

The saved Anthropic key authenticates successfully. The configured provider is direct Anthropic, the model is `claude-sonnet-5-5`, the local monthly allowance is USD 20, and the service environment file has mode 0600. The Models API returned both reviewed Claude model IDs. Credentials and request headers are absent from this report and the saved validation artifacts.

The selected configuration is **Sonnet 5.5, high effort, schema-constrained JSON**. Haiku remains opt-in. Production stayed on merged `main` throughout; tests ran from an isolated PR worktree. No extraction responses were imported into the production document cache, whose digest remained unchanged. The operational usage ledger records the actual requests.

## What the live checks found and changed

The first client relied on the prompt alone for JSON formatting. Sonnet returned explanatory prose followed by fenced JSON for the real July 2022 Waterfront input; the parser rejected it. The failure reproduced on the same source. The old OpenRouter path had requested JSON output, so omitting the corresponding Claude constraint was an integration gap.

The corrected client sends `output_config.format` with a complete extraction JSON schema. The shared prompt text and `v1-b63aa8c835ae` cache version remain unchanged. Schema validation initially rejected a nullable enum represented by a type array, then rejected 17 union parameters. Typed `anyOf` branches and the prompt's required string title produce an accepted schema with 16 unions. Offline checks protect those provider constraints. See [Claude structured-output documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

A full medium-effort comparison then exposed a separate quality problem: Sonnet returned only one of eight supported awards from September 2017's nested supplier-first list. All other Waterfront relationship checks passed. At high effort Sonnet recovered all eight awards in a focused retest and again in the final full pass. High is also the model's documented default effort; see [Sonnet model guidance](https://platform.claude.com/docs/en/models/sonnet-5-5/overview). This bounded evaluation supports the chosen setting, not a guarantee of error-free future extraction.

## Document coverage and results

Each complete model pass used the same 12 real inputs and unchanged shared prompt:

| Inputs | Checks |
|---|---|
| Seven Waterfront months: 2017-09, 2019-03, 2019-11, 2021-10, 2022-07, 2023-03, 2025-07 | 44 supported source-key/supplier/raw-amount/numeric-amount relationships, nested lists, repeated supplier/value observations, titles containing years and malformed published money |
| Human-labelled TRCA D01, D02 and D04 | 34 bidder/reference/price relationships, including prequalification exclusions and multiple contracts in one document |
| Exhibition Place 2019 electrical bus duct report | Five bid prices; distinguishes the $418,854.47 tender from the $403,854.47 recommended award |
| Exhibition Place WSIB report | No procurement contracts |

Waterfront expectations use the held source-verified claims, not record counts alone. TRCA comparisons retain the human labels and show discrepancies for review. Supplier matching normalizes whitespace, case and apostrophe style for evaluation; production stores verbatim names.

* **Sonnet, medium:** 10/12 automatic checks passed. The nested-list omission and TRCA D04 label discrepancy were visible failures. An additional printed-reference prefix difference appeared in D04; the source has the same printed call number. This comparison did not change production identity rules.
* **Haiku, medium:** 9/12 automatic checks passed. It recovered the supported relationships, but returned `(3,579,797)` and `(50,000)` where the held derivation used bare numeric extents. The existing conservative parser left those numeric values NULL. D04 had the same Bell Canada label discrepancy. These numeric regressions prevent promotion of the cheaper model in this change.
* **Sonnet, high:** 11/12 automatic checks passed. All seven Waterfront sets matched the 44 held supported relationships exactly. The remaining automatic mismatch is the source/label disagreement below. The Exhibition Place bid/award distinction and non-procurement exclusion passed.

### TRCA D04: preserve the label disagreement

The human label assigns Bell Canada a $235,000 bid price on `2104-09-3006`. The source describes that amount under financial details as anticipated annual ongoing-service costs based on a review of 2019 spending. It does not establish a submitted tender price. Both models returned a NULL bid price while retaining the bidder. In the final high-effort pass, this is the only bidder/reference/price mismatch among all 39 TRCA and Exhibition Place bids.

Returning NULL is consistent with the shared instruction to avoid inventing a bid price. The original human label remains unchanged; the automatic result remains recorded as a mismatch. Review this label before using D04 as an exact bid-price oracle.

### Waterfront March 2023: source coverage stays incomplete

The source still supplies no winning supplier for the $313,750 dewatering entry. Final Sonnet/high output preserves the four supported claims without adding a fabricated recipient, while the verifier reports the remaining unmapped monetary evidence. The benchmark checks conservation of those four supported claims; it does not count the missing recipient as resolved.

## Usage, caching and conservation

All live work, including failures and repeated validation, made **46 Messages requests**: 44 HTTP-success responses and two schema rejections. The ledger reconciles every reported token counter to the reviewed prices, with no unsettled reservations. The two schema rejections released their allowances. Total estimated cost is **USD 0.586624**. This includes the HTTP-success outputs rejected as malformed JSON. The Models API authentication check was separate.

The final 12-document Sonnet/high pass cost **USD 0.226494** by reported usage. Later requests reused the 1,184-token instruction prefix; usage showed cache creation followed by cache reads. These measured samples are not a monthly forecast for larger documents. The local budget still reserves a conservative allowance before every attempt and the Console workspace limit remains the account-level control. Prices: [official Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing).

The production extraction-cache digest remained unchanged across every validation run. Existing extraction results remain reusable, so deployment does not trigger a historical re-extraction. Credentials remain in the service environment file; the public procurement export contains neither credentials nor the usage ledger.

Offline regression suite: **1,189 passed**, including the structured-output request constraint, schema complexity, billing of rejected extraction results, reservations across retries, concurrency and month rollover.

Host evidence is retained under `/home/alex/tb-data/operations/claude-validation-2026-10-23/`: all trial reports, captured responses where available, the validation harness, Models result and independently reconciled `summary.json`. Earlier failures remain alongside the final pass. Human source documents and held Waterfront responses are already in the repository fixtures and ground-truth directories.
