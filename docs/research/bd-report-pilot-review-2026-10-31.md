# Historical BD extraction pilot — 2026-10-31

Eight reports (two per year, 2013–2016) were selected before extraction, with frozen PDF hashes and independently read Call Summary expectations in [the source inventory](bd-report-pilot-2026-10-31.json). Each inventory entry links to the City's original PDF. The same shared prompt/version and Sonnet/high request builder were used; no source-specific prompt or whole-cache invalidation was introduced.

All eight batch requests completed without transport, structural-validation or billing errors. The maximum reservation was USD 1.503810; reported batch usage settled to **USD 0.060809**. The requests and receipts are retained in the deployment's shared `llm-usage.sqlite`. An initial worktree ledger-location mistake was corrected by atomically migrating the existing submission, jobs, documents and remapped reservations into that ledger. No request was resent.

## Source review and disposition

| Council item | Source finding / returned result | Admission |
| --- | --- | --- |
| 2013.BD115.4 | Source has eight priced tenders and five Informal submissions. Model retained all 13 table entries, but appended “Informal” to five supplier names and incorrectly marked those entries `no_bid`. It also assigned compliance to formal bidders without individual source statements. | Entire report held; no contaminated supplier identities or no-bid claims admitted. |
| 2013.BD136.9 | All six named proponents match the source, including the printed `Auxilllium` spelling. Losing proposal prices are undisclosed. | Six participation observations, no invented prices. |
| 2014.BD198.1 | Both firms match. Better Deal is non-compliant; GFS has a base price and an alternative including 30% miscellaneous. | Two observations; qualified price and both extracted statuses retained only in raw cache. |
| 2014.BD209.1 | Both named proponents match; both passed the technical threshold. Losing price is not disclosed. | Two observations; both statuses remain raw-only under the conservative initial status policy. |
| 2015.BD6.3 | Model returned all 24 table cells in one contract. Part A/B/C identities are absent from the shared schema; bidders have different prices, non-compliance and no-bid outcomes across parts. | Entire report held; no flattening of lot outcomes. |
| 2015.BD44.16 | All three model supplier names are supported by the source. Layout text interleaves prices with wrapped names, so the literal contiguous-name gate cannot prove them. Bid totals also carry an explicit district/service scope footnote. | Entire report held. This is an admission/layout limitation, not a claim that the model's supplier names are wrong. |
| 2016.BD84.12 | Three names and prices match; Stevens & Black is explicitly non-compliant. The model additionally labels other firms compliant. | Three observations and three prices; all three status claims remain raw-only. |
| 2016.BD115.8 | One named firm; price carries miscellaneous and prompt-payment qualifications. | One participation observation; qualified price and status remain raw-only. |

The private rehearsal admits **14 bidder observations across five council items**, with **three reports refused, two prices withheld and eight status claims withheld**. Raw model payloads and original PDFs remain unchanged. No award facts derive from this corpus.

The eight reports contain 54 returned bid-table observations, including the 24 multipart cells and five wrongly labelled informal entries. That count is not 54 validated bids or recovered procurements. The pilot shows that structurally valid JSON and literal dollar amounts are insufficient admission criteria.

## Coverage and limits

On a private production snapshot, the existing City coverage audit moves from 4,107 to 4,104 awarded solicitations without stored bids. The three changes use existing council-to-document links; the two other admitted council items remain separate identities. After the normal procurement resolver runs, the canonical audit agrees with the reference/document audit with zero attribution mismatches. **This is a rehearsal using existing links, not independent validation of historical Call Number encodings.** No new aliases or production bid/award facts were added.

The first rehearsal's preliminary derived rows were discarded only on the private pilot copy before applying the reviewed policy. Production and raw caches were untouched. The admission implementation preserves earlier supported source facts when a later report is refused, and restricts replacement to admitted item contexts.

Do not infer full-corpus precision or completeness from eight reports. The capture holds 586 PDFs: 568 text-bearing, 18 without readable text. The broader batch has not been submitted. Before rollout, merge the admission path and decide how to recover informal-role and layout-held participation, preserve lot identity, and qualify price/status fields without silently changing the shared prompt/cache contract. Existing cache results can be re-derived offline as admission improves; they do not need another paid extraction.
