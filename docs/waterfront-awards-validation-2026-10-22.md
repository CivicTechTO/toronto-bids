# Waterfront own-site award capture validation — 2026-10-22

Scope: the public [Awarded Contracts page](https://www.waterfrontoronto.ca/opportunities/procurement/awarded-contracts), under [explicit permission](permissions/waterfront-toronto.md), for the own-site slice of #108. Bonfire, current opportunities, vendors of record and ACANs are outside this change.

## Captured and checked

The page contains 510 visible source text blocks across 111 publication months in 2017–2026. Blocks are not synonymous with contracts or award claims: some supply context for subsequent nested supplier lists; some contain multiple awards. The structural parser verifies that all visible year-body text survives capture. Raw HTML is retained before parsing. The final live snapshot SHA-256 was `eeb62f7104eb8d69d87fac1d84bf438fd64011dcc5d24a3fb20174dccc0457e5`.

The existing shared extraction client was run on every month, without changing its prompt or cache version. Literal source verification supports 508 supplier/value claims. Supplier rebuild links 507 to supplier identities; the remaining source recipient is a “Various…” grouping, deliberately excluded by the existing supplier policy.

Representative source checks:

| Source cohort | Verified behavior |
|---|---|
| September 2017 | Technical pilot heading retains context for four supplier-first nested entries; eight supported claims in the month. |
| October 2021 | Separate title followed by four play-equipment suppliers retains the relationships; nine claims in the month. |
| July 2022 | Two differently worded demolition entries publish the same supplier/value. Exact extracted titles distinguish their source blocks; both remain separate archive observations. No physical-contract equivalence is inferred. |
| July 2025 | Enbridge `$5,0000,000` remains literal with numeric NULL. |
| February 2025 | `$2,750 per week as and when required` remains a rate, numeric NULL. |
| October 2017 | `$858,000,000 including construction trade packages` retains the scope qualifier, numeric NULL. |
| March 2019 | `$79,55` remains malformed, numeric NULL. The `(2018)` in its title is not mistaken for another price. |
| November 2019 | `Ketchum Canada (2002) Inc.` retains its complete recipient name; its corporate year is not another price. |

## Explicit unresolved source entry

March 2023 publishes:

> Dewatering System for Chamber CC6567 Rebuild Operations for Port Lands Flood Protection ($313,750) (Contract awarded by Ellis Don Civil Ltd on behalf of Waterfront Toronto)

No winning supplier is named. The model assigned Ellis Don Civil Ltd as the recipient from the attribution; verification refuses that assignment. The original source block, value and manager annotation remain available. The other four supported awards from that month are stored. `publication_extraction` reports this month as incomplete, and standalone/nightly execution reports failure rather than declaring full coverage. All other 110 months pass the implemented evidence checks. An `ok` month is verification of extracted source claims and monetary coverage, not a publisher-declared award count.

## End-to-end rehearsal

Used SQLite's backup API to copy the live production database to `/home/alex/tb-data/operations/108/rehearsal/bids.sqlite`. Seeded that copy with the 111 actual monthly model results, keyed by input hash and the unchanged shared extractor version. Then fetched the live public page once through the implemented capture, with `limit=0` to forbid new model requests.

Result: 510 source blocks, 111 months, 508 supported claims, zero new model calls, one incomplete month as described above. Built suppliers and procurement identities, then generated JSON, schema, CSV zip and all Parquet tables. All accepted claims have procurement IDs. `PRAGMA foreign_key_check` returned no violations. Hashes of every original source-table payload (excluding derived supplier/procurement IDs) were identical before and after. Production was not modified.

Operational evidence is retained under `/home/alex/tb-data/operations/108/`: original page/model captures in `investigation/`, rehearsal output and exports, and `validation.json` with per-month status and original-source payload digests. Cached replay and stable IDs/first-seen timestamps are also covered by offline tests.

The complete offline suite passed: **1,127 tests**. Fixtures contain the real HTML and real shared-model responses for the representative regression cases, including the refused manager-as-recipient assignment.
