# TRCA bid records — human ground truth (2026-07-29)

Human labels for 15 TRCA board-report excerpts, produced to answer a question every prior
measurement in this repo had to beg: **how good is the incumbent parser actually?**

Until now every evaluation used a parser's own output as ground truth, so that parser scored
100% by construction and anything disagreeing with it scored as wrong. These labels break that
circularity.

## Files

| file | what it is |
|---|---|
| `documents.json` | the 15 excerpts as shown to labellers, with `meta` (page count, whether excerpted) |
| `labels-alex.json` | Alex's labels — 10 documents (D01–D10), 98 bid rows, plus a `corrections` block |
| `labels-gabe.json` | Gabe's labels — 10 documents (D06–D15), 69 bid rows |
| `machine-proposals.json` | which names the incumbent parser vs the LLM proposed — **withheld from labellers** |
| `labelling-tool.template.html` | the labelling UI (`__DATA__` is replaced with `documents.json`) |

## Method

- **From scratch, not classification.** Labellers were shown the text and asked to list every
  company that submitted a bid, its amount, and whether it won. They were *not* shown candidate
  names. Pre-populating candidates would have baked in the union of the parser's and the model's
  assumptions, making any bidder they both missed invisible.
- **Blind.** No indication of which names any machine had proposed.
- **Per-contract attribution.** Each row records which contract it belongs to. Added after Alex
  hit a document covering two contracts on the second excerpt — this turned out to be the whole
  story (see below).
- **Overlap by design.** 15 documents split 10/10, with 5 shared between two labellers, so
  inter-rater agreement can bound how much confidence the labels themselves deserve.
  **Only Alex's set is complete so far**; Gabe's is outstanding, so no agreement figure yet.
- **7 documents show full text; 8 are 109–402 page meeting packages** where the sections
  containing bid lists are shown with every omitted stretch marked in place.

## Headline result

Incumbent `trca_board` parser, scored against these labels (legal-suffix-insensitive matching):

| | |
|---|---|
| recall | **59%** (54 of 92) |
| precision | **93%** (3 real errors + 1 name-variant matching failure of mine) |
| single-contract documents | **96%** (25/26) |
| **multi-contract documents** | **44%** (29/66) |

**The parser's failure is structural, not transcriptional.** It reads a bid list correctly
whenever there is one to read, and loses more than half the record whenever a document holds
several contracts — it anchors on `received from the following`, takes that list, and stops.
D09 holds four contracts and yields 25%.

This retires the #203 conclusion that TRCA should keep its regex because cells could not be
attributed to a solicitation. The regex has the same attribution failure; it was invisible
because the regex *was* the ground truth.

## Corrections applied to the labels

Applied by Claude with Alex's explicit permission, under one rule: **only changes the source
text unambiguously decides.** Recorded in the `corrections` block of `labels-alex.json`.

- `D07` — `Wood Environment &` → `Wood Environmental &` (likewise)
- `D03` — added 3 bids for RFP 10009033 that were marked absent. This is the 402-page package,
  and it embeds the *same report* that appears standalone as `D07`, which was labelled correctly.
- `D01` — filled in contract `10040951`, stated in the report's award clause.
- `D10` — added the 3 disqualified tenderers (see the bid definition below).

**Deliberately not changed:**

- `D08` `Airborne Imaging` / `D09` `Action Buildworks` / `D10` `MultiTech.` — **both** forms
  appear verbatim in those documents, so the labeller's choice is defensible. An earlier
  analysis wrongly called these transcription errors.
- A `D01` name change to `Ltd` was **reverted**: the results table (the bid record) says
  `Limited`, and only the pre-qualification bullet list says `Ltd`. The labeller's original was
  verbatim from the right place.

## The bid definition, resolved by the documents

Both D01 and D10 had three companies the labeller excluded, and it looked like one definitional
question. Reading the reports showed they are **opposite cases**:

- **D01 is a two-stage procurement.** Six firms made *pre-qualification* submissions; the report
  then says tender documents "were issued to the following three (3) Proponent(s)". Buildscapes,
  Pine Valley and Shoreline never received tender documents and never bid. **Excluding them is
  correct; the parser counting them is an error.**
