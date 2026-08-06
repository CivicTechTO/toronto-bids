"""Snapshot the corpus out of the production DB into flat files, once.

Everything downstream — 66 calibration agents tonight, ~1,000 corpus agents after the gate —
reads only these files. The production DB is touched by exactly this one script, read-only,
and before the 05:30 nightly can change what "the corpus" means mid-run.

Layout (all under this directory):
  calib/docs/<id>.txt    the 125 human-labelled documents, FULL text, existing ids
  calib/probes/P0x.txt   6 largest corpus docs — protocol stress probes, no human label
  calib/key.json         id -> url/chars/probe flag  (answer side, never shown to agents)
  corpus/docs/<X>.txt    the 5,748 unlabelled documents, shuffled opaque ids
  corpus/key.json        X-id -> url/chars + snapshot provenance
"""
import datetime
import json
import pathlib
import random
import sys

sys.path.insert(0, "/home/alex/toronto-bids/scrapers")
from toronto_bids import config          # noqa: E402
from toronto_bids.store import db        # noqa: E402

S = pathlib.Path(__file__).resolve().parent
OLD = S.parent  # the session scratchpad, where class_public.json lives

lab = {x["id"]: x for x in json.load(
    open("/home/alex/Downloads/document_classification.json"))["documents"]}
pub = json.load(open(OLD / "class_public.json"))
calib = [d for d in pub if d["id"] in lab]
assert len(calib) == 125, len(calib)

c = db.connect(config.DB_PATH)
rows = {r["url"]: r["text"] for r in c.execute(
    "select url, text from background_pdf where text is not null and length(text) > 200")}
print(f"snapshot: {len(rows):,} documents from {config.DB_PATH}")

(S / "calib" / "docs").mkdir(parents=True, exist_ok=True)
(S / "calib" / "probes").mkdir(parents=True, exist_ok=True)
(S / "corpus" / "docs").mkdir(parents=True, exist_ok=True)

key = {}
for d in calib:
    text = rows.get(d["url"]) or d["full"]          # DB text is canonical; tool payload fallback
    (S / "calib" / "docs" / f"{d['id']}.txt").write_text(text, errors="replace")
    key[d["id"]] = {"url": d["url"], "chars": len(text), "probe": False}

calib_urls = {d["url"] for d in calib}
rest = [(u, t) for u, t in rows.items() if u not in calib_urls]

# protocol probes: the 6 largest unlabelled documents — the regime with zero human labels
rest.sort(key=lambda x: -len(x[1]))
probes = rest[:6]
for n, (u, t) in enumerate(probes, 1):
    pid = f"P{n:02d}"
    (S / "calib" / "probes" / f"{pid}.txt").write_text(t, errors="replace")
    key[pid] = {"url": u, "chars": len(t), "probe": True}
json.dump(key, open(S / "calib" / "key.json", "w"), indent=1)

snap_ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
rng = random.Random(2026)
rng.shuffle(rest)                                   # opaque ids carry no source ordering
ck = {"snapshot_utc": snap_ts, "db": str(config.DB_PATH), "docs": {}}
for n, (u, t) in enumerate(rest, 1):
    xid = f"X{n:05d}"
    (S / "corpus" / "docs" / f"{xid}.txt").write_text(t, errors="replace")
    ck["docs"][xid] = {"url": u, "chars": len(t)}
json.dump(ck, open(S / "corpus" / "key.json", "w"))

print(f"calib: 125 docs + 6 probes (largest {probes[0][1] and len(probes[0][1]):,} chars)")
print(f"corpus: {len(rest):,} docs under opaque ids   snapshot_utc {snap_ts}")
