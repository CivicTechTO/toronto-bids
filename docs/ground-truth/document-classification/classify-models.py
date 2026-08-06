"""Score document classifiers against the human labels.

    uv run python classify-models.py                      # the whole pareto front
    uv run python classify-models.py --models a,b --effort high

Stage one of the two-stage design: decide what a document IS, from its header block only, so
stage two (extraction) runs on the procurement subset. Classification sees ~2,500 characters —
the same view the human labeller had by default — which is what makes it cheap.

Scored against `document_classification.json`: 125 documents spanning every source, shuffled,
labelled blind. The rules baseline on the same set is 36%.
"""
import argparse
import collections
import concurrent.futures as cf
import json
import pathlib
import re
import sys
import time

import httpx
from dotenv import load_dotenv
import os

HERE = pathlib.Path(__file__).parent
load_dotenv("/home/alex/toronto-bids/scrapers/.env")
KEY = os.environ["OPENROUTER_API_KEY"]
LABELS = "/home/alex/Downloads/document_classification.json"
DOCS = pathlib.Path("/tmp/claude-1000/-home-alex-toronto-bids/"
                    "29bdc5d2-8380-4ad0-af3a-6c0de52f2f55/scratchpad/class_public.json")
HEAD = 2500
CONC = 6

# The pareto front, priced on our token shape with reasoning cost included.
FRONT = [("nvidia/nemotron-3-ultra-550b-a55b:free", None, 0.0),
         ("openai/gpt-5.6-luna", "high", 0.0259),
         ("openai/gpt-5.6-luna", "xhigh", 0.0309),
         ("openai/gpt-5.6-luna", "max", 0.0361),
         ("openai/gpt-5.2-codex", "xhigh", 0.3231)]

CLASSES = ["procurement_award", "procurement_other", "minutes", "agenda", "meeting_package",
           "agreement_or_mou", "land_property", "permit_regulatory", "governance_finance",
           "status_update", "attachment_or_map", "correspondence", "empty_or_unreadable"]

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["kind", "contains_bid_or_award"],
          "properties": {"kind": {"type": "string", "enum": CLASSES},
                         "contains_bid_or_award": {"type": "boolean"}}}

PROMPT = """Classify this document from a public-sector procurement archive. Reply with JSON only.

Choose exactly one "kind":

- procurement_award   a tender/RFP/RFQ/vendor-of-record being AWARDED to a supplier
- procurement_other   about buying something, but not an award report
- minutes             record of a meeting and its resolutions
- agenda              a list of items for a meeting
- meeting_package     many separate items bound into one document
- agreement_or_mou    a contract, MOU, lease or licence with another party — INCLUDING one
                      where the other party pays US (venue hire, licence fees)
- land_property       acquisition, disposal, easement, site plan
- permit_regulatory   permits, regulation applications, approvals
- governance_finance  budget, audit, policy, appointments, insurance
- status_update       an update on a project or programme
- attachment_or_map   a supporting exhibit, map, drawing or appendix — not a report itself
- correspondence      a letter from a councillor or an outside party
- empty_or_unreadable blank, a fragment, or garbled text

Also set "contains_bid_or_award": true if the document contains a list of who bid, an amount
awarded, or a named winning supplier — true even when "kind" is not a procurement type.

Reply as {{"kind": "...", "contains_bid_or_award": true|false}}.

Document:
{block}
"""

CLIENT = httpx.Client(timeout=240, limits=httpx.Limits(max_connections=CONC + 8))
# Free endpoints are capped at 20 requests/minute and throttle hard under bursts. Measured:
# CONC=6 trips it and the run reports INVALID. Concurrency is therefore per-model, not global.
FREE_CONC = 2
spend = {"$": 0.0}
applied_effort = {}


