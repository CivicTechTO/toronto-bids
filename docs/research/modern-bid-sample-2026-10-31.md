# Modern missing-bid source availability — 2026-10-31

A systematic committee-report discovery route has measurable headroom. In a deliberately high-value sample, **seven of eight missing procurements have official award reports that actually name submitters**. All seven report URLs are absent from the production `background_pdf` inventory. This is a discovery failure for those cases, not an extraction failure. It is not evidence that seven-eighths of the entire missing population is recoverable.

## Reproducible selection and limits

Used the read-only post-recovery snapshot at `.validation-systematic-bids-2026-10-31/production.sqlite` and `toronto_bids.bid_coverage.audit`, which counts direct document identities and unambiguous council-reference links. There are **513** missing documents with solicitation issue years 2020 onward.

Selected 16 documents in four strata. For each high-value stratum, take the four largest positive, eligible parsed OData award lines; for each small-value stratum, take the first four by SHA-256 of `modern-source-2026-10-31:<document_number>`. High value means a maximum eligible line of at least $5M; small means below $500K. These are ranking fields, not aggregated spending or validated contract totals. Proposal stratum means stored `RFP`/`NRFP`; RFT means stored `RFT`. The source feed labels are not perfectly semantic: the Humber report explicitly describes an nRFP despite the stored RFT label.

| Stored-type/value stratum | Missing population | Selected | Reports with named submissions located |
| --- | ---: | ---: | ---: |
| RFT, high | 23 | 4 | 4 |
| RFT, small | 41 | 4 | 0 |
| RFP/NRFP, high | 19 | 4 | 3 |
| RFP/NRFP, small | 106 | 4 | 1 |

The remaining modern documents belong to other type/value strata; this pass does not estimate their yield. Selection and complete ledger are in `.validation-systematic-bids-2026-10-31/modern/{select.py,sample.json,ledger.json}`. No production writes, paid model calls or large scans were performed. Eight official PDF GETs ran serially with at least 1.2 seconds between requests; each returned a PDF successfully, with hashes recorded in `captured-sources.json`.

Search used official `toronto.ca`/`secure.toronto.ca` results, trying bare and `Doc`-prefixed numbers, plus selected title fallbacks. Failure to find an indexed page is **not** a confidentiality, permanent-inaccessibility or no-bid finding. Joined tokens matter: the Fairbank report surfaced for `Doc4377242742`, while the bare-number query did not locate it in this bounded pass. Search-engine discovery is an opportunistic aid, not a complete or stable inventory.

## High-value source checks

| Document | Primary source and actual submission evidence | Consequence |
| --- | --- | --- |
| 2736063782 | [Ashbridges report, PDF p6](https://www.toronto.ca/legdocs/mmis/2021/ie/bgrd/backgroundfile-168619.pdf#page=6): two named tender bidders, each with an excluding-HST bid price. | Capture the two tender bids; four firms listed immediately before them are prequalified under a different call. |
| 3166773834 | [Watermains report, p7](https://www.toronto.ca/legdocs/mmis/2022/ie/bgrd/backgroundfile-225885.pdf#page=7): three named firms responded. | Named bids are available without published individual prices. Earlier contracts discussed in the report remain separate. |
| 4377242742 | [Revised Fairbank report, p11](https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-248701.pdf#page=11): six priced bids for the west contract, three for the east contract. | Preserve the two lots/contracts and nine price observations; six distinct firms overlap between lists. The consultant purchase-order amendment is separate. |
| 3987477359 | [Humber report, p4](https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-246668.pdf#page=4): five submissions declared, four firms named after an unnamed non-compliant submission was removed. | Capture supported names with visible partial coverage; do not invent the fifth firm or assign the later unnamed technical failure to a guessed supplier. |
| 4510705018 | [Western North York report, p5](https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-253175.pdf#page=5): four firms named as having submitted. | Names are in the report, not merely inferred from the eventual award recipient. |
| 4502108451 | [Paramedic station report, p5](https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-253128.pdf#page=5): two named submissions. | A blanket emergency/genuine-zero interpretation for this target would be wrong. |
| 2913757955 | [Black Creek report, p7](https://www.toronto.ca/legdocs/mmis/2021/ie/bgrd/backgroundfile-173745.pdf#page=7): six named submissions. | Preserve the EXP/Arcadis joint venture as one submission, rather than splitting it into two bidders. |
| 4241423110 | No relevant award report located in this bounded search. | Source status remains unknown; not a zero-bid finding. |

The [official September 2024 committee item](https://secure.toronto.ca/council/agenda-item.do?item=2024.GG16.7) identifies original and revised Fairbank reports separately. Discovery must retain revision history and select the applicable source instead of treating every PDF or attachment as another procurement.

## Smaller-value source checks

The one located report is [Doc3940063088, AV-service categories](https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-238776.pdf#page=5). It declares four proposals and publishes named category lists with repeated firms and a Solotech spelling variation on following pages. This PDF and a current cached extraction are already held, so this case belongs to attribution/cache replay rather than discovering new reports.

No relevant award/bid report was located for the other seven small-value targets in the bounded official-site search: **3885238177, 3921046980, 2982722772, 2729988932, 3261875502, 3324102272, 3763094007**. An incidental project-list mention for 3921046980 does not prove a submission list and was excluded. These seven remain unassessed for actual source disclosure; neither the award value nor the search outcome establishes a no-bid fact.

## Implications for a systematic build

1. Enumerate the complete committee/City Council report inventory, including items adopted without recorded votes; the recorded-vote CSV cannot be the sole discovery index. Seven sampled official reports omitted from storage are a concrete test set for that discovery route.
2. Separate report discovery from contract identity. Collect candidate PDFs and explicit source identifiers first, then establish which contract/list each observation belongs to.
3. Retain partial disclosure: Humber names four of five submitters. A single binary complete/incomplete flag or refusal of all rows would discard useful supported evidence.
4. Preserve lot, stage, joint-venture and revision structure. A one-price-per-supplier model would be wrong for Fairbank; treating prequalified firms as tender bidders would be wrong for Ashbridges.
5. Benchmark the candidate discovery route on these seven positives and the bounded-search negatives before claiming a recovery percentage or scheduling a full backfill. The high-value sample is intentionally enriched for committee-handled procurements; it cannot justify a whole-population forecast.
