# Extraction qualification baseline — 2026-11-12

This dated baseline is superseded by the [November 13 continuation](extraction-qualification-2026-11-13.md). Its measurements describe the initial implementation; the cohort and machine-readable report links now reflect continued work.

[Issue #302](https://github.com/CivicTechTO/toronto-bids/issues/302) remains incomplete. The new offline workflow makes the remaining evidence gaps reproducible; it does not assign an archive-wide Claude pass. No paid requests, provider changes, prompt edits, production-cache writes or extraction backfills occurred.

The [cohort and reproduction instructions](ground-truth/extraction-qualification/README.md) select 15 inputs across all nine active PDF corpora and Waterfront. Seven have independent agent-written source labels, with literal evidence and separate text/document hashes. Eight remain explicitly pending. Existing human labels remain unchanged. All original source snapshots, selected parsed responses, available provenance and the [machine-readable report](ground-truth/extraction-qualification/report.json) are committed together.

## Coverage and measurements

Six retained trial sets include October Sonnet/high, its nested-list repeat, medium-effort Sonnet and Haiku, the initial incomplete batch and the corrected batch. Every trial reports every selected document. There are 29 retained selected attempts across those trial sets, with other selections explicitly missing. The initial and corrected Waterfront batches remain unreviewed by the new relationship scorer; their historical source checks remain attached to their artifacts.

Only TRCA D01 and the two EP documents currently have both reviewed labels and retained responses. Across three synchronous model configurations:

| Corpus | Reviewed relation observations | Exact matches | Attribute discrepancies | Other surplus observations |
| --- | ---: | ---: | ---: | ---: |
| TRCA D01 | 33 observed / 30 expected | 29 | 1 | 3 role/stage false joins |
| EP bus duct | 18 observed / 18 expected | 10 | 8 | 0 |
| EP WSIB negative | Three empty-contract results | All three negative checks agree | 0 | 0 |

These are exact-field diagnostics against the new source-reviewed labels, not final factual-error adjudications or an aggregate model ranking. D01's three surplus relationships arise from placing invited/actual tenderers in the schema's prequalification-only list; another response leaves the explicitly acceptable winning bidder's status unstated. EP disagreements concern tax-basis and compliance-status labels; all five submitted prices and the distinct recommended award amount remain present. Source-review these differences before interpreting the stricter scores as extraction errors. A completely empty negative document also has to pass the contract-membership check.

The retained synchronous requests do not have exact saved request/schema identities. Consequently even matching relations cannot establish complete qualification provenance. The corrected batch retained exact request bodies, but full Waterfront source-path scoring and paired repeats are still absent. Six other PDF corpora and Zoo have no selected Claude responses in these historical trials. Missing work stays in coverage rather than being removed from the denominator.

Known selected-attempt charges total **USD 0.340808**. This reuses reported historical usage/ledger evidence and is not new spending. Some retained attempts lack charges; missing trial inputs also have no charge. The report distinguishes known totals from unknown entries and does not infer a zero cost for either. This total is not the cost of all October qualification requests.

## Runtime discrepancy audit

A read-only pass over the held archive evaluated 3,082 cached source records and found **1,510 declared-submission shortfalls**, with no malformed cached payloads. These counts are dated measurements, not claims about the current live archive. Multiple source records can share an extraction cache entry. No dispositions were applied. Full audit output is retained locally in `.validation-extraction-qualification-302/runtime-counts.json` and can be reproduced with `tb extraction-counts` against the same archive.

The shortfalls are a review workload, not 1,510 proven extraction failures. Sources can publish only the winner or a compliant subset. The new review mechanism binds any such explanation to document hash, prompt version, complete response and exact discrepancy, and requires a source quotation. It exposes stale review keys and preserves existing supported facts.

## Remaining qualification work

- Finish complete source review of TRCA multi-contract/category cases, TTC line-item allocations, the BA rescission control, all composite appendices and Waterfront publication relationships.
- Add Waterfront scoring through its actual source-verification path, preserving unsupported raw observations and the unnamed dewatering recipient gap.
- Source-review strict tax/status disagreements and the committee report's inconsistent initial-term/option-period description. Keep procurement identity and carried-decision adjudication linked to [#282](https://github.com/CivicTechTO/toronto-bids/issues/282).
- Capture complete exact-request/response provenance for any new isolated trials. Run matching synchronous/batch requests and independent difficult-case repeats under the predeclared per-corpus thresholds.
- Review material runtime count discrepancies with source evidence. The entire workload must not be auto-approved from the count alone.

The evaluator and count-review tests cover false joins, amount/basis/status errors, duplicates, complete-contract omissions, negative documents, incomplete input coverage, request/text tampering, unknown provenance, configuration parity, distinct repeats, evidence changes and read-only database behavior. Existing extraction/batch preservation regressions remain in the validation suite.

Validation: **1,852 passed, one skipped** in the complete offline suite, including 20 qualification tests. An earlier full run crossed a one-second boundary in the existing City received-participation timestamp comparison; that unchanged test passed separately and on the subsequent full run. The committed qualification report reproduces exactly, and `git diff --check` passes.
