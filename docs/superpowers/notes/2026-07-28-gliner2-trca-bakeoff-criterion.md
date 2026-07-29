# GLiNER2 vs the incumbent regex on TRCA prose bidders — criterion, fixed in advance

Written **before** installing anything or looking at any output, per CLAUDE.md's
"Parsing discipline". Recording it first is what stops the result being chosen after the fact.

## The question

TRCA's bidders exist mainly in prose bullet lists. `#203` measured that cells cannot replace the
regex there (356 rows vs the incumbent's 527, and 30 of 55 documents with a bid table cannot be
attributed to a solicitation). The open question is whether a **local, CPU-bound extraction
model** does better than the regex on the *prose*, which is where regex is weakest.

Candidate: **GLiNER2** (Fastino Labs, Apache 2.0, 205M params, CPU-first). It is an *encoder*
that tags spans in the input rather than generating text, so it cannot invent a bidder or a
price. That property is why it is being tried ahead of any generative model.

## Corpus and ground truth

- **Population**: TRCA `agency_board` reports whose text carries the incumbent's bullet-list
  anchor (`received from the following Proponent|vendor`) — **171 documents**, measured.
- **Ground truth**: the report's own declared count — `"Three (3) proposals were received"` —
  present on **128** reports corpus-wide. Ground truth comes from the documents, never from the
  parser under test, and never from the incumbent.
- **Input to the model**: the regex-narrowed span, not the whole document. TRCA documents run to
  577 pages; the incumbent's anchor already locates the region, and reusing it keeps the
  comparison about *extraction* rather than about retrieval.

## Metrics

1. **Count agreement** — extracted bidder count == declared count, per report. Primary.
2. **Verbatim verification** — every extracted name and price must appear character-for-character
   in the input span. Expected to be 100% by construction; measured anyway, because an assumed
   safety property is not a safety property.
3. **Superset check** — does GLiNER2 find the bidders the incumbent finds? Any incumbent bidder
   it drops is a real loss and must be inspected individually.
4. **Net-new** — bidders or prices GLiNER2 gets that the incumbent cannot. TRCA's 408 stored bids
   currently carry **zero** prices, so prices are the obvious candidate gain.
5. **Wall-clock** per document on this box (i3-13100, 4 cores, no GPU).

## Switch criterion

Adopt GLiNER2 for TRCA prose bidders **iff all four hold**:

- (a) count agreement **≥** the incumbent's on the same 128 reports;
- (b) **zero** verbatim-verification failures;
- (c) **no real bidder lost** against the incumbent (every drop explained as incumbent
  contamination, not as a miss);
- (d) it delivers something the incumbent cannot — prices, or documents the incumbent misses.

Otherwise: record the measurement, keep the regex, and do not re-litigate without new evidence.
A negative result is a complete answer, exactly as #83's and #203's were.

## Stop-and-ask gate

**One** refinement pass on the schema (field names and descriptions). The rule-count analogue
here is schema churn: if improving one report's output degrades another's, that is divergence —
**stop, report, keep the regex.** No per-document prompt tuning; no threshold fitted to the
failures. Tuning against the ground-truth set until it passes is not measurement, it is
overfitting with extra steps.

## Deployment constraint, decided in advance

GLiNER2 pulls PyTorch (~2 GB). This project's core pipeline has no such dependency, and a
`uv sync --locked` has already broken the nightly once by dropping optional deps. So even a
*successful* result does **not** put torch on the nightly path: it would ship as an opt-in
enrichment pass (the `enrich-council` pattern). The bake-off itself runs in a throwaway venv
outside the project entirely — nothing is added to `pyproject.toml` unless it earns it.
