# GLiNER2 vs the incumbent regex on TRCA prose bidders — result

Criterion fixed in advance: `2026-07-28-gliner2-trca-bakeoff-criterion.md`. Nothing below was
chosen after seeing the numbers, except where explicitly flagged.

**Setup.** GLiNER2 1.3.2 (`fastino/gliner2-base-v1`, 205M, Apache 2.0), local weights, CPU only
(i3-13100, 4 cores, torch pinned to 4 threads), throwaway venv outside the project. 144 TRCA
documents carrying the incumbent's bullet anchor; ground truth is the reports' own declared bid
counts (101 of them) and, for pairing, the ruled bid tables' own cells.

## Headline: the safety argument I made before running this was WRONG

The case for an extractive model over a generative one was: it tags spans in the input, so every
output can be verified verbatim against the source, so it cannot fabricate. **That property
holds and is worthless on its own.**

| | |
|---|---|
| verbatim verification failures | **0** |
| (bidder, price) pairs **correct** | **29 / 80 — 36%** |

Every name is real. Every price is real. **64% of the relations between them are fabricated**,
and verbatim checking cannot detect any of it. A bid record is a *relation*, and a relation has
no verbatim existence in the text to check against.

The mechanism, from `DocumentId=10725`: the model emitted names in **bullet-list order**
(alphabetical, as the prose lists them) and paired them against prices in **table order**
(ascending). Two orderings, zipped positionally. It assigned `$548,415` to
`CDR Young's Aggregates Inc.` — a firm that was **disqualified and had no price at all**.

Errors are not near-misses:

```
Doornekamp Construction  -> got '$ 209,650'   truth '$1,032,930'
Glenn Windrem Trucking   -> got '$ 633,465'   truth '$245,105'
AVI-SPL Canada Ltd.      -> got '$976,000'    truth '$1,155,960'
```

This is #94's lesson — *pairing is positional, so one stray line misattributes every bid after
it* — arriving from a completely new direction. #94 and #116 solved it by **refusing an unequal
pair rather than guessing**. GLiNER2 has no such refusal: it always produces a pairing.

## Scores against the criterion

| clause | result |
|---|---|
| (a) count agreement ≥ incumbent | **PASS** — 46/101 vs 43/101 (structured), 49/101 vs 43/101 (names-only) |
| (b) zero verbatim failures | **PASS** — 0 structured, 1 names-only |
| (c) no real bidder lost | **FAIL** — misses real incumbent bidders, e.g. 5 in `18386` |
| (d) delivers what the incumbent cannot | **FAIL** — the only candidate gain is prices, and prices are 64% fabricated |

**Verdict: do not adopt for (bidder, price) extraction.** The one capability it adds over the
incumbent is the one it gets wrong, in a way no cheap verifier can catch.

Cost, for the record: 0.38 s/doc names-only, 0.5 s/doc structured, ~2 s model load, on CPU. Speed
was never the problem.

## The refinement pass, and a bug in it worth recording

The criterion allowed one refinement. Chosen **before** seeing the baseline: names only, no price
relation — because all 408 stored TRCA bids carry `bid_price` NULL, so names are the incumbent's
actual job, and a flat list has no relation to fabricate.

**The first run of it was invalid and the invalid numbers were nearly reported as a GLiNER2
failure.** It returned `0/101` and 144 verbatim failures — because `extract_entities` returns
`{"entities": {label: [names]}}` and the harness flattened one level too few, emitting the label
string itself as a "name", exactly one per document. That signature (a round number equal to the
document count) is what gave it away. Corrected shape, re-run: **49/101 vs the incumbent's
43/101**, 981 rows vs 527, 1 verbatim failure.

## One genuinely open question, deliberately NOT pursued

Names-only found bidders in documents where the incumbent returns **nothing** — `10725` and
`10947` both yield 5 real firms from GLiNER2 and 0 from the regex, because `parse_trca_report`
is gated on finding a solicitation reference first. That is a real coverage gap and a plausible
criterion-(d) gain.

**It is unverified and must not be reported as a gain.** The 981-vs-527 row difference has had
**no precision check** — GLiNER2 may equally be picking up consultants, City staff, or
non-bidding firms named nearby. Establishing that would be a second refinement pass, past the
gate, so it stops here. It is a reason to look again, not a conclusion.

## What this says about local models here generally

Nothing about GLiNER2 disqualifies local CPU inference as an approach; the model reads names
well and costs almost nothing. The finding is narrower and more useful:

**Where the data is a relation, an extractive model's safety property does not apply, and this
archive's corpora are almost entirely relations** — bidder↔price, table↔solicitation,
award↔supplier. Any future local-model work here has to be scored on **relation** accuracy
against independent ground truth, never on whether the extracted strings appear in the source.
