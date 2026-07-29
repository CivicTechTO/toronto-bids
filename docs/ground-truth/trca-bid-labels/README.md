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
| `labels-alex.json` | Alex's labels, 10 documents, 95 bid rows, plus a `corrections` block |
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

- `D01` — `2220742 Ontario Limited` → `Ltd` (only that form appears in the document)
- `D07` — `Wood Environment &` → `Wood Environmental &` (likewise)
- `D03` — added 3 bids for RFP 10009033 that were marked absent. This is the 402-page package,
  and it embeds the *same report* that appears standalone as `D07`, which was labelled correctly.

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

- One labeller, 10 documents, 89 companies. Small, and single-rater until Gabe's set lands.
- Precision against these labels is a weak measure for the 8 excerpted documents: a parser row
  drawn from a section not shown to the labeller counts as unmatched but may be perfectly real.
  Recall is the sound number here, since the parser had access to the full document and the
  labeller only to part of it.
- `D09` is a Vendor of Record arrangement with multiple winners per service category
  (Roofing / Painting / Stucco), which is why it carries six `won` rows. That is correct.
