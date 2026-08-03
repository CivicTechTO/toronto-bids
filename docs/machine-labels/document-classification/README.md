# Document classification — machine labels (2026-08-01)

**These are machine-generated labels, not ground truth.** They live outside
`docs/ground-truth/` deliberately and must never be pooled with
`docs/ground-truth/document-classification/labels-alex.json` — that file is the calibration
target this pipeline was measured against, not a peer dataset. Where the two disagree, the
human file is right until shown otherwise.

## What this is

Stage-one classification (13-class `kind` + binary `contains_bid_or_award`) applied to the
**5,750 documents** in `background_pdf` that were not part of the 125-document human sample —
the whole rest of the archive that the classifier will eventually gate. Built with `Agent`/
`Workflow` subagents reading full document text (not the 2,500-char header the earlier LLM
API tests used), because full-text access is exactly what a subagent can do that a bare API
call cannot.

## Method

1. **Calibration** (`build_calib_batches.py`, `score_calibration.py`) — 3 models
   (haiku, sonnet, fable) x 2 independent blind passes over all 125 human-labelled documents
   plus 6 unlabelled "probe" documents (the largest in the corpus, to stress the reading
   protocol on a regime with zero human labels). 66 agents, 17 minutes,
   `wf_86b439f2-e85`.
2. **Model choice: haiku.** Two-pass union caught every procurement award and every
   flag-positive in the human set (proc-recall 22/22 across the union of both passes,
   flag-recall 25/25) at a small fraction of sonnet/fable's per-token cost — and cost is not
   incidental here, it multiplies by the number of passes and the corpus size. Kind accuracy
   was comparable across all three models (haiku 58-62%, sonnet 57-58%, fable 59-66%) and
   noisy pass-to-pass for all of them; none is a confident single-vote classifier, which is
   why the corpus run is two independent passes plus a judge, not one.
3. **Corpus labelling** — every one of the 5,750 documents read TWICE, independently, by
   separate haiku subagents (980 agents, 4.0 hours, `wf_7340bd0f-d86`). Batches are
   greedily size-balanced (a batch's cost is bounded by its total character count, capped per
   document, so one 8M-char outlier can't starve a batch of attention) and independently
   shuffled per pass, so a bad batchmate in pass 1 cannot also poison pass 2.
4. **Honeypots** — 120 of the 125 human-labelled documents were copied under fresh opaque
   corpus ids and silently mixed into the real run — indistinguishable from real corpus
   documents to the labelling agents. This is what lets the *deliverable* (after judging, not
   just a raw vote) be scored end-to-end against known answers, rather than trusting
   calibration numbers to generalize.
5. **Judging** — 820 of 5,750 documents where the two passes disagreed on kind or flag went to
   a third, independent, blind fable vote (`wf_c697e182-405`, 69 agents + 1 patch batch for
   3 documents that came back short a vote, 15 minutes). The judge never saw the two prior
   votes — an anchored judge would just inherit whichever wrong answer came first.

## Results

**Honeypots, end-to-end (n=120, the number that matters):**

| | kind accuracy | flag accuracy | flag recall | flag precision |
|---|---|---|---|---|
| this pipeline | 63% | 94% | 23/24 (96%) | 23/29 (79%) |

Kind accuracy tracks calibration closely (63% here vs ~60% in calibration) and is the weaker
number by design — see "the flag governs, not kind" below.

**One honeypot flag miss**, and it's informative rather than random: `C088`, a Vendor-of-Record
**extension of an existing arrangement** ("Extension of Contract No. 10034756"), was called
`procurement_other` / flag=false by *both* independent passes, unanimously, so it never reached
a judge. Whether an extension of an already-awarded VOR arrangement should trip the flag is a
genuine schema question, not obviously a model error — worth Alex's call on whether the flag
instructions should say so explicitly.

## The flag uses OR-of-votes, not majority — and that's a deliberate design decision, not a bug

The first assembly used majority vote for both `kind` and the binary flag. Measured against the
votes actually collected, majority (and its 1-vote-short-of-majority tie handling) left
**57 of the 5,750 corpus documents** on the wrong side of the flag despite at least one reader
catching real award language. **27 of those 57 had a vote of `kind=procurement_award`
specifically**, several with a named supplier and a dollar figure that the other one or two
readers missed entirely —

> "Award to Carollo Engineers Canada Ltd./EXP Services Inc. $2,765,968.25; three suppliers bid"
> "GEC Architecture o/a GEC Architecture Inc. as recommended supplier with award value
> $2,017,106.90 for design services"
> "Award Information form: Viola Management Inc. awarded $4,156,172.30 for Local Roads
> Resurfacing in Toronto, RFT, six bids received, multiple suppliers listed with bid prices"
> "the Board approved the Energy Retrofit Project to be awarded to Ecosystems Energy Services
> Inc. ... The cost of the preferred proposed Detailed Concept Design is $5,576,000"

— each outvoted by two readers who called it something else. This is exactly the cost asymmetry
this session's earlier binary-classifier exploration named for the same flag (uncommitted
scratchpad script, `bow_binary.py`), carried over here: a false positive on the flag costs one
wasted extraction call that finds nothing; a false negative means the document is never
extracted and **nothing downstream ever notices**.
`assemble_labels.py`'s `decide()` now takes the flag as **any vote true → true** rather than
majority. This recovered those 57 documents (flag count 1,238 -> 1,295) at a measured honeypot
cost of 2 additional false positives (23/27 -> 23/29 precision) and zero recall change in the
honeypot sample (the one honeypot miss is a unanimous 0-of-2 case that no aggregation rule over
these particular votes can recover). The other 30 of the 57 had no `procurement_award` vote at
all, and on inspection most are the flag doing exactly what it's meant to independent of kind —
a `correspondence` email about a "Construction Tender Award", `minutes` recording a tender
awarded in passing, a `procurement_other` RFP explicitly "awarded to Tyler Technologies Inc" —
real award content, correctly filed under a different kind. **`kind` stays majority-with-null-on-tie** — a wrong kind is mostly harmless (most classes
mean "extract nothing" either way) and a guessed kind is worse than an honest null, so there's
no equivalent asymmetry pushing it toward OR.

