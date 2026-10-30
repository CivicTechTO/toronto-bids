# City bid coverage audit (#163) — 2026-10-30

The current archive has **2,336 of 6,444 awarded City solicitations with captured bid observations** and **4,108 without (63.75%)**. Source-proven council-reference links account for **1,009** covered solicitations that a direct-document-only check would miss. Every attributed bid ID reconciles to the canonical procurement identity; there are no ambiguous reference groups or canonical-attribution disagreements in this snapshot.

This is a measurement of captured observations, not a conclusion that 4,108 procurements had no bids. No amount threshold, era, empty extraction, or missing report proves that. Stored bid observations also include source-labelled non-compliance, withdrawal and no-bid statuses; their presence does not prove multiple competitive submissions.

## Population and conservation

Count each `solicitation.document_number` once when it has at least one `award.source='odata'` row. Multiple suppliers/award lines and the CKAN cross-check do not add denominator rows. CKAN-only awards, orphan awards, composite Call Numbers, agency contracts and noncompetitive workspaces stay outside this City-spine population. One document has a mixture of unlabelled and `not_an_award` lines; a mixed document remains in this source-row population, while the labelled non-award line is excluded from value prioritization.

A bid's explicit document identity wins. A reference-only bid is attributed only when the union of dual-key bid evidence and `solicitation_link` names one document. A council item/report mentioning several procurements does not distribute each bidder to every procurement. The independent canonical-ID comparison checks the exact bid IDs, not merely equal totals.

| Solicitation issue-year cohort | Awarded City documents | Without captured bids |
| --- | ---: | ---: |
| through 2012 | 706 | 701 |
| 2013 2018 | 3,470 | 2,467 |
| 2019 | 479 | 424 |
| 2020 onward | 1,789 | 516 |
| unknown | 0 | 0 |

These cohorts use **solicitation issue date**, not award date or report date. The old 554/266/99 figures used older populations and different criteria; they are not silently carried forward as current estimates. Composite-era observations under Call Numbers are not attributed to a City document without proof, and the absence of a published bidder list in one historical source does not establish absence in every source.

## Evidence inventory for the 4,108

| Evidence state | Solicitation count |
| --- | ---: |
| Held City PDF contains named cached observations requiring attribution/source review | 34 |
| Current cache has no named bids; source review still needed | 2 |
| No stored City-PDF association or explicit held document-number mention found | 4,072 |
| **Total** | **4,108** |

Only **five** missing documents already have stored report associations. Scanning explicit `Doc`/`Document Number` mentions in held `bgrd`, `committee_award` and `award_summary` text finds **36** candidate documents in total, including the five. This is a **review queue**, not an identity join. Cached counts here cover the entire report and may describe other contracts, prequalification rosters or later tenders.

For the 4,072 remainder, the audit does not search the web, unreadable PDFs, unassociated reports lacking an explicit document marker, council HTML agendas, or a historical Call Number matcher. They need discovery or source-proven linkage; they are not declared staff-delegated, noncompetitive, zero-bid or unrecoverable.

The actual number of source-confirmed zero-bid solicitations is **not established** (`null` in the machine report). No credible numeric “genuine floor” can be inferred from this inventory alone.

## Ranked recovery queue

1. **Restore three held committee submission lists (#164).** The actual PDFs list six submitters for Doc2305234907 (arboriculture), eight for Doc3448368603 (basement flooding engineering), and five for Doc4929553192 (bridge program consulting). The 19 names/list memberships were independently checked with `pdftotext` against hash-verified PDF bytes. Prices are unpublished in these lists; no amounts are inferred. Cached compliance/status labels still need review before promotion, particularly technical evaluation failures. [Source evidence](source-review.json).
2. **Review the remaining cached panel-report candidates.** An isolated replay of the existing committee/panel caches makes **44** previously uncovered documents appear covered, with no newly uncovered documents. The 44 **includes** the three above. This is a counterfactual, not approved new coverage: it adds **1,010** stored bid rows across the two sources, including observations for documents already covered, and changes **three prior status fields**. It retains all prior bid IDs and leaves award, solicitation and raw-cache payloads unchanged. Mixed identifiers, roster semantics, status changes and duplication need source review. [Exact replay](cached-replay.json).
3. **Find the ferry supplemental report (#164).** Doc4053424337's held PDF describes an ongoing evaluation/negotiation process and explicitly promises a supplemental report. That is a concrete discovery lead, not evidence that no bidders exist.
4. **Work the historical/modern discovery remainder as one inventory (#164/#167).** Use [missing.csv](missing.csv) across all issue years; keep report discovery and identity proof separate. `max_parseable_award_line` is only a triage field: it is the maximum eligible positive parsed OData line, not a contract total or validated economic value. Known corrupt/unknown/non-amount/non-award labels and unparseable/foreign-currency amounts are excluded. Do not sum it into a spending claim.
5. **Keep count-only and parent-call cases incomplete.** Doc5425112657's source explicitly declares two bids but publishes no bidder list in the held form. Doc3512316731 and Doc4906085019 appear as parent RFSQs in later tender reports. The later tender's bidder list is not proof of bids on the earlier RFSQ. These are recorded counterexamples to blanket bridging in [source-review.json](source-review.json).

## Reproduce

From `scrapers/`, with a resolved production snapshot or released SQLite database:

```sh
uv run python -m toronto_bids.bid_coverage --database /path/to/bids.sqlite --out /tmp/bid-coverage
uv run python ../docs/audits/bid-coverage-2026-10-30/replay.py --database /path/to/bids.sqlite --out /tmp/cached-replay.json
```

The first command opens SQLite read-only and enables `query_only`. The second backs up that read-only input into a mode-0600 file inside a private temporary directory, replays only existing caches and resolves identities **on the temporary copy**, then deletes it. Neither calls an extraction provider or publishes data. The CLI exits nonzero if canonical identities disagree with the audit's source-key attribution. The snapshot is pinned in [snapshot.json](snapshot.json); the private database is not committed. All 4,108 document contexts are in [audit.json](audit.json), with a compact complete inventory in [missing.csv](missing.csv) and priorities in [recovery-queue.json](recovery-queue.json).

Validation: **1,426 tests pass**, including six audit tests for award-source deduplication, ambiguous multi-contract references, canonical-ID drift, prequalification mentions, cache/evidence states, older-era observations and deterministic read-only output. Production data, links, prompt and extraction caches were not changed. No model requests or new scraping were needed.

## Issue disposition

#163's measurement is refreshed by this audit; its old body and percentage should be superseded. The remaining recovery work belongs to the bounded queue above, with #164/#167 tracking committee/source discovery and #103/#282 retaining their separate agency scope. This audit does not close those coverage gaps or justify relabelling missing bids as an actual zero.
