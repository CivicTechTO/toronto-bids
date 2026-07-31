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
# override with:  --models a,b,c   (flex only applies to OpenAI ids)
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

Reply with JSON only, of the form {{"bids":[{{"company":..., "contract":..., "amount":...}}]}}.
(Some providers require the literal word "json" in the prompt before they will emit JSON, and
some ignore a response schema entirely — so the format is stated here as well as requested.)

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


def _parse(txt):
    """Lenient JSON extraction: some providers fence the block or prepend prose."""
    if not txt:
        return []
    s = txt.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\n?|```$", "", s, flags=re.M).strip()
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1:
        return []
    try:
        return json.loads(s[i:j + 1]).get("bids", [])
    except Exception:
        return []


def ask(model, flex, block):
    """Structured output, degrading gracefully across providers.

    Not every provider accepts `json_schema` — DeepSeek answers 400 "This response_format type
    is unavailable now" but handles `json_object` fine. Falling back matters: without it the
    model scores 0% and looks incapable when it was never actually asked.
    """
    base = {"model": model, "max_tokens": 4000, "reasoning": {"effort": "low"},
            "messages": [{"role": "user", "content": PROMPT.format(block=block[:60000])}],
            "usage": {"include": True}}
    if flex:
        base["service_tier"] = "flex"
    formats = [{"type": "json_schema",
                "json_schema": {"name": "bids", "strict": True, "schema": SCHEMA}},
               {"type": "json_object"}]
    for fmt in formats:
        body = dict(base, response_format=fmt)
        for attempt in range(4):
            try:
                r = CLIENT.post("https://openrouter.ai/api/v1/chat/completions",
                                headers={"Authorization": f"Bearer {KEY}"}, json=body)
                if r.status_code == 400:
                    break                       # this format is unsupported; try the next one
                if r.status_code != 200:
                    time.sleep(2 ** attempt)    # rate limit: back off, never retry instantly
                    continue
                j = r.json()
                spend["$"] += float((j.get("usage") or {}).get("cost") or 0)
                return _parse(j["choices"][0]["message"].get("content"))
            except Exception:
                time.sleep(2 ** attempt)
    print(f"    !! gave up on a call to {model} — this run is INVALID for it", flush=True)
    return []


def main():
    global MODELS
    argv = list(sys.argv[1:])
    if "--models" in argv:
        i = argv.index("--models")
        MODELS = [(m, m.startswith("openai/")) for m in argv[i + 1].split(",")]
        del argv[i:i + 2]
    who = argv[0] if argv else "alex"
    only = argv[1] if len(argv) > 1 else None
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
