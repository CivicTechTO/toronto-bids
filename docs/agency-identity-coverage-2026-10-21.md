# Agency identity coverage and baseline reconciliation (#257)

The identity rules added under #217 refuse unsupported identities, but the nightly
previously logged refusals and then reported success unless some other operation failed.
Derived rows could disappear during rebuilding without an inspectable account of the
affected evidence. #257 records the decisions and makes that coverage gap operationally
visible. It leaves the identity rules and extraction prompt unchanged.

## Operator workflow

`tb enrich-agencies --only trca` uses held reports and cached extractions by default.
Identity refusals make it exit 1 while valid contracts still rebuild. Refusals appear as
`identity:trca`, separately from `download:trca` or `extract:trca`. Nightly uses the same
path, marks the agencies step and `sync_run` failed, and continues through export.
`tb status` also displays the persisted coverage summary, so a standalone rebuild's
refusals remain visible after its terminal output has gone.
Missing eligible extractions still require the existing extraction API; this change
does not make enrichment universally offline.

Inspect the latest successful rebuild and historical decision evidence:

```sh
tb agency-coverage --only trca --out /tmp/trca-coverage.json
tb agency-coverage --baseline /path/to/bids.before-nightly.sqlite --out /tmp/reconciliation.json
```

The inspection command does not rebuild or extract. It returns 1 for uninitialized
coverage, current identity refusals, or unmatched baseline facts, after writing its JSON.
The baseline opens with SQLite `mode=ro`; the configured current database follows normal
CLI initialization. A rebuild timestamp identifies the snapshot being inspected.

`agency_derivation` stores the buyer, source PDF SHA-256, extraction version and full-result
digest, original contract index and count, source URL/item context, original contract JSON,
and accepted reference or refusal reason. `active=1` selects decisions from the latest
successful rebuild. A changed cache result or changed identity decision creates historical
evidence rather than overwriting an earlier refusal. The ledger and summary commit in
the same transaction as derived rows; failed swaps roll all of them back. Ledger records
are internal SQLite evidence, outside the public JSON/Parquet/CSV table set.

## Counting

An indexed URL is an observation, not necessarily a different document. Coverage counts
each `(corpus, PDF SHA-256, extraction version, result digest, contract index)` once across
URLs. Bid/award fact counts include named suppliers in those contracts; they are not
distinct table-row counts. All URL contexts remain inspectable. Accepted/refused sets can
overlap if contexts yield different decisions for the same extracted contract.

Alias normalization/consolidation counts are stored rows touched across solicitation,
bid and award tables in that rebuild. They are separate from refused facts. Later runs
can correctly report zero newly normalized aliases while reporting the same refusals.
The preexisting empty-derived-table safeguard remains: all-refused evidence is still
recorded even when an empty derived table leaves previously held rows untouched.

## Isolated production replay, 2026-10-21

The replay used a SQLite backup of production in `/tmp/tb-257/replay.sqlite`, the held
current cache, and the pre-nightly `operations/224/bids.before-capture.sqlite` baseline.
It performed no network requests, paid extraction, production writes or publication.
The complete baseline disposition and deduplicated source evidence are in
[`agency-identity-reconciliation-2026-10-21.json`](agency-identity-reconciliation-2026-10-21.json).
Each unmatched row references entries in the artifact's `evidence` dictionary.

| Agency | Accepted contracts | Refused contracts | Affected bid facts | Affected award facts |
|---|---:|---:|---:|---:|
| TRCA | 802 | 1,028 | 20 | 1,362 |
| Zoo | 76 | 31 | 9 | 27 |
| Exhibition Place | 110 | 6 | 1 | 6 |

These are deduplicated extraction facts, not the historical row decline. TRCA's old
1,192 refusal log count included repeated document URL observations.

Every baseline award/bid row receives one disposition:

| Disposition | Award rows | Bid rows |
|---|---:|---:|
| Same identifier, supplier and amount | 446 | 618 |
| Proven canonical identifier alias, same supplier and amount | 742 | 1,066 |
| Same bid key, changed stored amount (listed separately) | 0 | 42 |
| Same report/supplier/amount under another reference | 26 | 37 |
| Absent from stored rows; linked to refused identity evidence | **140** | **12** |
| Total baseline rows | **1,354** | **1,775** |

The same-fact comparison is diagnostic, not an identifier equivalence or authorization
to merge. Forty-two surviving bid keys have a different price from the baseline;
`changed_bid_amounts` retains both rows rather than presenting them as unchanged facts.
The earlier key-only investigation grouped them with surviving identities, which explains
its different intermediate counts. Different URLs observing one PDF can also leave a
different final `report_url` on a surviving row; a supported identity match therefore does
not require the final URL to remain identical.

The 140 absent awards comprise TRCA 121, Zoo 13 and EP 6; the 12 absent bids comprise
TRCA 10, Zoo 1 and EP 1. Every one links to current refused cached contract evidence.
Reasons include reference-less multi-contract reports and multiple distinct identifiers,
such as RFQ 10043863 / contract 10043792 and RFP 10042179 / contract 10040929.

Those source facts remain unresolved. Recovery with a proven identity belongs to #258;
this work supplies the enduring ledger and reconciliation needed to do it safely.

## Validation

The full offline suite passes (1,049 tests). Regression tests exercise the actual
derive/rebuild/capture/nightly paths, with mixed accepted/refused contracts, duplicate
document hashes, stored aliases, all-refused tables, cache and decision changes,
transaction rollback, and changed bid amounts. Three held source fixtures retain the
original extraction JSON and its digest.

The actual CLI ran against an isolated replay: inspection returned 1 with all 140/12
unmatched facts linked to refusal evidence; `tb status` showed all three agencies as
incomplete. A second complete rebuild preserved ledger IDs, `first_seen` and contract
payloads. Replay scripts and full operational reports remain in `/tmp/tb-257/`.
