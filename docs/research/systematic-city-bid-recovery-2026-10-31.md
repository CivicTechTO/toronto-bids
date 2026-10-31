# Systematic City bid recovery: findings and recommended next build

Research date: 2026-10-31. This investigation made no production changes, paid model requests, or new identity merges. It used a consistent read-only post-recovery database snapshot, held official agendas, bounded downloads of primary PDFs, and the ordinary anonymous City meeting/search UI. Reproducible probes and source hashes are under `.validation-systematic-bids-2026-10-31/`.

The 19-observation recovery was valid but addressed only three procurements. The stronger next step is a systematic source-acquisition and identity pipeline. We found both an explicit extraction blind spot and a legacy identity defect; increasing extraction volume alone would leave both unresolved.

## Current population and what it means

There are 6,444 awarded City-spine solicitations, 2,339 with captured bid observations and **4,105 without**. The source population and unambiguous-reference attribution follow the merged [#163 audit](../audits/bid-coverage-2026-10-30/README.md), measured again after the 19-observation recovery. Exact stored-key/canonical-ID agreement checks consistency, not whether an original identity decision is factually correct.

| Issue-year cohort | Without captured bids |
| --- | ---: |
| Through 2012 | 701 |
| 2013–2018 | 2,467 |
| 2019 | 424 |
| 2020 onward | 513 |
| **Total** | **4,105** |

**3,592 (87.5%) are historical, through 2019.** Neither issue year, value nor missing evidence proves genuine zero bids. Across the whole population, 304 missing documents have a maximum eligible parsed award line of at least $5M, 998 have $500K–$5M, 2,585 are below $500K and 218 lack an eligible value. These are triage counts, not spending aggregates or evidence of publication obligations. Root metrics: `root-metrics.json` in the research evidence directory.

## 1. A proven missing acquisition/extraction stage

`background_pdf` already records **586 undownloaded City Bid Committee PDFs whose item reference has no stored bids**. They are all 2013–2016: 187, 123, 158 and 118 respectively. The other 3,883 queued City staff reports already have item-level bids and are a different population; downloading all queued files blindly would waste effort.

The exclusion is explicit in code:

- [`_BA_REPORTS_WITHOUT_BIDS`](../../scrapers/toronto_bids/sources/bid_award_panel.py) excludes `%.BD%` references from the no-bid download route.
- [`extraction.CORPORA`](../../scrapers/toronto_bids/extraction.py) admits composite reports through 2012 and BA reports from 2017, but omits 2013–2016 BD reports on the assumption that their agendas tabulate their own bids.

A deterministic sample of two queued/no-bid PDFs per year returned eight valid PDFs. **All eight contain actual named bidder/proponent lists**, including prices, non-compliant entries and multipart bid/no-bid results. For example, the [2013 eLearning report](https://www.toronto.ca/legdocs/mmis/2013/bd/bgrd/backgroundfile-60506.pdf) names six proponents, and the [2014 kitchen-products report](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-73230.pdf) contains two named bids including one non-compliant bidder. Selection, all URLs, hashes and observations are in `historical/queued-sample.json`.

This is evidence for adding a systematic **2013–2016 BD staff-report admission and extraction path**, not a promise of 586 newly covered solicitations. A report is not necessarily a unique City document; native Call Number attribution remains necessary.

## 2. Substantial offline linkage headroom, with an important safety limit

Local analysis found **669 currently missing historical documents with Call Number encoding candidates on references that already carry 3,312 bid observations**. Of these, 330 use a ten-digit division/year/sequence call shape; 339 depend on a padded tender-number/year representation. Nineteen have multiple candidate references. These are hypotheses, not new approved identities or 669 recoveries.

A separate **389** missing documents have encoding candidates among the 586 queued/no-bid report references. Two overlap the 669, so the two routes identify **1,056 distinct candidate documents** in total. Their arithmetic overlap is not independent validation; repeated source considerations and bidder-table quality remain to check.

The current encoded-call hypothesis agrees with 1,279 of 1,284 existing legacy links. That is **agreement with existing decisions, not 99.6% measured accuracy**. Some current titles were themselves copied from those decisions and cannot serve as independent confirmation. The [historical research note](historical-bid-headroom-2026-10-31.md) separates printed source identifiers, inferred encodings, reused/compound calls and source conflicts. Do not turn digit stripping or right-padding into unconditional identity joins.

## 3. Existing identity/data defects must be corrected before bulk linkage

Primary source checks confirmed two incorrect legacy associations:

- **2014.BD174.2:** [Tender 12-2014, McGregor Arena](https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/backgroundfile-68240.pdf) was awarded to Integrated Building & Design Corporation for $1.72M. Its stored link instead points at document **5092408869**, a 2025 roadway procurement awarded to D Crupi and Sons for the same amount. The supplier token match is only **`and`**. This reference currently has no stored bid rows, but the false identity is still material.
- **2015.BD25.1:** [Tender 14-2015, facility pavement reconstruction](https://www.toronto.ca/legdocs/mmis/2015/bd/bgrd/backgroundfile-79745.pdf) was awarded to Bond Paving & Construction for $224,400. Its stored link instead points at document **2962047194**, a 2021 Sanscon procurement for the same amount. The shared supplier token is only **`construction`**. Eight stored bid rows are attached to that wrong procurement. The target also carries a title copied from the legacy item, so its derived title is not independent corroboration.

The current [`match_pre_ariba_solicitations`](../../scrapers/toronto_bids/sources/bid_award_panel.py) accepts any supplier-token intersection plus a unique rounded amount across all OData awards; it has no date guard. A unique match is therefore not sufficient proof. Three other identifier disagreements match supplier/amount/year and remain unresolved publisher-identifier discrepancies, not three more proven false joins.

A second defect affects the stored BD25.1 table: the primary PDF gives Bond Paving the first price ($286,536.36), then Serve Construction $359,463.32; the stored table starts Serve at Bond's price, shifts later pairings, and treats a non-compliance footnote as the last supplier at $995,617.27. Equal row counts did not establish correct relationships. The held publisher HTML preserves the correct order. The current parser drops one header line but leaves an extra HST line and a supplier-column footnote; equal-length arrays therefore pass its guard while shifting every relation. A standalone read-only reproduction at `.validation-systematic-bids-2026-10-31/reproduce_relation_error.py` fails on the first expected supplier. This is a reproduced parser defect, and these rows cannot be calibration truth until corrected.

Corrections must preserve original source bytes and history, detach incorrect associations and repair any previously merged canonical identities/derived titles explicitly. Removing a `solicitation_link` alone is insufficient if its canonical keys have already been merged. Retain valid raw observations while withholding unsupported identity/relationship claims. No correction was applied during this investigation.

## 4. Modern committee discovery has measured, source-backed yield

A reproducible 16-document sample of the 513 modern missing documents used four deliberately highest-value RFTs, four highest-value RFP/NRFPs, and eight hash-selected smaller-value targets. **Seven of the eight high-value targets have official reports naming actual submitters; none of those seven URLs is held in production.** Six give complete named lists; the seventh names four out of five declared submissions. One smaller-value case has a held named-list PDF; the other seven were unlocated in bounded searches and remain unknown.

See [modern source sample](modern-bid-sample-2026-10-31.md) for the exact selection and primary URLs. Important regression cases include [Ashbridges](https://www.toronto.ca/legdocs/mmis/2021/ie/bgrd/backgroundfile-168619.pdf#page=6) (four prequalified firms, two actual tender bidders), [Fairbank](https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-248701.pdf#page=11) (two lots, six plus three price observations), [Humber](https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-246668.pdf#page=4) (one unnamed submission), and [the paramedic station](https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-253128.pdf#page=5) (two named submissions despite the earlier zero-bid speculation).

The high-value selection is intentionally enriched for committee-handled awards. Its 7/8 yield must not be projected onto all 4,105 missing records.

## 5. A demonstrated systematic discovery mechanism

The [official schedule dataset](https://open.toronto.ca/dataset/3bfab808-105b-45ab-8d92-cf1916594653/) can seed complete agendas rather than the recorded-vote CSV. One ordinary headed-browser fetch of [2015.CC6](https://secure.toronto.ca/council/report.do?meeting=2015.CC6&type=agenda) exposed **204 item headings and 406 PDF links**, including reports listing nine and five separate winter-maintenance calls. One [2024.GG14](https://secure.toronto.ca/council/report.do?meeting=2024.GG14&type=agenda) fetch exposed **27 items and 37 PDF links**, including the ferry's original and supplemental reports.

The [current official advanced search](https://secure.toronto.ca/council/#/advancedSearch) also searches staff-report attachments. Ordinary public UI requests used `POST /council/api/multiple/agenda-items.json`; searching the middle historical call `6032-14-0193` located its item even though the title only gives a range. Responses provide item identities and totals, so PDF-link resolution and per-contract proof are still required. This is an observed web-app endpoint, not a guaranteed supported API. Only first-page results were tested; complete paging must be reconciled in any adapter.

Tested stateless HTTP routes returned 403; an anonymous headed browser worked. Thus reuse one paced Playwright context for agendas/search and ordinary HTTP for PDFs. Do not assume a browser is required for each PDF or each result. Current-term schedule deduplication demonstrated 138 distinct CC/EX/GG/IE meeting-number combinations; older resources are advertised but complete historical meeting enumeration still needs validation. Full evidence: [discovery research note](systematic-bid-discovery-2026-10-31.md).

## Recommended implementation sequence

1. **Correct and constrain legacy identity/relationship derivation first.** Preserve the two known false-link cases and shifted table as regression fixtures. Check dates, meaningful supplier identity and printed native call evidence; derived titles and equal rounded prices are insufficient. Add an explicit repair/migration path for incorrect canonical merges. Re-measure coverage honestly, even if repairs initially increase the missing count.
2. **One resumable acquisition/extraction job for all 586 eligible BD PDFs, across all four years.** Retain report/call/contract scope, publish no bidder facts until source identity and relation checks pass, and expose blocked/pending states. Use the shared extraction/cache and batched Sonnet, deduplicated by bytes; no per-source parser rewrite or model re-run for unchanged documents. Do not require maintainer approval year by year.
3. **Validate the native Call Number crosswalk against independent source facts.** Evaluate the 669 held-bid candidates and newly captured reports with a stratified pilot, known positives and the confirmed negative cases. Keep conflicting/reused/compound identifiers unresolved. Persist proof for accepted equivalences; do not relax the old winner/price matcher to manufacture coverage. The 1,056 combined candidates are a test population, not an acceptance target.
4. **Add complete agenda indexing plus targeted attachment search for committee/Council gaps.** Start with the seven independently located modern positives and historical multi-call examples; retain revisions, supplements and committee/Council duplicate considerations. Extend a complete inventory across terms after the bounded mechanism passes its coverage checks.

Measure **newly covered, correctly identified solicitations**, source-reviewed bidder/price relations, explicit incomplete disclosures and refusals—not just names, files or API success. Run the initial historical backlog separately from the bounded incremental nightly; honor pacing, Retry-After, resumable state and the existing model budget. The investigation spent zero model credits; a production pilot should estimate uncached tokens before submitting any batch.
