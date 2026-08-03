"""Build the corpus labelling plan: 5,750 unlabelled docs + 120 honeypots, 2 passes.

Honeypots are human-labelled calibration docs re-minted under corpus ids (file copies, so
nothing distinguishes them), present in BOTH passes like any real document. They therefore
flow through consensus and judging, measuring the accuracy of the DELIVERABLE — the whole
pipeline — on docs with known answers, not merely per-vote agreement.
"""
import json
import pathlib
import random
import shutil

S = pathlib.Path(__file__).resolve().parent
PER_BATCH_TARGET = 12
N_HONEYPOT = 120

ck = json.load(open(S / "corpus" / "key.json"))
calkey = json.load(open(S / "calib" / "key.json"))

docs = [{"id": i, "chars": k["chars"], "path": str(S / "corpus" / "docs" / f"{i}.txt")}
        for i, k in ck["docs"].items()]

rng = random.Random(801)
hp_pool = sorted(i for i, k in calkey.items() if not k["probe"])
hp = rng.sample(hp_pool, N_HONEYPOT)
hp_key = {}
for n, cid in enumerate(hp, 1):
    xid = f"X{len(docs) + n:05d}"             # continues the corpus numbering
    shutil.copyfile(S / "calib" / "docs" / f"{cid}.txt",
                    S / "corpus" / "docs" / f"{xid}.txt")
    hp_key[xid] = cid
json.dump(hp_key, open(S / "corpus" / "honeypot_key.json", "w"), indent=1)
for xid, cid in hp_key.items():
    docs.append({"id": xid, "chars": calkey[cid]["chars"],
                 "path": str(S / "corpus" / "docs" / f"{xid}.txt")})

n_batches = -(-len(docs) // PER_BATCH_TARGET)
(S / "corpus" / "manifests").mkdir(exist_ok=True)


def partition(seed):
    r = random.Random(seed)
    d = docs[:]
    r.shuffle(d)
    d.sort(key=lambda x: -x["chars"])
    batches = [{"docs": [], "chars": 0} for _ in range(n_batches)]
    for doc in d:
        b = min(batches, key=lambda b: (b["chars"], len(b["docs"])))
        b["docs"].append(doc)
        b["chars"] += min(doc["chars"], 80_000)
    for b in batches:
        r.shuffle(b["docs"])
    return [b["docs"] for b in batches]


total_files = 0
for p, seed in [(1, 31), (2, 32)]:
    for bn, part in enumerate(partition(seed), 1):
        mp = S / "corpus" / "manifests" / f"p{p}_b{bn:03d}.txt"
        mp.write_text("\n".join(f"{d['id']} | {d['chars']} chars | {d['path']}"
                                for d in part) + "\n")
        total_files += 1

print(f"{len(docs):,} docs ({len(docs) - N_HONEYPOT:,} corpus + {N_HONEYPOT} honeypots)")
print(f"{n_batches} batches per pass x 2 passes = {total_files} manifests "
      f"-> {total_files} labelling agents")
