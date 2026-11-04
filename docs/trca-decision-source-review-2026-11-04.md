# TRCA award decision review — November 4, 2026

The ten rows removed by #292 must not be restored unchanged. Three repeat a shared approved VOR ceiling as though allocated to each supplier; four repeat a shared proposed extension ceiling; two are future recommendations; one is an uncorroborated retrospective total. Genuine prior awards still derive from other reports. A report attached to a future meeting is not necessarily a future recommendation: document21022 is the actual October2 minutes.

This review used the original held PDFs/text and read-only SQLite snapshots. The source investigation changed no production rows or extraction caches and needed no model or network requests. The implementation below adds reviewed registry entries. Hashes of the four original PDFs were independently checked against the files held on disk. Findings describe the November4 source state, not the eventual outcome of the November6 meeting.

## Exact reconciliation of the ten removed rows

Comparison basis: the predeployment backup `before-292-20261104T175155Z.sqlite` versus the current production archive, using `(buyer_id,native_ref,supplier_name_raw,award_amount,source)` as the fact key. There are **12 raw award observations** in the four affected extraction results, but only **ten distinct derived rows disappeared**: the other two still derive independently from earlier reports.

| Removed old row | Contract | Supplier, verbatim | Cached amount | Classification / correct treatment |
| --- | --- | --- | --- | --- |
| 131952 | 10042138 | G & L Group Ltd. (O/A Draglam Waste) | $130,000 | October2 carried extension authorization; $130,000 is shared across the VOR. Retain named recipient with unknown individual amount, not $130,000. Such a NULL-amount fact already survives from20933. |
| 131953 | 10042138 | GFL Environmental Inc. | $130,000 | Same shared ceiling and existing NULL-amount recipient fact. |
| 131954 | 10042138 | Public Disposal and Recycling Inc. | $130,000 | Same shared ceiling and existing NULL-amount recipient fact. |
| 131957 | 10009966 | Morrison Hershfield Limited | $412,667.50 | Retrospective assertion in21037; independently held original decision establishes $299,007.50, not this amount. Keep the $412,667.50 claim/date withheld; do not substitute or invent a subsequent approval. Original $299,007.50 fact survives from4747. |
| 131958 | 10009966 | Stantec | $368,215.98 | Proposed November6 change order; no completed decision evidence in reviewed source set. Keep withheld. |
| 131959 | 10045642 | Doane Grant Thornton LLP | $171,595 | Proposed November6 audit award, including $30,410 Foundation share requiring separate approval. Keep withheld; supported submitted bids remain. |
| 131960 | 10042420 | Dynex Construction Inc. | $35,700,000 | Proposed November6 VOR extension, shared total across four vendors. Keep withheld; no individual allocation is published. |
| 131961 | 10042420 | Sartor Environmental Group Inc. | $35,700,000 | Same future shared extension. |
| 131962 | 10042420 | TMI Contracting & Equipment Rental Ltd. | $35,700,000 | Same future shared extension. |
| 131963 | 10042420 | Valefield Contracting Inc | $35,700,000 | Same future shared extension. Preserve raw spelling; do not silently equate punctuation variants for identity. |

The two other21022 observations are genuine carried awards and were **not lost**:

| Contract | Recipient and award | Independently derived source | Decision date supported by carried minutes |
| --- | --- | --- | --- |
| 10045025 | Hohol One Enterprises Limited, $279,451.00 plus applicable taxes; separate $55,890 contingency | 20869 (same bytes also indexed as20815) | 2026-09-18, Executive Committee B43/26 in20908; Board reconfirms October2 A139/26 in21022. |
| 10045514 | Dynex Construction Inc., $234,386.00 plus applicable taxes; separate $35,200 contingency | 20902 (same bytes also indexed as20848) | 2026-09-18, Executive Committee B44/26 in20908; Board reconfirms October2 A140/26 in21022. |

The three surviving10042138 recipient rows from20933 already carry `award_date=2026-10-02` and NULL individual amounts, consistent with the carried decision. No date correction is needed for those stored facts. Existing10045025/10045514 rows correctly retain September18 as the original carried Executive Committee decision; a later October2 confirmation should not overwrite that date.

## Carried decision evidence

### October2 minutes,21022

[Original public PDF](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=21022), SHA-256 `6cbe5f59a1204104b4f7fca5c62ffeb37dfd7eb30e5752d58fe8da76e64a35f5`.

The first page states: `Board of Directors Meeting #7/26 was held via videoconference, on October 2, 2026.` These are actual minutes with moved/seconded resolutions and Carried outcomes; their attachment to a November6 agenda does not turn them into November6 proposals.

Page10, item8.8, resolution A130/26 identifies `RFQ Contract No. 10042138 for Construction Waste Disposal` and names `G & L Group Ltd. (O/A Draglam Waste), GFL Environmental Inc., and Public Disposal and Recycling Inc.` The operative passage reads:

> THEREFORE LET IT BE RESOLVED THAT TRCA staff be authorized to exercise their option to extend the VOR arrangement from January 1, 2027 to December 31, 2027, at a total cost not exceeding $130,000, plus applicable taxes; to be expended as authorized by TRCA staff, subject to annual budget approvals;

