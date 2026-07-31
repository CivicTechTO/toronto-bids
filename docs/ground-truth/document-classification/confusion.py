"""Confusion matrices for the SVM and the LLM, plus accuracy under the schema's own groupings.

Two questions the aggregate accuracy cannot answer:

  1. Do the SVM and the LLM make the SAME mistakes? If so the categories are ambiguous, not the
     methods weak — and no amount of model capability fixes it.
  2. How much of the error is operationally harmless? The schema treats several classes
     identically (all extract nothing), so confusing two of them costs the archive nothing.
"""
import json
import pathlib
import warnings

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.svm import LinearSVC

warnings.filterwarnings("ignore")
S = pathlib.Path("/tmp/claude-1000/-home-alex-toronto-bids/"
                 "29bdc5d2-8380-4ad0-af3a-6c0de52f2f55/scratchpad")
GT = pathlib.Path("/home/alex/toronto-bids/docs/ground-truth/trca-bid-labels")

lab = {x["id"]: x for x in json.load(open("/home/alex/Downloads/document_classification.json"))["documents"]}
docs = [d for d in json.load(open(S / "class_public.json")) if d["id"] in lab]
ids = [d["id"] for d in docs]
X = [d["head"] for d in docs]
y = np.array([lab[i].get("kind") or "" for i in ids])

svm = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2,
                                    sublinear_tf=True), LinearSVC(C=1, class_weight="balanced"))
cv = StratifiedKFold(n_splits=2, shuffle=True, random_state=7)
pred_svm = cross_val_predict(svm, X, y, cv=cv)

# the schema's own grouping: what the archive DOES with each class
ACTION = {"procurement_award": "EXTRACT", "procurement_other": "EXTRACT",
          "agreement_or_mou": "REVIEW", "minutes": "CROSS-CHECK", "status_update": "CROSS-CHECK",
          "meeting_package": "SPLIT"}
act = lambda k: ACTION.get(k, "ignore")

CLASSES = sorted(set(y))
W = max(len(c) for c in CLASSES) + 1


def matrix(pred, title):
    print(f"\n{'='*76}\n{title}\n")
    print(" " * W + "".join(f"{c[:9]:>10s}" for c in CLASSES) + "   recall")
    for t in CLASSES:
        row = [(int(((y == t) & (pred == p)).sum())) for p in CLASSES]
        n = sum(row)
        hit = row[CLASSES.index(t)]
        cells = "".join((f"{v:>10d}" if v else f"{'·':>10s}") for v in row)
        print(f"{t:<{W}s}{cells}   {hit}/{n}")
    print(f"\n  overall accuracy: {(pred==y).mean():.0%}")
    grp = np.array([act(k) for k in pred]) == np.array([act(k) for k in y])
    print(f"  accuracy on what the archive DOES with it: {grp.mean():.0%}")
    lost = int((np.array([act(k) for k in y]) == "EXTRACT").sum()
               - ((np.array([act(k) for k in y]) == "EXTRACT")
                  & (np.array([act(k) for k in pred]) == "EXTRACT")).sum())
    print(f"  procurement documents that would NOT be extracted: {lost}")


matrix(pred_svm, "char 3-5gram + LinearSVC  (cross-validated, 2-fold)")

pf = GT / "classification-predictions.json"
if pf.exists():
    allp = json.load(open(pf))
    for tag, m in allp.items():
        if len(m) < len(ids) * 0.8:
            print(f"\n(skipping {tag}: only {len(m)} of {len(ids)} predictions — run incomplete)")
            continue
        pred = np.array([m.get(i, {}).get("kind") or "(none)" for i in ids])
        matrix(np.where(np.isin(pred, CLASSES), pred, "attachment_or_map"),
               f"LLM: {tag}   (out-of-vocab predictions folded to the nearest ignore class)")