- **D10 had eight tenders submitted**, three of which "were disqualified because of the
  incomplete submission package". Those three *did* bid. **They belong in the record with no
  valid price**, matching the archive's existing `Non-Compliant` convention (#94).

**Rule: a company is a bidder if it SUBMITTED A BID for that contract.** Pre-qualified then
eliminated = no. Submitted then disqualified = yes. Both are stated explicitly by the documents,
so this is not a matter of taste.

The parser's 3 confirmed precision errors are all the same class: taking a pre-qualification
bullet list as the bid list.

## Caveats

- One labeller, 10 documents, 92 companies. Small, and single-rater until Gabe's set lands.
- Precision against these labels is a weak measure for the 8 excerpted documents: a parser row
  drawn from a section not shown to the labeller counts as unmatched but may be perfectly real.
  Recall is the sound number here, since the parser had access to the full document and the
  labeller only to part of it.
- `D09` is a Vendor of Record arrangement with multiple winners per service category
  (Roofing / Painting / Stucco), which is why it carries six `won` rows. That is correct.

## Inter-rater agreement (both labellers complete, 2026-07-30)

Gabe's set arrived (10 documents, 69 rows). Five documents — D06–D10 — were labelled
independently by both, which is what bounds how much confidence these labels deserve.

Raw company-set agreement across the five is 69% (Jaccard), which looks poor until it is broken
down **per contract**:

| | |
|---|---|
| contracts **both labellers read** | **42 of 44 companies agreed — 95%** |
| companies in contracts **only one labeller reached** | **13** |

The two remaining disagreements are name variants (`MultiTech.` vs `MultiTech Trades
Corporation.`, `DJ McRae` vs `DJ McRae Contractors Ltd.`) — both forms appear verbatim in the
documents, so neither is wrong.

**Every substantive disagreement is contract COVERAGE, not reading.** On D06 Gabe found 1 of the
3 contracts; on D09, 1 of 4. Both are the large meeting packages.

### What this means

1. **The labels are trustworthy where they overlap.** Two people reading the same bid list agree
   ~95–100%. Extraction from a bid list is not the ambiguous part of this task.
2. **Finding all the contracts is the hard part — for humans as well as software.** Gabe stopped
   at the first bid list in exactly the documents where the incumbent parser stops at the first
   bid list. The failure is a property of the documents, not of regex.
3. **Ground truth on multi-contract packages should be assumed INCOMPLETE, including Alex's.**
   Two independent readers both under-covered; there is no basis for assuming a third would not.
   Recall figures measured against these labels are therefore an **upper bound on the labels**,
   not on the documents.
4. An independent corroboration of one correction: Alex's D10 originally omitted the three
   disqualified tenderers, which were added by adjudication from the report's own wording. Gabe
   included all three unprompted.

### Consequence for the model scores above

The models were scored against Alex's labels and reached 100% precision, meaning they proposed
nothing Alex did not have — so on these documents they matched Alex's *coverage* rather than
exceeding it. Whether any extractor finds contracts that BOTH humans missed is not yet measured,
and cannot be measured from these labels alone.

`labels-gabe.json` was recovered as a raw localStorage dump via devtools: the export UI failed in
his browser even after a hard reload, for reasons still unknown (it worked for the other
labeller on the same published page).

## Models scored against these labels (2026-07-29)

The first non-circular comparison in this evaluation: each model was given **exactly the text
the labeller saw** (`documents.json`), so neither side had access the other lacked. Scored
against Alex's set, legal-suffix-insensitive name matching.

| | recall | precision | single-contract | **multi-contract** | est. full backlog |
|---|---|---|---|---|---|
| incumbent parser | 59% | 93% | 96% | **44%** | free |
| `openai/gpt-5.6-luna` | 100% | 100% | 100% | **100%** | **~$4** |
| `openai/gpt-5.6-terra` | 99% | 99% | 96% | 100% | ~$10 |
| `openai/gpt-5.6-sol` | 100% | 100% | 100% | **100%** | ~$56 |
| `anthropic/claude-opus-5` | 100% | 100% | 100% | **100%** | ~$290 |

