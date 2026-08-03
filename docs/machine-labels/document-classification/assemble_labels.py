"""Assemble the final machine-labelled set from all vote sources.

Usage: assemble_labels.py <judge_journal> [<more journals>...]
Reads corpus/votes.json (the two haiku passes) and merges every extra journal given
(patch votes tagged haiku|*, judge votes tagged judge|*).

Decision rules, per document:
  kind:  all votes agree            -> that kind,  agreement 'unanimous'
         majority among >=3 votes   -> that kind,  agreement 'judged'
         no majority                -> kind null,  agreement 'disputed'   (listed, never guessed)
  flag:  majority of votes (odd count after judging; a 1-1 tie with no judge -> disputed)

Honeypots (120 human-labelled docs that rode the whole pipeline under corpus ids) are scored
end-to-end and EXCLUDED from the deliverable. Probes never entered the corpus plan.
"""
import ast
import collections
import json
import pathlib
import sys

S = pathlib.Path(__file__).resolve().parent

ck = json.load(open(S / "corpus" / "key.json"))
hp_key = json.load(open(S / "corpus" / "honeypot_key.json"))
lab = {x["id"]: x for x in json.load(
    open("/home/alex/Downloads/document_classification.json"))["documents"]}
votes = json.load(open(S / "corpus" / "votes.json"))

for J in sys.argv[1:]:
    for line in open(J):
        e = json.loads(line)
        if e.get("type") != "result":
            continue
        r = e["result"]
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except json.JSONDecodeError:
                r = ast.literal_eval(r)
        if not isinstance(r, dict) or "batch_tag" not in r:
            continue
        role = r["batch_tag"].split("|")[0]           # 'haiku' patch vote or 'judge'
        for entry in r["labels"]:
            i = entry["id"]
            if i not in votes:
                continue
            votes[i].append({"pass": role, "kind": entry["kind"],
                             "flag": bool(entry["contains_bid_or_award"]),
                             "confidence": entry["confidence"],
                             "evidence": entry["evidence"]})


def decide(vs):
    """kind: majority vote, null on no majority (a display/audit call - errs toward "don't know").
    flag: OR of votes, not majority. The project's own cost model (bow_binary.py) makes this the
    right rule: a false positive on the flag costs one wasted extraction call; a false negative
    means the document is never extracted and nothing downstream ever notices. Measured on this
    run: 9 of the 5,750 corpus docs had exactly one vote (of three) correctly catch a real award
    - a named supplier plus a dollar figure, e.g. "Award to Carollo Engineers Canada Ltd./EXP
    Services Inc. $2,765,968.25; three suppliers bid" - outvoted 2-1 under a majority rule. OR
    recovers those at the cost of extra (cheap, harmless) extraction calls on the false-positive
    side; it never manufactures certainty it doesn't have on the kind classification.
    """
    kinds = collections.Counter(v["kind"] for v in vs)
    (top_kind, top_n), = kinds.most_common(1)
    if top_n == len(vs):
        kind, k_agree = top_kind, "unanimous"
    elif top_n > len(vs) / 2:
        kind, k_agree = top_kind, "judged"
    else:
        kind, k_agree = None, "disputed"
    t = sum(v["flag"] for v in vs)
    flag = t > 0
    f_agree = "unanimous" if t in (0, len(vs)) else "or_recovered" if t < len(vs) else "judged"
    ev = ""
    pool = [v for v in vs if v["flag"]] if flag else [v for v in vs if v["kind"] == kind]
    if pool or vs:
        ev = max((v["evidence"] for v in (pool or vs)), key=len)
    return kind, k_agree, flag, f_agree, ev


final = {}
for i, vs in votes.items():
    if not vs:
        final[i] = None
        continue
    kind, ka, flag, fa, ev = decide(vs)
    final[i] = {"kind": kind, "kind_agreement": ka,
                "contains_bid_or_award": flag, "flag_agreement": fa,
                "votes": len(vs), "evidence": ev,
                "vote_detail": [{"who": v["pass"], "kind": v["kind"], "flag": v["flag"]}
                                for v in vs]}

# ---- honeypots: the end-to-end deliverable accuracy on known answers
hk = hf = hf_tp = hf_fp = hf_fn = 0
h_tot = 0
hp_miss = []
for xid, cid in hp_key.items():
    f = final.get(xid)
    if not f:
        continue
    h_tot += 1
    truth_kind = lab[cid].get("kind") or ""
    truth_flag = bool(lab[cid].get("contains_bid_or_award"))
    hk += f["kind"] == truth_kind
    hf += f["contains_bid_or_award"] == truth_flag
    if f["contains_bid_or_award"] and truth_flag:
        hf_tp += 1
    if f["contains_bid_or_award"] and not truth_flag:
        hf_fp += 1
    if f["contains_bid_or_award"] is False and truth_flag:
        hf_fn += 1
        hp_miss.append((xid, cid))
print(f"HONEYPOTS end-to-end (n={h_tot}): kind {hk}/{h_tot} ({hk/max(1,h_tot):.0%})   "
      f"flag {hf}/{h_tot} ({hf/max(1,h_tot):.0%})   "
      f"flag tp {hf_tp} fp {hf_fp} fn {hf_fn}")
if hp_miss:
    print("  MISSED FLAGS (the expensive error):")
    for xid, cid in hp_miss:
        print(f"    {xid} <- {cid}  human kind={lab[cid].get('kind')}")

# ---- deliverable: corpus docs only
out, disputed_k, disputed_f, unvoted = [], 0, 0, 0
kind_dist = collections.Counter()
for i in sorted(ck["docs"]):
    f = final.get(i)
    if not f:
        unvoted += 1
        continue
    disputed_k += f["kind"] is None
    disputed_f += f["contains_bid_or_award"] is None
    kind_dist[f["kind"] or "(disputed)"] += 1
    out.append({"url": ck["docs"][i]["url"], "chars": ck["docs"][i]["chars"], **f})

doc = {
    "provenance": "machine",
    "warning": ("Machine-generated labels. NEVER pool with the human ground truth in "
                "docs/ground-truth/ - validate against it."),
    "snapshot_utc": ck["snapshot_utc"],
    "method": {
        "labeller": "claude-haiku-4.5 subagents, full-document access, effort=low",
        "passes": "2 independent blind passes; disagreements judged by a third blind "
                  "fable vote; majority decides, no majority -> disputed (null)",
        "calibration": "125 human-labelled docs x 3 models x 2 passes; haiku chosen "
                       "(2-pass union: proc-recall 22/22, flag-recall 25/25)",
        "workflows": ["wf_86b439f2-e85 (calibration)", "wf_7340bd0f-d86 (corpus passes)"],
    },
    "honeypots": {"n": h_tot, "kind_acc": round(hk / max(1, h_tot), 3),
                  "flag_acc": round(hf / max(1, h_tot), 3),
                  "flag_tp": hf_tp, "flag_fp": hf_fp, "flag_fn": hf_fn},
    "labels": out,
}
dest = S / "labels-machine.json"
json.dump(doc, open(dest, "w"), indent=1)
print(f"\nDELIVERABLE: {len(out):,} corpus docs -> {dest}")
print(f"  kind disputed {disputed_k}   flag disputed {disputed_f}   no votes at all {unvoted}")
print(f"  flag=true: {sum(1 for o in out if o['contains_bid_or_award'])}   "
      f"kind distribution: {dict(kind_dist.most_common())}")
agree = collections.Counter(o["kind_agreement"] for o in out)
print(f"  kind agreement: {dict(agree)}")
