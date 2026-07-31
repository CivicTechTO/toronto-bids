# Selecting a model by measured ordering, not by leaderboard

A protocol for choosing a model for a *specific* task, when the published rankings are composites
built for someone else's workload.

**The problem it solves.** General leaderboards (Artificial Analysis Intelligence Index, LMArena,
and similar) aggregate many capabilities into one number. Your task uses a few of them. So the
composite ranks models by a weighted average that may put almost no weight on the thing you
actually need — and a price/performance frontier computed on it will recommend the wrong model,
in either direction: overspending on capability you don't use, or picking a model that fails on
the one axis that matters.

**The idea.** Use the general index only to *sample* candidates. Then let your own measured
results tell you which published benchmark ranks models the way your task does, and re-compute
the frontier on **that** benchmark. You end up with a cheap proxy that predicts your workload,
so future model choices need no new measurement.

---

## The protocol

### 1. Compute the price/performance frontier on a general index — for YOUR token shape

Take a broad index and compute the Pareto front of cost against score. Two details matter:

- **Price the front on your real request shape.** Published "cost per 100 requests" or "$/M
  tokens" assume an input:output ratio that is probably not yours. Measure your own — a
  long-input/short-output extraction task and a short-input/long-output generation task rank
  providers differently, because output typically bills at 5–10× input.
- **The front is a sampling frame, not a shortlist.** Its job is to give you models that are
  *spread out* in capability and price, cheaply.

### 2. Measure real performance on the actual task, against ground truth

This is the expensive step and there is no way around it.

- **Ground truth must be independent of every candidate**, and independent of any incumbent
  system you are comparing against. If you score against the incumbent's output, the incumbent
  is 100% by construction and every disagreement is charged against the candidate.
- **Report the metric your task actually cares about.** Precision measured against incomplete
  ground truth punishes a model for being right; if your labels may under-cover, recall is the
  sound number and disagreements must be adjudicated individually.
- **Look for the axis that discriminates.** Aggregate accuracy often hides it. Bucket results by
  a structural property of the input and see where the spread appears.

### 3. Find the published benchmark whose ordering matches yours

Now you have a measured ranking. Look for a public benchmark that ranks the same models the same
way — and prefer one that is *mechanistically* related to the discriminating axis you found, not
merely correlated.

The composite's own components are the first place to look. A composite is usually a weighted sum
of published sub-benchmarks; find the ones that load on your axis and check their weight in the
total. If your task's competence is 6% of the index, that alone explains why the index misled you.

### 4. Re-compute the frontier on that benchmark, and select from it

The new front is your selector. From here, choosing a model for this task costs a lookup rather
than an experiment — which is the payoff, because model releases are continuous and re-measuring
everything each time is not viable.

Re-validate against ground truth periodically, and whenever the task's input distribution shifts.

---

## Why this is economically right

Direct measurement is *more* accurate than any proxy — the task is its own best benchmark. The
reason not to just measure everything is throughput: there are hundreds of models and they change
weekly.

So the protocol spends measurement once to buy a **cheap, reusable predictor**. Step 2 costs real
effort (ground truth is the expensive part, not the inference). Steps 3–4 amortise it.

A useful consequence: once you have ground truth, testing one more candidate is often trivially
cheap — cents and minutes. Keep the proxy for *narrowing* hundreds of models to a handful, and
still measure the handful directly.

---

## Failure modes

**No discrimination.** If every model on the front scores the same, you have learned that your
task sits below the front's floor — not that all models are equal. **Extend the front downward**
(cheaper and weaker models) until something fails. You cannot identify a correlated benchmark
from a flat line.

**Too little discrimination.** One failing model is not a ranking. Picking the benchmark that
happens to rank that one model low is curve-fitting to a single point. You need several models
spread across the measured metric before step 3 is meaningful.

**Ties everywhere.** With few models and many ties, several benchmarks will "match" your ordering
by chance. Prefer a mechanistic explanation for *why* a benchmark should track your task, and
treat ordering agreement as corroboration rather than evidence on its own.

