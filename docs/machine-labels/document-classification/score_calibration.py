"""Score the calibration workflow against Alex's 125 human labels.

Reads the workflow journal (each agent's structured return, self-identified by batch_tag),
joins on document id, and answers, per model:
  - kind accuracy, procurement recall/precision   (the governing metric: a missed award is
    never extracted and nothing downstream notices)
  - binary flag recall/precision
  - self-consistency: pass1 vs pass2 vote flips   (= the judge load a corpus run would carry)
Plus the cross-model check that matters for the ground truth itself: documents where ALL SIX
votes agree with each other and disagree with Alex are candidate human-label errors, printed
with each model's evidence quote.
"""
import ast
import collections
import json
import pathlib
import sys

S = pathlib.Path(__file__).resolve().parent
JOURNAL = pathlib.Path(sys.argv[1])

lab = {x["id"]: x for x in json.load(
    open("/home/alex/Downloads/document_classification.json"))["documents"]}
key = json.load(open(S / "calib" / "key.json"))
probes = {i for i, k in key.items() if k["probe"]}

votes = {}                                    # (model, pass, docid) -> label dict
for line in open(JOURNAL):
    e = json.loads(line)
    if e.get("type") != "result":
        continue
    r = e["result"]
    if isinstance(r, str):                    # journal may stringify python-repr style
        try:
            r = json.loads(r)
        except json.JSONDecodeError:
            r = ast.literal_eval(r)
    if not isinstance(r, dict) or "batch_tag" not in r:
        continue
    model, p, _b = r["batch_tag"].split("|")
    for entry in r["labels"]:
        votes[(model, p, entry["id"])] = entry

MODELS = ["haiku", "sonnet", "fable"]
scored = [i for i in lab]                     # the 125 human-labelled ids
n_proc = sum(1 for i in scored if (lab[i].get("kind") or "") == "procurement_award")
n_flag = sum(1 for i in scored if lab[i].get("contains_bid_or_award"))
print(f"human ground truth: {len(scored)} docs, {n_proc} procurement_award, "
      f"{n_flag} flag-positives\n")

hdr = (f"{'model':7s} {'pass':4s} {'cover':>6s} {'kind-acc':>8s} "
       f"{'proc-rec':>9s} {'proc-prec':>9s} {'flag-rec':>9s} {'flag-prec':>9s}")
print(hdr)


def stats(model, p):
    got = [i for i in scored if (model, p, i) in votes]
    if not got:
        return None
    v = {i: votes[(model, p, i)] for i in got}
    kind_ok = sum(v[i]["kind"] == (lab[i].get("kind") or "") for i in got)
    tp = sum(v[i]["kind"] == "procurement_award" == (lab[i].get("kind") or "") for i in got)
    fp = sum(v[i]["kind"] == "procurement_award" != (lab[i].get("kind") or "") for i in got)
    fn = sum((lab[i].get("kind") or "") == "procurement_award" != v[i]["kind"] for i in got)
    ftp = sum(v[i]["contains_bid_or_award"] and lab[i].get("contains_bid_or_award") for i in got)
    ffp = sum(v[i]["contains_bid_or_award"] and not lab[i].get("contains_bid_or_award") for i in got)
    ffn = sum(not v[i]["contains_bid_or_award"] and lab[i].get("contains_bid_or_award") for i in got)
    print(f"{model:7s} {p:4s} {len(got):3d}/125 {kind_ok/len(got):8.0%} "
          f"{tp:5d}/{tp+fn:<3d} {tp/max(1,tp+fp):9.0%} {ftp:5d}/{ftp+ffn:<3d} "
          f"{ftp/max(1,ftp+ffp):9.0%}")
    return v


per_model = {}
for m in MODELS:
    v1, v2 = stats(m, "p1"), stats(m, "p2")
    per_model[m] = (v1, v2)
    if v1 and v2:
        both = [i for i in scored if i in v1 and i in v2]
        kflip = sum(v1[i]["kind"] != v2[i]["kind"] for i in both)
        fflip = sum(v1[i]["contains_bid_or_award"] != v2[i]["contains_bid_or_award"]
                    for i in both)
        agree = [i for i in both if v1[i]["kind"] == v2[i]["kind"]]
        acc_agree = (sum(v1[i]["kind"] == (lab[i].get("kind") or "") for i in agree)
                     / max(1, len(agree)))
        print(f"{'':7s} self-consistency: kind flips {kflip}/{len(both)} "
              f"({kflip/max(1,len(both)):.0%} would need a judge), flag flips {fflip}; "
              f"when both passes agree, kind-acc {acc_agree:.0%}\n")

# ---- candidate human-label errors: all six votes agree, and against Alex
print("\nUNANIMOUS-vs-HUMAN disagreements (candidate label errors):")
n_cand = 0
for i in scored:
    six = [votes.get((m, p, i)) for m in MODELS for p in ("p1", "p2")]
    if any(x is None for x in six):
        continue
    kinds = {x["kind"] for x in six}
    if len(kinds) == 1 and kinds != {lab[i].get("kind") or ""}:
        n_cand += 1
        mk = six[0]["kind"]
        ev = max((x["evidence"] for x in six), key=len)
        print(f"  {i}: human={lab[i].get('kind') or '(blank)'}  all-6-votes={mk}")
        print(f"      {key[i]['url'][:100]}")
        print(f"      evidence: {ev[:220]}")
    flags = {x["contains_bid_or_award"] for x in six}
    if len(flags) == 1 and flags != {bool(lab[i].get("contains_bid_or_award"))}:
        print(f"  {i}: FLAG human={bool(lab[i].get('contains_bid_or_award'))} "
              f"all-6-votes={flags.pop()}  "
              f"evidence: {max((x['evidence'] for x in six), key=len)[:180]}")
if n_cand == 0:
    print("  (none)")

# ---- protocol probes: the 6 biggest corpus docs, no human label — eyeball the behaviour
print("\nPROBES (largest unlabelled docs — protocol stress test):")
for i in sorted(probes):
    row = [f"{i} ({key[i]['chars']:,} chars)"]
    for m in MODELS:
        for p in ("p1", "p2"):
            x = votes.get((m, p, i))
            row.append(f"{m[:3]}.{p}={x['kind'] if x else 'MISSING'}"
                       + ("+FLAG" if x and x["contains_bid_or_award"] else ""))
    print("  " + "  ".join(row))
    ev = [votes[(m, p, i)]["evidence"] for m in MODELS for p in ("p1", "p2")
          if (m, p, i) in votes]
    if ev:
        print(f"      e.g. {max(ev, key=len)[:200]}")

# ---- confusion for the strongest model, pass 1
counts = collections.Counter()
for m in MODELS:
    v1, v2 = per_model[m]
    for v in (v1, v2):
        if not v:
            continue
        for i in v:
            counts[(m, (lab[i].get("kind") or ""), v[i]["kind"])] += 1
print("\ntop confusions per model (human -> model, both passes pooled):")
for m in MODELS:
    rows = [(k[1], k[2], n) for k, n in counts.items() if k[0] == m and k[1] != k[2]]
    rows.sort(key=lambda x: -x[2])
    print(f"  {m}: " + "; ".join(f"{a or '(blank)'}->{b} x{n}" for a, b, n in rows[:5]))
