# Document extraction

Bids and awards inside PDFs (Award Summary Forms, committee staff reports, composite reports, and TRCA, Exhibition Place, and Toronto Zoo board reports) are extracted with a single LLM prompt rather than per-source parsers. The older regex and pdfplumber parsers were removed in #205 after evaluation against human labels.

For batch queuing, scheduling, and billing recovery, see [claude-batches.md](claude-batches.md). For provider configuration and budget limits, see [deploy/README.md](../deploy/README.md).

## Corpora

`extraction.CORPORA` maps each corpus name to the `background_pdf` rows it covers:

- `trca`, `ep`, `zoo`, `ttc`: agency board reports.
- `award_summary`: City of Toronto Award Summary Forms.
- `composite`: 2009-2012 Bid Committee composite reports.
- `ba_report`: Bid Award Panel staff reports. Bids are stored in `bid` keyed on the council reference; awards are omitted because they already exist on the solicitation spine (#216).
- `bd_report`: 2013-2016 Bid Committee staff reports (see [bd-report-recovery.md](bd-report-recovery.md)).
- `committee`: City Council and Standing Committee award staff reports.

To add a new document-backed source, add a corpus entry and a branch in `backfill_from_extraction`. Corpus names are also listed in `cli.py`, `_CORPUS_SOURCE`, and `_CORPUS_BUYER_SLUG`; search for an existing corpus name when adding a new one.

## Provider, prompt, and cache

- `extract.py` owns the shared prompt, output validation, and provider interface.
- Direct Claude (`claude.py`) is the default provider, with no automatic fallback. Legacy OpenRouter requires explicit opt-in via `TB_LLM_PROVIDER=openrouter`.
- `llm_budget.py` reserves each attempt against a local monthly budget before calling the API, then reconciles against reported token usage. Attempts with unknown billing outcomes keep their reservations.
- Routine nightly extraction uses `batch_extraction.py`; standalone CLI commands run synchronously unless `--batch` is passed.
- `extraction_cache` is keyed by `(sha256, extractor_version)`, where `EXTRACTOR_VERSION` is `v1-<prompt hash>`. **Any edit to the shared prompt invalidates the entire cache** and triggers re-extraction on the next run. Treat prompt changes as a deployment event.

## Classification gate and extraction flow

`extract_corpus` skips documents with no text and documents ruled non-procurement by the classification gate:

- If a URL appears in the static label snapshot (`config.CLASSIFICATION_LABELS_PATH`), that label decides it.
- Otherwise, for gated corpora (`_CLASSIFIER_GATED_CORPORA`), `classify.py` (#229) runs a pure-Python character n-gram linear model using weights in `data/document_classifier.json.gz`. It skips only documents confidently classified as non-procurement (threshold chosen in cross-validation to lose zero labelled bid documents).
- `award_summary`, `committee`, and `ba_report` are never classifier-gated because they are procurement documents by nature.
- Regenerate weights with `docs/ground-truth/document-classification/build-classifier.py` (uses `scikit-learn` via `uv run --with`; do not edit the weights file by hand). The script refuses to write a threshold that drops any labelled bid document.
- `_skip_reason` is shared by `extract_corpus` and `_count_uncached`. Keep them in sync so keyless runs behave properly (#220).
- Each request carries at most 400,000 characters of report text by default. Longer documents split at blank lines where possible and at the character limit when a paragraph exceeds it. The chunks preserve the original text and order.
- Per-document errors are logged and counted without stopping the corpus.
- `check_declared_counts` logs a warning when extracted bids fall short of a document's stated bid count. Higher counts are kept because documents sometimes declare only compliant bids. These warnings are log-only and noisy on composite reports, which often state a count without listing bidders.

`extract_and_backfill` extracts uncached documents and then runs backfill. Without an API key it succeeds if every eligible document is already cached; otherwise it raises. When passed a `failures` list, it appends `extract:<corpus>` if any document failed.

## Backfill and table swap contract

`backfill_from_extraction` rebuilds each source's derived rows from `extraction_cache` under these rules (#215):

1. Derive all rows before modifying the database.
2. Never overwrite a table with an empty derived set (prevents a machine without the cache from wiping the archive).
3. Abort without writing if fewer than `_MIN_SWAP_COVERAGE` of the corpus's previously extracted documents are cached at the current `EXTRACTOR_VERSION`.
4. Apply changes in one transaction via `db.rebuild_rows` (#218): upsert every derived row on its conflict target, then delete only rows for that source that were not touched. Surviving rows keep their `id` and `first_seen`, refresh `last_seen`, and take the new derived values. (Nullable key columns not wrapped in `COALESCE` in the conflict target, such as a NULL `agency_bid.buyer_id`, do not match and are re-inserted.)

### Field rules

- **Tax basis (`hst_basis`)**: Do not compare prices across tax bases. Backfill maps `including_HST` to `including`, `plus_HST` to `excluding`, and leaves `"net of taxes"` or unknown values as `NULL`.
- **Bid metadata**: City and agency bids store compliance `status` independently of price, and preserve verbatim `price_header` when present in the cache (otherwise `NULL`).
- **Agency dates**: Use the posted, closed, and decision dates returned by extraction.

## Agency references, coverage, and reviewed identities

- **Reference canonicalization** (`linking/agency_reference.py`, #217): Canonicalizes known body-specific reference formats. A single contract without a reference falls back to its council item reference or report URL. Multi-contract documents without clear references are refused and counted. Proven aliases are reconciled inside the same transaction, keeping canonical IDs, earliest `first_seen`, latest `last_seen`, and non-null metadata, and merging duplicate `agency_solicitation` aliases. Ambiguous identities are never merged.
- **Coverage ledger** (#257): `agency_derivation` records accepted and refused contracts along with source/result hashes, extraction version, contract position, and URL. Decisions and the latest `agency_coverage` summary commit with the derived-row swap, while older refusal history stays in the table. Inspect with `tb agency-coverage` (optionally with `--baseline` against a read-only database). Coverage deduplicates across URLs by `(document_hash, contract_index)` and counts alias consolidations separately. `extract_and_backfill` reports refusals as `identity:<corpus>` failures even when other contracts in the corpus succeeded.
- **Reviewed agency identities** (`linking/agency_identity.py`, #258): `data/agency_identity_evidence.json` binds published identifier roles and quoted passages to a buyer, PDF hash, extraction version, and exact contract fingerprint.
  - Unknown sources keep the standard conservative rules.
  - Changed reviewed TTC contexts or malformed evidence abort the rebuild before replacing rows.
  - Reviewed TTC entries can use `withheld_bid_fields` to null out unsupported tax or compliance claims (leaving prices, names, participation, and raw cache payloads intact; see [ttc-history.md](ttc-history.md)).
  - New review entries bind the full result digest and contract index even when no dates are edited.
  - Zoo historical confirmations can reconcile in the ledger when an exact priced original source exists (see `docs/zoo-ep-identity-review-2026-10-30.json`).
  - TRCA decision-date reviews bind the full result digest and contract index to carried resolutions (see `docs/trca-identity-review-2026-10-29.json`).
  - Shared Vendor of Record (VOR) ceilings can be withheld as individual awards while still keeping supported bids, reported under `coverage:<corpus>`. Accepted and refused contract counts can overlap for partial recoveries.
  - Legacy reference corrections require matching report, supplier, and amount before re-keying history. Parent references and suffixed call numbers stay distinct unless explicitly reviewed.

## Model choice (2026-10-23 evaluation)

Direct Claude (`claude-sonnet-5-5`, `high` effort) is the default; nightly runs use Anthropic's Message Batches API for a 50% token discount. Measured on a 12-document comparison set (7 Waterfront months, 3 TRCA reports, 1 EP award, 1 non-procurement report):

| Configuration | Sync cost (12 docs) | Median latency | Waterfront relationships | Auto checks |
|---|---:|---:|---:|---:|
| Sonnet / `high` | $0.226 | 8.4s | 44/44 | 11/12 |
| Haiku / `medium` | $0.017 | 8.4s | 42/44 | 9/12 |
| Haiku / `xhigh` | $0.029 | 14.5s | 43/44 | 10/12 |

Haiku missed a recipient and introduced an attribution spelling error even at `xhigh`, and multi-view voting shared errors across runs. Batching Sonnet cuts cost in half without lowering extraction quality. Qualify Haiku (or Haiku + selective Sonnet escalation) on a broader held-out set before changing the production default.

## Parsing discipline

These rules apply to the remaining rule-based parsers (agenda HTML tables, feed normalizers) and to evaluating model output:

- **Stop if rules diverge.** If fixing one edge case keeps introducing new ones (as happened with regex attempts in #151), stop and ask whether the corpus can be parsed cleanly at all rather than piling on document-specific exceptions. "No clean rule-based extraction exists" is a valid conclusion (#83).
- **Measure against ground truth in the documents**, not against the code being replaced. Compare extracted counts against stated counts like `Number of Bids Received`. Ground-truth label sets live in `docs/ground-truth/`.
- **Refuse and log instead of guessing.** A logged refusal is a visible gap; a bad guess corrupts the archive.
- **Record document defects as published.** Source PDFs contain typos and malformed prices (such as `$1,479,386,.57`) or tables showing only compliant bids. Do not invent workarounds that alter what the City published.
- **Evaluate per corpus.** What works for Award Summary Forms or EP reports does not automatically transfer to council staff reports (#116).
