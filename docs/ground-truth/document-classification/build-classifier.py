"""Train the runtime extraction-gate classifier and write its weights (#229).

The package cannot depend on scikit-learn, so this script — the only place it is used — trains
the model `bow-classify.py` measured best ("char 3-5gram + LinearSVC": 22/22 proc-recall, 65%
class accuracy) on all 125 human-labelled documents, and ships its vocabulary, idf, weights
and intercepts to `scrapers/toronto_bids/data/document_classifier.json.gz`. Inference is
reimplemented in pure Python in `toronto_bids/classify.py`; this script checks the two agree.

Run from `scrapers/` (scikit-learn is pulled in for this run only, never added to the lock):

    uv run --with scikit-learn python ../docs/ground-truth/document-classification/build-classifier.py

THE GATE, AND HOW ITS THRESHOLD WAS CHOSEN
The gate needs a binary answer: could this document carry a bid or award? In the human labels
the 25 `contains_bid_or_award` documents are 22 procurement_award, 1 procurement_other and
2 minutes — so the multi-class model is collapsed with
    margin = max(score of a non-KEEP class) - max(score of a KEEP class)
and a document is skipped only when margin > THRESHOLD. (bow-classify.py's binary models
trained on the flag directly were far worse: 18-19/25 recall at 5-fold CV.)

Threshold measurement: repeated 2-fold stratified CV (the k bow-classify.py had to use, since
classes have 2-3 members), N_REPEATS shuffles, every document scored out-of-fold each repeat.
Recall is counted on the human flag. At the seed bow-classify.py used (7), margin > 0 already
missed nothing — but across shuffles margin > 0 missed 19 of 750 and > 0.1 missed 2. The worst
flag-positive margin seen over 30 repeats was +0.12; THRESHOLD = 0.3 leaves a 0.18 cushion above
it and still skips about half of the non-procurement documents. Losing a bid document is
permanent and silent; an extra extraction call costs cents.
"""

import collections
import gzip
import json
import pathlib
import sys
import warnings

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.svm import LinearSVC

warnings.filterwarnings("ignore")

HERE = pathlib.Path(__file__).resolve().parent
SCRAPERS = HERE.parent.parent.parent / "scrapers"
OUT = SCRAPERS / "toronto_bids" / "data" / "document_classifier.json.gz"

KEEP = ["agenda", "meeting_package", "minutes", "procurement_award", "procurement_other"]
THRESHOLD = 0.3
HEAD_CHARS = 2600  # documents.json "head": the view the labeller and the models saw
NGRAM_RANGE = (3, 5)
N_REPEATS = 30
DECIMALS = 3  # rounding drift ~4e-3 in margin, against a 0.18 cushion; keeps the file small


def pipeline():
    return make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=NGRAM_RANGE, min_df=2, sublinear_tf=True),
        LinearSVC(C=1, class_weight="balanced", random_state=0),
    )


def margins(dec, classes):
    ki = [i for i, c in enumerate(classes) if c in KEEP]
    si = [i for i, c in enumerate(classes) if c not in KEEP]
    return dec[:, si].max(1) - dec[:, ki].max(1)


lab = {x["id"]: x for x in json.load(open(HERE / "labels-alex.json"))["documents"]}
docs = [d for d in json.load(open(HERE / "documents.json")) if d["id"] in lab]
X = [d["head"][:HEAD_CHARS] for d in docs]
y = np.array([lab[d["id"]].get("kind") or "" for d in docs])
flag = np.array([bool(lab[d["id"]].get("contains_bid_or_award")) for d in docs])
classes = sorted(set(y))
print(f"documents: {len(X)}   classes: {len(classes)}   flag-positive: {flag.sum()}")
print("flag-positive by kind:", dict(collections.Counter(y[flag])))

# --- threshold measurement, out of fold ---
missed = skipped_neg = 0
worst = []
for seed in range(N_REPEATS):
    cv = StratifiedKFold(n_splits=2, shuffle=True, random_state=seed)
    dec = cross_val_predict(pipeline(), X, y, cv=cv, method="decision_function")
    m = margins(dec, classes)
    worst.append(float(m[flag].max()))
    missed += int(((m > THRESHOLD) & flag).sum())
    skipped_neg += int(((m > THRESHOLD) & ~flag).sum())
n_pos, n_neg = int(flag.sum()), int((~flag).sum())
kept_neg = n_neg * N_REPEATS - skipped_neg
recall = 1 - missed / (n_pos * N_REPEATS)
precision_kept = (n_pos * N_REPEATS - missed) / (n_pos * N_REPEATS - missed + kept_neg)
print(f"\n{N_REPEATS}x repeated 2-fold CV at threshold {THRESHOLD}:")
print(f"  flag recall (bid/award docs NOT skipped): {n_pos * N_REPEATS - missed}/"
      f"{n_pos * N_REPEATS} = {recall:.1%}")
print(f"  skip precision (skipped docs truly non-procurement): "
      f"{skipped_neg}/{skipped_neg + missed} = {skipped_neg / max(1, skipped_neg + missed):.1%}")
print(f"  non-procurement skipped: {skipped_neg / N_REPEATS:.1f}/{n_neg} per repeat "
      f"({skipped_neg / (n_neg * N_REPEATS):.0%})")
print(f"  precision of the kept (extracted) set: {precision_kept:.0%}")
print(f"  worst flag-positive margin: {max(worst):+.3f}  (skip needs > {THRESHOLD})")
if missed:
    sys.exit(f"REFUSING to write: threshold {THRESHOLD} lost {missed} bid/award documents")

# --- train on everything and export ---
model = pipeline().fit(X, y)
vec, svc = model.steps[0][1], model.steps[1][1]
assert list(svc.classes_) == classes
idf = vec.idf_
coef = svc.coef_  # (n_classes, n_features)
features = {
    gram: [round(float(idf[j]), DECIMALS)] + [round(float(w), DECIMALS) for w in coef[:, j]]
    for gram, j in sorted(vec.vocabulary_.items())
}
data = {
    "about": "Generated by docs/ground-truth/document-classification/build-classifier.py "
             "(#229). Do not edit by hand.",
    "classes": classes,
    "keep": KEEP,
    "threshold": THRESHOLD,
    "head_chars": HEAD_CHARS,
    "ngram_range": list(NGRAM_RANGE),
    "intercepts": [round(float(b), DECIMALS) for b in svc.intercept_],
    "features": features,
}
raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
OUT.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
print(f"\nwrote {OUT} ({OUT.stat().st_size // 1024} KiB, {len(features)} features)")

# --- the pure-Python inference must reproduce scikit-learn ---
sys.path.insert(0, str(SCRAPERS))
from toronto_bids.classify import DocumentClassifier  # noqa: E402

clf = DocumentClassifier.load(OUT)
sk = margins(model.decision_function(X), classes)
py = np.array([clf.margin(d["head"]) for d in docs])
drift = float(np.abs(sk - py).max())
print(f"pure-Python vs scikit-learn margin, max abs difference: {drift:.2e}")
assert drift < 0.01, drift
print(f"in-sample: {int(((py > THRESHOLD) & flag).sum())} bid/award docs skipped, "
      f"{int(((py > THRESHOLD) & ~flag).sum())}/{n_neg} non-procurement skipped")
