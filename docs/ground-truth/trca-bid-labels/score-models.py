"""Score extractors against the human labels in this directory.

    uv run python score-models.py alex          # Alex's 10 documents
    uv run python score-models.py gabe          # Gabe's 10 documents
    uv run python score-models.py gabe D11-D15  # a holdout subset

Each model is given EXACTLY the text the labeller saw (`documents.json`), so neither side has
access the other lacked. Needs OPENROUTER_API_KEY in scrapers/.env; the incumbent-parser column
additionally needs TB_DATA_DIR pointing at a store.

Written after the original harness was lost. The Sol-at-22% incident is why `ask()` backs off
and why a call that gives up says so out loud rather than returning an empty list silently.
"""
import concurrent.futures as cf
import difflib
import json
import os
import pathlib
import re
import sys
import time
import unicodedata

import httpx
from dotenv import load_dotenv

HERE = pathlib.Path(__file__).parent
load_dotenv("/home/alex/toronto-bids/scrapers/.env")
KEY = os.environ["OPENROUTER_API_KEY"]
MODELS = [("openai/gpt-5.6-luna", True), ("openai/gpt-5.6-terra", True),
          ("openai/gpt-5.6-sol", True), ("anthropic/claude-opus-5", False)]
CONC = 6

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["bids"],
          "properties": {"bids": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["company", "contract", "amount"],
              "properties": {"company": {"type": "string"}, "contract": {"type": "string"},
                             "amount": {"type": ["string", "null"]}}}}}}

# The pre-qualified-vs-disqualified distinction came from human labelling (see README), not from
# any model. It is given to the extractor because it is the archive's definition of a bid.
PROMPT = """Below is text from a Conservation Authority board report. It may describe SEVERAL
different contracts.

List every company that SUBMITTED A BID for a contract described here.

- A company that submitted a bid counts, including one whose bid was disqualified or found
  non-compliant. Leave its amount empty if no valid price is shown.
- A company that only made a PRE-QUALIFICATION submission and was never issued tender documents
  does NOT count — it never bid.
- Ignore evaluation committees, staff, consultants and the awarding body itself.
- Copy company names and amounts character-for-character as printed.
- For each bid, give the contract or tender number it was for (or the project name if none is
  shown), so bids on different contracts stay apart.

Text:
{block}
"""

SUFF = re.compile(r"\b(limited|ltd|inc|incorporated|corporation|corp|company|co|group|holdings"
                  r"|ulc|llp)\b\.?", re.I)


def key(s):
    s = unicodedata.normalize("NFKD", (s or "")).replace("’", "'").lower()
    return re.sub(r"[^a-z0-9]", "", SUFF.sub(" ", s))


def match(a, B):
    return a in B or any(difflib.SequenceMatcher(None, a, b).ratio() >= 0.88 for b in B)


CLIENT = httpx.Client(timeout=240, limits=httpx.Limits(max_connections=CONC + 8))
spend = {"$": 0.0}


def ask(model, flex, block):
    body = {"model": model, "max_tokens": 2500, "reasoning": {"effort": "low"},
            "messages": [{"role": "user", "content": PROMPT.format(block=block[:60000])}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "bids", "strict": True,
                                                "schema": SCHEMA}},
            "usage": {"include": True}}
    if flex:
        body["service_tier"] = "flex"
    for attempt in range(5):
        try:
            r = CLIENT.post("https://openrouter.ai/api/v1/chat/completions",
                            headers={"Authorization": f"Bearer {KEY}"}, json=body)
            if r.status_code != 200:
                time.sleep(2 ** attempt)          # rate limits need backoff, not instant retries
                continue
            j = r.json()
            spend["$"] += float((j.get("usage") or {}).get("cost") or 0)
            return json.loads(j["choices"][0]["message"]["content"]).get("bids", [])
        except Exception:
            time.sleep(2 ** attempt)
    print(f"    !! gave up on a call to {model} — this run is INVALID for it", flush=True)
    return []


def main():
    who = sys.argv[1] if len(sys.argv) > 1 else "alex"
    only = sys.argv[2] if len(sys.argv) > 2 else None
    docs = {d["id"]: d for d in json.load(open(HERE / "documents.json"))}
    lab = [d for d in json.load(open(HERE / f"labels-{who}.json"))["documents"]
           if [e for e in d["entries"] if e["company"].strip()]]
    if only:
        lo, hi = only.split("-")
        lab = [d for d in lab if lo <= d["id"] <= hi]
    truth_n = sum(len({key(e["company"]) for e in d["entries"]}) for d in lab)
    print(f"scoring against {who}: {len(lab)} documents, {truth_n} companies"
          + (f" ({only})" if only else ""))
    print(f"\n{'model':26s} {'recall':>7s} {'prec':>6s} {'single':>8s} {'MULTI':>8s} {'$':>8s}")

    try:
        sys.path.insert(0, "/home/alex/toronto-bids/scrapers")
        from toronto_bids import config
        from toronto_bids.store import db
        c = db.connect(config.DB_PATH)
        tp = fn = fp = 0
        g = {1: [0, 0], 2: [0, 0]}
        for d in lab:
            truth = {key(e["company"]) for e in d["entries"]}
            got = {key(r["n"]) for r in c.execute(
                "select distinct bidder_name_raw n from agency_bid a "
                "join background_pdf p on p.url=a.report_url "
                "where a.source='trca_board' and p.url=?", (d["url"],))}
            f = sum(1 for t in truth if match(t, got))
            tp += f
            fn += len(truth) - f
            fp += sum(1 for x in got if not match(x, truth))
            k = 1 if len({e.get("contract", "") for e in d["entries"]}) <= 1 else 2
            g[k][0] += f
            g[k][1] += len(truth)
        print(f"{'incumbent parser':26s} {tp/max(1,tp+fn):6.0%} {tp/max(1,tp+fp):5.0%} "
              f"{g[1][0]/max(1,g[1][1]):7.0%} {g[2][0]/max(1,g[2][1]):7.0%} {0:8.4f}")
    except Exception as exc:
        print(f"{'incumbent parser':26s}  (skipped: {exc})")

    for model, flex in MODELS:
        tp = fn = fp = 0
        g = {1: [0, 0], 2: [0, 0]}
        start = spend["$"]
        with cf.ThreadPoolExecutor(max_workers=CONC) as ex:
            futs = {d["id"]: ex.submit(ask, model, flex, docs[d["id"]]["context"]) for d in lab}
            for d in lab:
                truth = {key(e["company"]) for e in d["entries"]}
                got = {key(b.get("company")) for b in futs[d["id"]].result() if b.get("company")}
                got.discard("")
                f = sum(1 for t in truth if match(t, got))
                tp += f
                fn += len(truth) - f
                fp += sum(1 for x in got if not match(x, truth))
                k = 1 if len({e.get("contract", "") for e in d["entries"]}) <= 1 else 2
                g[k][0] += f
                g[k][1] += len(truth)
        print(f"{model:26s} {tp/max(1,tp+fn):6.0%} {tp/max(1,tp+fp):5.0%} "
              f"{g[1][0]/max(1,g[1][1]):7.0%} {g[2][0]/max(1,g[2][1]):7.0%} "
              f"{spend['$']-start:8.4f}", flush=True)
    print(f"\ntotal spend: ${spend['$']:.4f}")


if __name__ == "__main__":
    main()