Whole exercise cost **$2.67**. The cheapest model on the published price/intelligence frontier is
as good as the most expensive one here; everything above Luna buys nothing this task uses.

**Method** (the script was not preserved; it is a short harness, ~150 lines): for each document,
POST the full shown text to OpenRouter with a JSON-schema-constrained response of
`{company, contract, amount}` records, `service_tier: "flex"` for OpenAI models, reasoning effort
low. Score company names against the labeller's with legal suffixes stripped and a 0.88
difflib ratio as the match threshold. Bucket documents by whether the labels contain one contract
or several.

The prompt carried the **pre-qualified-vs-disqualified rule** (below), which came from human
labelling, not from any model.

### Read these numbers carefully

- **100% precision means the models proposed nothing Alex did not have** — they matched his
  *coverage*, they did not exceed it. Given that two independent human readers both
  under-covered the multi-contract packages, this is not evidence that the models found
  everything in the documents.
- Nine documents, 92 companies, one labeller's set. Small.
- The first run of the harness reported `gpt-5.6-sol` at **22%**. That was a missing backoff in
  the retry loop — four instant retries under rate limiting, silently returning empty. Sol is
  100%. Treat every figure here as fragile until reproduced.

## Holdout: models on Gabe's 5 solo documents (D11–D15)

25 companies Alex never saw, so nothing here was tuned against. `score-models.py gabe D11-D15`.

| | recall | precision |
|---|---|---|
| incumbent parser | 68% | 81% |
| all four hosted models | **100%** | 52–56% |

**The low precision is the labels, not the models.** All 23 "false positives" are verbatim in the
source, and every one carries a **contract number Gabe never reached**. Two were traced to the
source text and are exact bid lists:

- D14 / RFP 10041763 — *"four (4) proposals were received from the following vendor(s): Ecoman
  Corporation, Northern Wildflowers Inc., Quality Seeds Ltd., St. Williams Nursery & Ecology
  Centre Inc."* — precisely the four the model returned.
- D15 / RFP 10020367 — a three-stage procurement: 8 pre-qualification submissions, 7 issued RFP
  documents, then *"Five (5) proposals were received"*. The model returned exactly those five and
  **excluded** Bronte and Dynex, who pre-qualified and received documents but never proposed.
  That is the pre-qualified-vs-submitted rule applied correctly to a case it had never seen.

D14 is the sharpest illustration: Gabe labelled one contract; the document holds four.

### This answers a question the earlier round could not

Against Alex's set the models scored 100% precision, meaning they matched his coverage without
exceeding it — so nothing could be concluded about bids *both* humans missed. Here the models
recovered **23 real bids that no human recorded**. On multi-contract packages they outperform
both the incumbent parser and the human labellers.

### And it further weakens the labels as a gold standard

On D12 Gabe recorded `Airborne Imagine Inc.` and `Airborne Imaging Inc.` as two separate
companies — the City's own typo, listed twice — and omitted `Aeroquest Mapcon Inc.` entirely. He
labelled the same report correctly as D08. An intra-rater inconsistency on identical text.

**Practical consequence:** precision measured against these labels is not a measure of extractor
correctness. Only recall is sound, and only adjudication against the source settles a
disagreement.

## Model selection (2026-07-31)

**Best free: `nvidia/nemotron-3-ultra-550b-a55b:free`.
Best paid: `openai/gpt-5.6-luna`.**

Full ladder, scored against Alex's set (10 documents, 92 companies), `reasoning: effort=low`:

