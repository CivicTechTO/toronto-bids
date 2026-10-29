# Waterfront CC6567 recipient recovery — 2026-10-29

The current [awarded-contracts publication](https://www.waterfrontoronto.ca/opportunities/procurement/awarded-contracts) still lists the March 2023 CC6567 dewatering work at $313,750 without a recipient. Its attribution identifies Ellis Don as awarding construction manager. The cached model result incorrectly makes that manager the recipient; the literal verifier correctly retained the other four claims and rejected this one.

The same publisher's [May 29, 2023 Finance, Audit and Risk Management Committee book](https://www.waterfrontoronto.ca/sites/default/files/2023-05/Meeting%20Book%20-%20Finance%2C%20Audit%20%26%20Risk%20Management%20Committee%20-%20May%2029%2C%202023_0.pdf) identifies **WJ Groundwater** against the CC6567 rebuild dewatering work, package **TPBR182**, **CM#2023-03**, in the March 2023 PLFP contingency tracker on PDF page 27. The downloaded full PDF SHA-256 is `d0dfeab3bbc5a064eeefd495235227cacd344ad4371132c6bec3009770700e01`. Its rendered page and extracted text were checked together. The committed single-page excerpt has SHA-256 `fb83fbb66377aa5b5064225f17c18a6e6db1dab389bbb3b1f7f8a884a5982221`.

The cross-source review supplies the recipient. The primary award publication supplies the unchanged $313,750 amount. The tracker separately labels an estimate, approved amount and contingency drawdown; none becomes a replacement award price or a tax-converted estimate. The primary publication's month remains publication precision; no award day, solicitation ID, corporate alias or bidder list is inferred.

## Binding and preservation

`data/publication_award_evidence.json` binds the exact primary block key/text/month, complete monthly extraction digest, model-result fingerprint, extraction version and exact contract/award positions to the reviewed recipient and independent publisher quote. Changed reviewed contexts fail before deriving a replacement month. Other rows keep literal recipient/amount verification; an awarding-agent annotation alone still supplies no recipient.

The raw cache is unchanged. `published_award.evidence_json` records the source proof and JSON exports parse it as an object. The frontend companion links the recipient source to PDF page 27 while retaining the primary publication and its original text. The former March 2023 entry is removed from the active known-gap registry, so a future regression is a failure.

## Validation

All 1,398 backend tests passed, including the real cache result, actual PDF quote, stable claim identity, immutable raw results, context/version changes, original manager rejection and source-proof export. A fresh production-copy rehearsal grew published claims from 508 to 509, retained every earlier claim and source observation, preserved all other procurement rows and raw caches, and passed foreign-key checks. March 2023 verification becomes `ok` without model requests.

A full frontend build from that corrected export displayed WJ Groundwater, $313,750, the original publication link and the page-27 corroborating link. Data-independent buyer-page tests passed against that full build. The supplementary review is local evidence; nightly still fetches only the original award page. No paid extraction or prompt/cache-version change was made. Production admission follows deployment of the reviewed backend change.

Operational fetch, PDF, rendered page, immutable cache snapshot and rehearsal evidence remain under `.validation-agency-recovery/2026-10-29/` on the archive host.