def ask(model, effort, block):
    base = {"model": model, "max_tokens": 3000,
            "messages": [{"role": "user", "content": PROMPT.format(block=block[:HEAD])}],
            "usage": {"include": True}}
    if model.startswith("openai/"):
        base["service_tier"] = "flex"
    # effort names differ between the benchmark's variant labels and the API; degrade rather
    # than silently score a model that was never actually asked.
    efforts = [effort, "high", None] if effort else [None]
    formats = [{"type": "json_schema",
                "json_schema": {"name": "c", "strict": True, "schema": SCHEMA}},
               {"type": "json_object"}]
    for eff in efforts:
        for fmt in formats:
            body = dict(base, response_format=fmt)
            if eff:
                body["reasoning"] = {"effort": eff}
            for attempt in range(6):
                try:
                    r = CLIENT.post("https://openrouter.ai/api/v1/chat/completions",
                                    headers={"Authorization": f"Bearer {KEY}"}, json=body)
                    if r.status_code == 400:
                        break
                    if r.status_code != 200:
                        time.sleep(min(30, 2 ** attempt) * (3 if ":free" in model else 1))
                        continue
                    j = r.json()
                    spend["$"] += float((j.get("usage") or {}).get("cost") or 0)
                    applied_effort[(model, effort)] = eff or "none"
                    txt = (j["choices"][0]["message"].get("content") or "").strip()
                    if txt.startswith("```"):
                        txt = re.sub(r"^```[a-z]*\n?|```$", "", txt, flags=re.M).strip()
                    i, k = txt.find("{"), txt.rfind("}")
                    if i < 0:
                        return None
                    o = json.loads(txt[i:k + 1])
                    return (o.get("kind"), bool(o.get("contains_bid_or_award")))
                except Exception:
                    time.sleep(2 ** attempt)
    print(f"    !! gave up: {model} ({effort}) — INVALID for this model", flush=True)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models")
    ap.add_argument("--effort")
    a = ap.parse_args()
    front = FRONT
    if a.models:
        front = [(m, a.effort, 0.0) for m in a.models.split(",")]

    lab = {x["id"]: x for x in json.load(open(LABELS))["documents"]}
    docs = [d for d in json.load(open(DOCS)) if d["id"] in lab]
    print(f"classification: {len(docs)} documents, header block of {HEAD} chars")
    print(f"{'rules baseline':38s} {'36%':>7s}\n")
    print(f"{'model':30s} {'eff':>6s} {'class':>7s} {'bidflag':>8s} {'proc-recall':>12s} {'$':>8s}")

    preds = {}
    for model, effort, _ in front:
        start = spend["$"]
        with cf.ThreadPoolExecutor(max_workers=FREE_CONC if ":free" in model else CONC) as ex:
            got = {d["id"]: ex.submit(ask, model, effort, d["head"]) for d in docs}
            ok = flag_ok = n = 0
            proc_hit = proc_tot = 0
            conf = collections.Counter()
            for d in docs:
                truth = lab[d["id"]].get("kind") or ""
                tflag = bool(lab[d["id"]].get("contains_bid_or_award"))
                res = got[d["id"]].result()
                n += 1
                if res is None:
                    conf[("(no answer)", truth)] += 1
                    continue
                k, f = res
                preds.setdefault(f"{model}|{effort}", {})[d["id"]] = {"kind": k, "flag": f}
                ok += (k == truth)
                flag_ok += (f == tflag)
                if truth == "procurement_award":
                    proc_tot += 1
                    proc_hit += (k == "procurement_award")
                if k != truth:
                    conf[(k, truth)] += 1
        eff = applied_effort.get((model, effort), "?")
        print(f"{model:30s} {eff:>6s} {ok/n:6.0%} {flag_ok/n:7.0%} "
              f"{proc_hit}/{proc_tot:<3d}{'':>4s} {spend['$']-start:8.4f}", flush=True)
        if conf:
            top = ", ".join(f"{g}->{t}×{c}" for (g, t), c in conf.most_common(3))
            print(f"{'':38s} top errors: {top}", flush=True)
    out = HERE / "classification-predictions.json"
    json.dump(preds, open(out, "w"), indent=1)
    print(f"\npredictions saved -> {out.name}  (re-scoring never needs a re-run)")
    print(f"total spend: ${spend['$']:.4f}")
    print("proc-recall = procurement_award documents correctly identified; the expensive error, "
          "since a missed award is never extracted and nothing downstream notices.")


if __name__ == "__main__":
    main()
