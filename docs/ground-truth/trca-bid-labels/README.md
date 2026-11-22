# TRCA bid records ground truth (2026-07-29)

Human labels for 15 TRCA board-report excerpts, created to measure the old `trca_board` regex parser and candidate LLMs against independent ground truth instead of comparing against the parser's own output.

## Files

| File | Contents |
|---|---|
| `documents.json` | 15 document excerpts shown to labellers, with page count and excerpt metadata |
| `labels-alex.json` | Alex's labels: 10 documents (D01-D10), 98 bid rows, plus a `corrections` log |
| `labels-gabe.json` | Gabe's labels: 10 documents (D06-D15), 69 bid rows |
| `machine-proposals.json` | Regex parser and LLM proposals (withheld from labellers) |
| `labelling-tool.template.html` | Labelling UI (`__DATA__` replaced with `documents.json`) |

## Method

- **Blind, from scratch:** Labellers read the text and listed every bidding company, its amount, and whether it won, without seeing parser or LLM suggestions.
- **Per-contract attribution:** Every row names its contract reference (added after encountering multi-contract documents).
- **Two labellers with 5-document overlap:** 15 documents split 10/10 with D06-D10 labelled independently by both Alex and Gabe to measure inter-rater agreement.
- **Excerpts:** 7 documents show full text; 8 are 109-402 page meeting packages showing the bid-list sections with omitted stretches marked.

## Bid definition

Reading D01 and D10 settled who counts as a bidder:

- **Pre-qualified but eliminated = not a bidder (D01):** Six firms pre-qualified, and tender documents were issued to three. The three eliminated firms never bid; counting them was a regex parser error.
- **Submitted then disqualified = bidder (D10):** Eight tenders were submitted and three were disqualified for incomplete packages. Those three belong in `bid` with `NULL` price and status `disqualified` / `Non-Compliant` (#94).

All three precision errors made by the old regex parser came from mistaking pre-qualification lists for bid lists.

## Label corrections

Applied to `labels-alex.json` with Alex's approval only where source text was unambiguous (logged in `corrections`):

- `D07`: `Wood Environment &` -> `Wood Environmental &`.
- `D03`: Added 3 bids for RFP 10009033 (same report embedded in a 402-page package that appeared standalone as `D07`).
- `D01`: Filled in contract `10040951` from the award clause.
- `D10`: Added the 3 disqualified tenderers per the bid definition above.

Deliberately kept unchanged: `D08` `Airborne Imaging`, `D09` `Action Buildworks`, and `D10` `MultiTech.` (both spelling forms appear verbatim in those PDFs), plus `D01` `Limited` (matches the bid results table rather than the pre-qualification list).

## Inter-rater agreement (D06-D10, 2026-07-30)

Raw Jaccard overlap across the 5 shared documents was 69%, split cleanly by contract coverage:

- **Contracts both labellers read:** 42 of 44 companies agreed (95%). The only 2 differences were verbatim name variants (`MultiTech.` vs `MultiTech Trades Corporation.`, `DJ McRae` vs `DJ McRae Contractors Ltd.`).
- **Contracts only one labeller found:** 13 companies (on D06 Gabe found 1 of 3 contracts; on D09, 1 of 4; both are large meeting packages).

Takeaways:

1. Two readers agree 95-100% once they locate the same bid list.
2. Finding every contract inside large packages is the hard part for both humans and regexes.
3. Labels on multi-contract packages may miss contracts, so recall against these labels is an upper bound, and apparent model "false positives" must be checked against the PDF text.
4. Gabe independently included all three disqualified tenderers on D10, corroborating the D10 adjudication.

(Note: `labels-gabe.json` was exported from browser `localStorage` via devtools because the page's export button failed in his browser.)

## Regex parser vs LLMs (Alex's set: D01-D10, 2026-07-29 to 2026-07-31)

Each model received the exact text in `documents.json` with `reasoning: effort=low` and the pre-qualified-vs-disqualified rule in the prompt, scored with legal-suffix-insensitive matching (0.88 `difflib` threshold):

| Extractor | AA-LCR | Recall | Precision | Single-contract | Multi-contract | Est. backlog cost |
|---|---:|---:|---:|---:|---:|---:|
| Incumbent `trca_board` regex | - | 59% (54/92) | 93% | 96% (25/26) | 44% (29/66) | $0 |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | 0.67 | 99% | 99% | 96% | 100% | $0 |
| `tencent/hy3-preview` | ? | 99% | 99% | 96% | 100% | ~$2 |
| `deepseek-v4-flash` | 0.657 | 74% | 99% | 96% | 65% | ~$4 |
| `openai/gpt-5.6-luna` | 0.69 | 100% | 100% | 100% | 100% | ~$4 |
| `openai/gpt-5.6-terra` | - | 99% | 99% | 96% | 100% | ~$10 |
| `openai/gpt-5.6-sol` | - | 100% | 100% | 100% | 100% | ~$56 |
| `moonshotai/kimi-k3` | - | 100% | 100% | 100% | 100% | ~$86 |
| `anthropic/claude-opus-5` | - | 100% | 100% | 100% | 100% | ~$290 |

The old regex parser failed structurally on multi-contract documents (stopping after the first `received from the following` list, dropping D09 to 25%), retiring the earlier #203 assumption that TRCA should keep its regex.

## Holdout test (Gabe's solo documents: D11-D15)

25 companies unseen during prompt development (`score-models.py gabe D11-D15`):

| Extractor | Recall | Precision |
|---|---:|---:|
| Incumbent regex parser | 68% | 81% |
| All four hosted models | 100% | 52-56% |

All 23 "false positives" from the hosted models were real verbatim bids from contracts Gabe did not reach (for example, D14 held four contracts where Gabe labelled one, and D15 correctly returned the 5 actual proposers while excluding 2 pre-qualified firms that never proposed). On D12, Gabe also listed both spellings of the City's typo (`Airborne Imagine Inc.` / `Airborne Imaging Inc.`) and missed `Aeroquest Mapcon Inc.`. This confirms precision against incomplete human labels cannot be trusted without checking the source PDF.

## Benchmark notes and caveats

- **General index vs AA-LCR:** Artificial Analysis Intelligence Index did not predict performance (only 12.5% of its weight covers long-context reasoning and instruction following). AA-LCR matched the Nemotron vs DeepSeek ordering once reasoning-token costs were priced in, though one failing model is not enough to validate it as a proxy (see `docs/protocols/model-selection-by-measured-ordering.md`).
- **Operational notes on free vs paid models (at time of evaluation):** `nemotron-3-ultra:free` lacked enforced JSON schema output and had a 20 req/min, 1,000 req/day cap; `gpt-5.6-luna` supported strict schemas and Flex pricing (~$4 for the 6,163-doc backlog). (Production later migrated to direct Claude API; see `deploy/README.md`.)
- **Harness caveats:**
  - The prompt gained an explicit `"reply with JSON"` instruction mid-evaluation for DeepSeek and Tencent; earlier runs (Luna, Terra, Sol, Opus 5) used constrained decoding.
  - Missing retry backoff originally caused `gpt-5.6-sol` to score 22% under rate limits before being fixed to 100%.
  - Run-to-run variance was ~1% (Nemotron scored 100% once and 99% on repeat).
  - `D09` is a Vendor of Record with six winners across three service categories.
  - Benchmark scores come from high/reasoning variants while these runs used `effort: low`.