The resolution ends `Carried`. This is one approved shared authorization ceiling. It neither allocates $130,000 to every named vendor nor proves executed expenditure. The source report20933 independently describes that same amount as `The estimated value of the one (1) year contract extension`.

Pages18–19, item10.2.2, resolution A139/26:

> THEREFORE LET IT BE RESOLVED THAT RFT No. 10045025 for the Sandalwood Park Erosion Control and Infrastructure Protection Project be awarded to Hohol One Enterprises Limited at a total cost not to exceed $279,451.00, plus applicable taxes, to be expended as authorized by TRCA staff;

A separate clause authorizes `$55,890.00` contingency. The resolution ends `Carried`.

Page20, item10.2.3, resolution A140/26:

> THEREFORE, LET IT BE RESOLVED THAT RFT No. 10045514 for the Loafer's Lake Park Erosion Control and Infrastructure Protection Project, City of Brampton be awarded to Dynex Construction Inc. at a total cost not to exceed $234,386.00, plus applicable taxes, to be expended as authorized by TRCA staff;

A separate clause authorizes `$35,200.00` contingency. The resolution ends `Carried`.

### September18 Executive Committee minutes,20908

[Original public PDF](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=20908), SHA-256 `a74291bcb9a078d0c12724997a9459a2d84fc2bbcf5a1dac4ac1589905d996f5`.

The first page identifies `Executive Committee Meeting #6/26` held `on September 18, 2026`. Items9.2/B43/26 and9.3/B44/26 contain the same operative10045025 and10045514 award clauses, amounts and recipients above, each followed by `Carried`. This supports the earlier decision dates independently of the staff recommendation reports20869 and20902.

### Original10009966 decision,4833

[Original public minutes](https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=4833), SHA-256 `ce28927dacadf3bb4a9f9cf3f6bb2cb51d88098cb92ef7666f8c8537efa0b461`.

The first page identifies Executive Committee Meeting #5/19 held `on Friday, June 7, 2019`. Resolution B67/19 states:

> THEREFORE LET IT BE RESOLVED THAT Request for Proposal (RFP) No.10009966 for Highland Creek Markham Branch (Corporate Drive) Flood Remediation Class Environmental Assessment be awarded to Morrison Hershfield Limited at a total cost not to exceed $299,007.50, plus applicable taxes, to be expended as authorized by TRCA staff;

Separate clauses authorize conditional `$68,150.50` expenditures if a ScheduleC EA is required and `$55,000` contingency. The motion ends `CARRIED`. Neither these amounts nor their sum yields $412,667.50. All held-text mentions of10009966/412,667.50 were checked: only21037 asserts the latter amount. Do not derive a changed award from that retrospective total or from arithmetic.

The original staff report [4747](https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=4747), SHA-256 `91021d66491275285792ff677ce5bea68e0777aad1ceaa1002b52f0506aa8def`, is dated June7,2019 and supports the same base amount, but4833 is the independent carried decision.21037 instead labels B67/19 as approved May29,2019; use the held original minutes rather than silently adopting this contradictory retrospective date. The existing $299,007.50 fact already has June7,2019.

## Future and mixed reports

[21030](https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=21030), SHA-256 `2b414f743a078d9b20b1b57018d603604669aac6b7ccb8b94523eac63b7dda3c`, is a November6 staff recommendation. It explicitly seeks an additional two-year shared VOR extension of $35,700,000, producing a proposed revised total $68,877,500. It recounts an earlier February21,2025 authorization of $33,177,500, but this is not independent evidence adopting the new extension. All four cached $35,700,000 per-vendor allocations remain unsupported even if a later meeting adopts the shared ceiling.

[21037](https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=21037), SHA-256 `91ed8f0c021b268fa78b5710364d52757a04a9d533ff091ff5d76214160160c2`, mixes prior history and future action. The recommendation says `a Change Order ... be awarded to Stantec ... at a total cost not to exceed $368,215.98, plus applicable taxes`. The historical WHEREAS mentions Morrison Hershfield $412,667.50, but the quoted B67/19 and independent original minutes establish the earlier $299,007.50 base. Its footer says November27,2026 even though its meeting heading is November6; neither date resolves the historical monetary conflict. Keep the uncorroborated monetary claim and future change order separate and withheld.

[21044](https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=21044), SHA-256 `370007633a3cee37c6551a3a6db15c3e35a09e4efb5c382b269b20a637ac3081`, is a November6 audit recommendation. The proposed $171,595 includes $30,410 for the separate Foundation, expressly requiring its own Board approval; TRCA/Village share is $141,185. The operative recommendation and financial details identify $14,118.50 contingency (10% of TRCA share), while a rationale paragraph says $17,159.50 (10% of combined total), which the cache selected. Do not resolve that conflict by arithmetic or publish the full proposal as an adopted TRCA-only award. The two actual proposals received remain supported participation facts.

## Original source and extraction bindings