| model | AA-LCR | recall | precision | single-contract | **multi-contract** | backlog cost |
|---|---|---|---|---|---|---|
| incumbent regex parser | — | 59% | 93% | 96% | **44%** | free |
| **`nemotron-3-ultra:free`** | 0.67 | **99%** | 99% | 96% | **100%** | **$0** |
| `tencent/hy3-preview` | ? | 99% | 99% | 96% | 100% | ~$2 |
| `deepseek-v4-flash` | 0.657 | **74%** | 99% | 96% | **65%** | ~$4 |
| **`openai/gpt-5.6-luna`** | 0.69 | **100%** | 100% | 100% | **100%** | **~$4** |
| `openai/gpt-5.6-terra` | — | 99% | 99% | 96% | 100% | ~$10 |
| `openai/gpt-5.6-sol` | — | 100% | 100% | 100% | 100% | ~$56 |
| `moonshotai/kimi-k3` | — | 100% | 100% | 100% | 100% | ~$86 |
| `anthropic/claude-opus-5` | — | 100% | 100% | 100% | 100% | ~$290 |

Every model beats the incumbent parser, and the entire spread from free to $290 is 99–100%
except DeepSeek. **Price buys nothing on this task above the free tier.**

### Why these two

**`nemotron-3-ultra:free` — the free choice.** 99%/99%, $0, 1M context. Its limits are
operational, not qualitative:

- **No structured-output support at all** (`structured_outputs: false`, `response_format: false`).
  It produces JSON by following instructions, not by constrained decoding — the exact failure
  mode that made Qwen3-1.7B unusable. A 550B model complies far more reliably, but nothing
  enforces it, so output must be validated and a document refused rather than partially stored.
- **20 requests/minute (fixed) and 1,000/day.** Fine for the nightly (2–10 documents). The TRCA
  backlog of 3,411 documents takes ~4 days and consumes the whole daily free allowance.
- **Free endpoints rotate out without notice.** Building the only extraction path on one invites
  a silent outage.

**`openai/gpt-5.6-luna` — the paid choice.** 100%/100%, enforced JSON schema, `service_tier:
flex` verified applying (50% off, confirmed by arithmetic on the returned per-token cost), and
**~$4 for the entire 6,163-document backlog** with ~$1/month steady state.

**Recommended shape: Nemotron as default, Luna as automatic fallback** on rate-limit or
unavailability. They are within 1 point of each other, so the fallback costs pennies and removes
a single point of failure.

### What the intelligence index and AA-LCR each got right

The Artificial Analysis **Intelligence Index does not predict this task**. It spans 37.8→60.7
across the models above and yields a flat 99–100% line with one outlier — and the outlier is not
the cheapest model. The arithmetic reason: long-context reasoning is **6.25%** of that index and
instruction-following another 6.25%; this task is essentially only those two.

**AA-LCR does order our one discriminating pair correctly** — but only after a bug in the
frontier computation was fixed. The original front scored each model's best *variant* (often
"max effort") while pricing it at base rates; correcting for reasoning-token cost moved
Nemotron to 0.67, above DeepSeek's 0.657, matching the measured ordering. Before the fix it
appeared inverted.

**This is not yet a validated proxy.** One discriminating pair is not an ordering; `tencent/
hy3-preview` scored 99% and its AA-LCR is unknown, which would falsify the correlation if it is
below 0.657. Confirming it would require deliberately testing models AA-LCR predicts will fail.
See `docs/protocols/model-selection-by-measured-ordering.md`.

### Caveats on the table

- The prompt gained an explicit "reply with JSON" line partway through, to satisfy DeepSeek's
  `json_object` requirement and Tencent's disregard for response schemas. The earlier four models
  (Luna, Terra, Sol, Opus 5) were measured before that line existed; they used constrained
  decoding, which forced JSON regardless, so it should not move them — but the table is not
  perfectly like-for-like.
- Run-to-run variance is real: Nemotron scored 100%/100% once and 99%/99% on a repeat, same
  prompt and documents. Differences of 1–2 points are not meaningful.
- Benchmark scores are reported at `(high)` / `(Reasoning)` variants; we measured at
  `effort: low`.
- Three models needed provider-specific handling to work at all — DeepSeek requires the literal
  word "json", Tencent ignores response schemas and returns markdown, and both looked like 0%
  capability until diagnosed. Any production path needs that fallback logic or it will score a
  capable model as useless.
