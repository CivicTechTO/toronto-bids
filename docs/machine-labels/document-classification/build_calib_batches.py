"""Build the calibration batch plan: 3 models x 2 passes x 11 size-balanced batches.

The two passes use DIFFERENT partitions (different seed), so a document's two votes from the
same model come from different batch contexts — a bad batchmate can't poison both votes.
Models share the partition within a pass, so the model comparison sees identical conditions.
"""
import json
import pathlib
import random

S = pathlib.Path(__file__).resolve().parent
MODELS = ["haiku", "sonnet", "fable"]
N_BATCH = 11

key = json.load(open(S / "calib" / "key.json"))
docs = []
for did, k in key.items():
    sub = "probes" if k["probe"] else "docs"
    docs.append({"id": did, "chars": k["chars"],
                 "path": str(S / "calib" / sub / f"{did}.txt")})
assert len(docs) == 131

INSTRUCTIONS = """\
You are labelling municipal documents for a public procurement archive. Judge each document
INDEPENDENTLY — one document's content must never influence another's label.

TAXONOMY — choose exactly ONE kind per document. kind = what the document IS (its purpose),
not what it mentions.
- procurement_award: the document's purpose is reporting or recommending the award of a
  tender / RFP / RFQ / quotation / vendor-of-record to a supplier.
- procurement_other: about buying something, but not an award report — prequalification,
  procurement policy, a solicitation being issued or extended.
- minutes: the record of a meeting and its resolutions.
- agenda: the list of items for an upcoming meeting.
- meeting_package: many separate items/reports bound into one large document.
- agreement_or_mou: an agreement / MOU / lease / licence with another party — INCLUDING one
  where the other party pays us (a festival licence agreement, a lease to a tenant).
- land_property: land or property — acquisition, disposal, easement, expropriation, site plan.
- permit_regulatory: permits, regulation applications, approvals.
- governance_finance: budget, audit, policy, appointments, insurance.
- status_update: a status or progress update on a project or programme.
- attachment_or_map: a supporting exhibit, map or drawing — not a report itself.
- correspondence: a letter from a councillor or an outside party.
- empty_or_unreadable: blank, a tiny fragment, or garbled text — even if you can guess what
  it was meant to be.

THE FLAG — contains_bid_or_award is INDEPENDENT of kind: set it true if bidder names, bid
prices, or the award of a contract to a named supplier appear ANYWHERE in the text. Minutes
and meeting packages often contain awards — flag those true while keeping their kind.

READING PROTOCOL (file paths and sizes are in the manifest):
- 60,000 chars or fewer: Read the ENTIRE file before labelling.
- larger: (a) Read the first 15,000 chars; (b) run exactly this, substituting the path:
  grep -inE "tender|request for (proposal|quotation|tender)|rfp|rfq|bids? (received|submitted|opened)|lowest.*bid|award of (the )?contract|contract be awarded|be awarded to|purchase order|vendor of record|procurement" FILE | head -60
  (c) Read a ~3,000-char region around the strongest matches (Read with offset/limit) before
  deciding. The flag covers the WHOLE document, not just the part you happened to read.

EVIDENCE — a short verbatim quote (max 300 chars) from the document that best supports the
kind, or the flag whenever the flag is true.

Label EVERY document in the manifest — one entry per id, in any order. Reply ONLY through the
structured output tool; no prose."""


def partition(seed):
    rng = random.Random(seed)
    d = docs[:]
    rng.shuffle(d)
    d.sort(key=lambda x: -x["chars"])            # greedy bin-pack on size
    batches = [{"docs": [], "chars": 0} for _ in range(N_BATCH)]
    for doc in d:
        b = min(batches, key=lambda b: b["chars"])
        b["docs"].append(doc)
        b["chars"] += min(doc["chars"], 80_000)  # huge docs cost ~a capped read, not 8M
    for b in batches:
        rng.shuffle(b["docs"])                   # no size ordering visible to the agent
    return [b["docs"] for b in batches]


(S / "calib" / "manifests").mkdir(exist_ok=True)
plan, sizes = [], []
for p, seed in [(1, 11), (2, 22)]:
    parts = partition(seed)
    for bn, part in enumerate(parts, 1):
        mpath = S / "calib" / "manifests" / f"p{p}_b{bn:02d}.txt"
        mpath.write_text("\n".join(f"{d['id']} | {d['chars']} chars | {d['path']}"
                                   for d in part) + "\n")
        sizes.append(len(part))
        for model in MODELS:
            plan.append({"model": model, "pass": p, "batch": bn,
                         "manifest": str(mpath), "n": len(part),
                         "ids": [d["id"] for d in part]})

json.dump({"instructions": INSTRUCTIONS, "batches": plan},
          open(S / "calib" / "batches.json", "w"))
print(f"{len(plan)} agent tasks ({len(MODELS)} models x 2 passes x {N_BATCH} batches), "
      f"batch sizes {min(sizes)}-{max(sizes)} docs")