All four affected extractions use `v1-b63aa8c835ae`. Fingerprints use the existing `agency_identity.contract_fingerprint` convention, not a replacement extraction payload.

| Doc | Original result SHA-256 | Contract count |
| --- | --- | ---: |
| 21022 | `0025295add97b99c1eb30dbfbb2bf32b4168c8e57f1ce6115f35c5699f1b2e7b` | 3 |
| 21030 | `0ae1de22a6c9011cd88e7c66865a3ca289c5c741ad857a871c7a907cda7531bd` | 1 |
| 21037 | `61180af4f34a64c129d995d5450d077f334eb362365d03f74af10afbb5399efb` | 1 |
| 21044 | `3daeef0be391e348f12989a481eb84fd848656430b95a894064e44eb9802a982` | 1 |

| Doc / contract index | Contract reference | Contract SHA-256 |
| --- | --- | --- |
| 21022 / 0 | RFQ No. 10042138 | `ad816ef0077526078615baaff6ad0c741b29f7f2575f267eab914efde3ff9210` |
| 21022 / 1 | RFT No. 10045025 | `dc8fbd2e1aa3bf873b383113563a765042d69e890a0c1a741b6bfb1f126cb450` |
| 21022 / 2 | RFT No. 10045514 | `fe087c20426d4fbb35bd449f49c2a37293cd934499c543b64066cf1fe700bbff` |
| 21030 / 0 | 10042420 | `ce0e8295678165ca495f1bea22212fafe4c456227fde68683ef27822fc48eecb` |
| 21037 / 0 | RFP No.10009966 | `de2be2b687bf74ecb911246eb86049db8a375cae6e2e4df1a595c68853e2c441` |
| 21044 / 0 | RFP No. 10045642 | `c6a1a69b85e1be4521009b4e5b82f76cea66ba4e9679e8ad5345aac23b9cf864` |

Additional source bindings:20933 SHA `6a3132aa39c1b50d066cbde7361f5882972e1212c1535b14bd60c2ef354f2975`;20869/20815 SHA `1c05a9508f4de7954e3563260264bd38a3ec8d407fad56e2b92e798edf35780b`;20902/20848 SHA `3f899e49ce6416050144114b68a9b35c71d6ed3ba2aff1d68e73661fa7b822d6`.

## Review implementation implications

A reviewed claim admission should bind the original report URL/hash, exact extraction result/version/contract position, individual award index and expected raw amount, plus the carried decision source/hash/date and literal passage. Release only the specific supported claim. Shared approved VOR caps can support named recipients with NULL individual amounts and `value_confidential=false`, retaining the cap in source evidence; they cannot support duplicated individual allocations. Prior accepted facts and dates should survive the new reviews without duplicate repricing or replacing September18 decisions with October2 confirmations. Future proposals and uncorroborated historical totals remain explicit withheld claims. Preserve raw cache JSON and original source context throughout.


## Participation finding and implementation

The earlier description of thirteen successful qualifiers in21063 was incorrect. The unchanged shared prompt defines legacy `prequalified` as firms with first-stage-only submissions. Its thirteen names are the unsuccessful applicants; the five finalists are correctly in `invited_to_tender`, and the three actual tenders are correctly in `bids`. The source table identifies eighteen RFPQ applicants in total. No prompt, model output or price change is needed.

The review names each role explicitly: `prequalification_submissions` (18), `qualified_for_tender` (5), `invited_to_tender` (5), `tender_submissions` (3). Canyon Avenue21060 separately has thirteen qualified/invited firms and six tenders; its original applicant pool is not stated and stays unknown. Source spellings remain literal, including Hawkins/Hawkings across tables; no alias is inferred. The nine actual tender rows receive `result_stage="Tender submissions"`. Audit proposals remain separate supported bid facts.

Every review binds the original PDF hash, extraction version, complete result digest, contract fingerprint and position. Each award is classified individually. Admitted claims additionally require a held decision URL/hash, literal passages and an explicit decision date. Runtime verifies cited text before any swap; missing/changed evidence requires review and keeps verified rows.

The original `agency_derivation.contract_json` and `extraction_cache` remain unchanged. `identity_evidence_json` records explicit participation roles, accepted award projections, withheld reasons and individual amounts withheld for shared limits. Coverage counts accepted recipients separately from withheld monetary claims. Reconciliation identifies unsupported prices/proposals as source withholding, rather than identity failures.

A stale board-derived awarded label clears only if the held/reviewed contract has no accepted award. Portal labels and independent awards survive. An exact reviewed unsupported observation can be withdrawn even if it was the only award; an otherwise empty derivation preserves unrelated history.

## Production-copy validation

The full production cache was replayed and exported without API calls. All 8,732 bid facts and 3,592 award facts retain their IDs, first-seen history, names, references, prices and award dates; all 3,098 raw extraction rows are unchanged. Only nine tender-stage labels and the unsupported10045642 board-derived awarded status change. All 839 TRCA award rows and 1,081 TRCA bid rows reconcile to the same identities. Production remains unchanged pending merge/deployment.