**A false ordering from a broken harness.** This is the dangerous one, because it silently
selects the wrong benchmark. A model that scores 0% because it rejected your request format, or
22% because your retry loop had no backoff, will look like a capability difference. Guard it:

- make a failed call *say so loudly* rather than returning empty
- treat any impossible result (accuracy rising as input degrades, a round number equal to the
  document count) as an apparatus bug until proven otherwise
- expect provider-specific request handling — some reject JSON-schema mode, some require the
  literal word "json" in the prompt, some ignore response schemas entirely

**Ground truth that is itself incomplete.** If your labellers miss things, a model that finds
them is penalised. Adjudicate disagreements against the source before believing a precision
number.

---

## Worked example: bid extraction from procurement documents (2026-07)

**Task.** Extract every company that submitted a bid, with amount and contract, from
Conservation Authority board reports. Long input (~4,500 tokens), short output (~700), measured
in:out ratio **5.9:1**.

**Step 1.** Pareto front on the Artificial Analysis Intelligence Index, priced on that actual
ratio rather than a generic one.

**Step 2.** Ground truth built by two people labelling from scratch — no candidate names shown,
blind to what any machine proposed. 15 documents, ~92 companies, with per-contract attribution.

Measured (recall against human labels):

| model | Intelligence Index | recall | multi-contract | cost/backlog |
|---|---|---|---|---|
| incumbent regex parser | — | 59% | **44%** | free |
| `nemotron-3-ultra:free` | **37.8** | **99%** | 100% | **$0** |
| `tencent/hy3-preview` | 41.2 | 99% | 100% | ~$2 |
| `deepseek-v4-flash` | **49.9** | **74%** | **65%** | ~$4 |
| `gpt-5.6-luna` | 51.2 | 100% | 100% | ~$4 |
| `gpt-5.6-terra` | 55.0 | 99% | 100% | ~$10 |
| `kimi-k3` | 57.1 | 100% | 100% | ~$86 |
| `claude-opus-5` | 60.7 | 100% | 100% | ~$290 |

**The index does not predict performance.** It spans 37.8→60.7 and produces a flat 99–100% line
with one outlier — and the outlier is *not* the cheapest model. DeepSeek at II 49.9 scores 74%,
below two cheaper models at 37.8 and 41.2.

**The discriminating axis was structural, not general.** Bucketing by whether a document covered
one contract or several: every model scored ~96% on single-contract documents. Multi-contract is
where the spread appeared — 100% for most, **65%** for DeepSeek, **44%** for the incumbent parser.
The competence is *"find all N instances in a long document"*, not "reason well".

**Step 3 — where this example stops.** The index's own weighting already explains the failure:
long-context reasoning (AA-LCR) is **6.25%** of the total and instruction-following (IFBench)
another 6.25%. This task is essentially only those two — **12.5% of the index**, with the other
87.5% measuring agents, coding and scientific reasoning that the task never touches.

But the protocol could **not** be completed here, and it is worth being explicit about why:
only **one** model discriminated. One failure is not an ordering, so no benchmark can be
validated against it. Completing step 3 would need candidates spread across the measured metric —
which means deliberately testing models *expected to fail*, not just the frontier.

**Practical outcome anyway.** A free model does the task at 99%, and the most expensive model
tested (18× the price of Luna, 100× that of the free one) does no better. The measurement paid
for itself regardless of whether a proxy benchmark was ever identified.

---

## When not to use this

- **A one-off task.** If you will never choose a model for this workload again, skip steps 3–4
  and just measure the candidates directly. The proxy only pays off across repeated selection.
- **No ground truth is obtainable.** Then you have no step 2, and the protocol has no foundation.
  Building ground truth is usually the right first investment anyway — it is what makes every
  later claim checkable.
- **Cost is already negligible.** If the whole workload costs a few dollars on the best available
  model, optimising the selection is not worth the measurement. Note this is only knowable *after*
  pricing the task on your real token shape, which is step 1.