**The flag governs, not kind.** In the two-stage design, `contains_bid_or_award` is what gates
extraction; `kind` is bookkeeping, and the two are voted independently per reader. Of the 136
documents where `kind` stayed disputed (null, three-way vote split with no majority), 25 had at
least one `procurement_award` vote among them — and every one of those 25 is flagged true under
the OR rule and will still reach extraction, regardless of its unresolved `kind`.

## Corpus-wide numbers

- **5,750 documents.** `kind`: unanimous 4,977 (87%), judged-to-majority 637 (11%), disputed/
  null 136 (2%). Flag: 1,295 documents (23%) carry `contains_bid_or_award=true`.
- `kind` distribution: `governance_finance` 1,313, `procurement_award` 1,027, `status_update`
  1,000, `attachment_or_map` 884, `agreement_or_mou` 302, `correspondence` 276, `minutes` 266,
  `permit_regulatory` 257, `land_property` 173, disputed 136, `procurement_other` 80,
  `empty_or_unreadable` 24, `meeting_package` 8, `agenda` 4.
- **1,027 documents machine-classified `procurement_award`** is a real number about the corpus,
  not an artifact of stratified training-sample proportions the way the SVM's 18.5%
  corpus-wide prediction was flagged as being (see the pending 200-document human verification
  of that SVM sample, `verify_public.json` in the session scratchpad — still Alex's to label,
  and now a natural cross-check against these machine labels on the same documents).

## Cost

~93M subagent tokens, 1,116 agents, ~4.6 hours wall clock across the three workflow runs
(calibration 6.35M tokens / 66 agents / 17 min; corpus passes 80.0M tokens / 980 agents / 4.0h;
judging 6.67M tokens / 70 agents / 15 min). All haiku or fable — no OpenRouter spend.

## Files

| file | what it is |
|---|---|
| `labels-machine.json` | the deliverable — 5,750 documents, one entry per document: `kind`, `kind_agreement` (unanimous/judged/disputed), `contains_bid_or_award`, `flag_agreement` (unanimous/or_recovered/judged), per-vote detail, evidence quote |
| `export_snapshot.py` | one-time, read-only snapshot of `background_pdf` into flat files under opaque ids — the only script that touches the production DB |
| `build_calib_batches.py` | calibration batch/manifest builder (3 models x 2 passes x 11 batches) |
| `score_calibration.py` | scores calibration votes against `docs/ground-truth/document-classification/labels-alex.json`, surfaces unanimous machine-vs-human disagreements as candidate label errors |
| `build_corpus_batches.py` | corpus batch/manifest builder, including honeypot injection |
| `score_corpus.py` | joins the two corpus passes, builds patch (under-voted) and judge (disagreement) work lists |
| `assemble_labels.py` | final decision logic (majority-with-null for kind, OR for flag) and honeypot end-to-end scoring |

The three `Workflow` scripts themselves (`wf_86b439f2-e85`, `wf_7340bd0f-d86`,
`wf_c697e182-405`) are not checked in — they're saved under this machine's
`~/.claude/projects/.../workflows/scripts/` and are reproducible from the prompts embedded in
this README plus the `.py` files above.

## Limits

- **Kind accuracy (~60-65%) is unresolved and matches calibration** — it was never the target
  of this run. The disputed-136 and judged-637 buckets are exactly where a real kind refinement
  effort should start, if one is wanted; per the flag results above, it wouldn't change what
  gets extracted, only how the archive would describe non-procurement documents.
  `docs/ground-truth/document-classification/README.md`'s confusion-matrix findings
  (`governance_finance` <-> `status_update` being a fuzzy, low-stakes boundary) hold here too —
  it dominates the disputed and judged buckets in this run as well.
- **Honeypot n=120 is still a small sample** for a hard 95%+ recall claim — one miss moves the
  headline number by 4 points. Treat "23/24 flag recall" as consistent with calibration, not as
  independently precise.
- **Not re-validated against the SVM.** The pending 200-document human verification of the SVM's
  predictions (session scratchpad `verify_public.json`) now doubles as an independent check on
  these machine labels for the same 200 documents, once labelled.
