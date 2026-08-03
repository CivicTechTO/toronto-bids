"""Join the corpus run's two vote passes; emit judge/patch work and interim honeypot stats.

Outputs (under corpus/):
  votes.json            (docid -> [vote, ...]) parsed once, so assembly never re-reads journals
  judge/manifests/      docs whose two votes disagree on kind or flag -> third blind vote (fable)
  patch/manifests/      docs with fewer than two votes -> extra haiku vote first
Returned ids are validated against each batch's manifest: unknown ids are dropped and counted,
missing ids feed the patch list. Honeypot per-vote accuracy is reported as an interim check;
the end-to-end honeypot number comes after judging, in the assembly step.
"""
import ast
import json
import pathlib
import sys

S = pathlib.Path(__file__).resolve().parent
JOURNALS = [pathlib.Path(a) for a in sys.argv[1:]]
PER_BATCH = 12

ck = json.load(open(S / "corpus" / "key.json"))
hp_key = json.load(open(S / "corpus" / "honeypot_key.json"))
calkey = json.load(open(S / "calib" / "key.json"))
lab = {x["id"]: x for x in json.load(
    open("/home/alex/Downloads/document_classification.json"))["documents"]}
all_ids = set(ck["docs"]) | set(hp_key)

manifest_ids = {}                              # (pass, batch) -> ids expected in that batch
for mp in (S / "corpus" / "manifests").glob("p*_b*.txt"):
    p, b = mp.stem.split("_")
    ids = [line.split(" | ")[0] for line in mp.read_text().splitlines() if line.strip()]
    manifest_ids[(p, int(b[1:]))] = ids

votes = {i: [] for i in all_ids}
unknown, dupes, results_seen = 0, 0, 0
for J in JOURNALS:
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
        results_seen += 1
        _model, p, b = r["batch_tag"].split("|")
        expect = set(manifest_ids.get((p, int(b[1:])), []))
        seen_here = set()
        for entry in r["labels"]:
            i = entry["id"]
            if i not in expect or i not in votes:
                unknown += 1
                continue
            if i in seen_here:
                dupes += 1
                continue
            seen_here.add(i)
            votes[i].append({"pass": p, "kind": entry["kind"],
                             "flag": bool(entry["contains_bid_or_award"]),
                             "confidence": entry["confidence"],
                             "evidence": entry["evidence"]})

json.dump(votes, open(S / "corpus" / "votes.json", "w"))
counts = {0: 0, 1: 0, 2: 0}
for i, v in votes.items():
    counts[min(len(v), 2)] = counts.get(min(len(v), 2), 0) + 1
print(f"batch results parsed: {results_seen}   unknown-id labels dropped: {unknown}   "
      f"dupes: {dupes}")
print(f"vote coverage: 2 votes {counts[2]:,}   1 vote {counts[1]:,}   0 votes {counts[0]:,}")

# ---- interim honeypot check: per-vote accuracy on docs with known human labels
hk_ok = hk_tot = hf_ok = hf_tot = 0
for xid, cid in hp_key.items():
    truth_kind = lab[cid].get("kind") or ""
    truth_flag = bool(lab[cid].get("contains_bid_or_award"))
    for v in votes[xid]:
        hk_tot += 1
        hk_ok += v["kind"] == truth_kind
        hf_tot += 1
        hf_ok += v["flag"] == truth_flag
if hk_tot:
    print(f"honeypots (interim, per-vote): kind {hk_ok}/{hk_tot} ({hk_ok/hk_tot:.0%})   "
          f"flag {hf_ok}/{hf_tot} ({hf_ok/hf_tot:.0%})   [calibration was ~60% / ~90%]")

# ---- work lists
def doc_line(i):
    src = hp_key.get(i)
    path = S / "corpus" / "docs" / f"{i}.txt"
    chars = calkey[src]["chars"] if src else ck["docs"][i]["chars"]
    return f"{i} | {chars} chars | {path}"


patch = sorted(i for i, v in votes.items() if len(v) < 2)
disagree = sorted(i for i, v in votes.items() if len(v) == 2
                  and (v[0]["kind"] != v[1]["kind"] or v[0]["flag"] != v[1]["flag"]))
kind_dis = sum(1 for i in disagree if votes[i][0]["kind"] != votes[i][1]["kind"])
flag_dis = sum(1 for i in disagree if votes[i][0]["flag"] != votes[i][1]["flag"])
print(f"\npatch (needs another haiku vote): {len(patch):,} docs")
print(f"judge (2 votes, disagree):        {len(disagree):,} docs "
      f"(kind {kind_dis:,}, flag {flag_dis:,})")

for name, ids in [("patch", patch), ("judge", disagree)]:
    d = S / "corpus" / name / "manifests"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("b*.txt"):
        old.unlink()
    for bn in range(0, len(ids), PER_BATCH):
        chunk = ids[bn:bn + PER_BATCH]
        (d / f"b{bn // PER_BATCH + 1:03d}.txt").write_text(
            "\n".join(doc_line(i) for i in chunk) + "\n")
    n = -(-len(ids) // PER_BATCH) if ids else 0
    print(f"  {name}: {n} manifests of <= {PER_BATCH}")
