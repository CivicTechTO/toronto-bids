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
