"""Does a dirt-simple bag-of-words linear classifier match the LLM on stage one?

If it does, stage one needs no API: instant, deterministic, free, no rate limit, no vendor.

Honest framing up front: the LLM needs ZERO training examples. A supervised classifier needs
labels, and we have 125 across 13 classes — some classes have 2 or 3 members. So this is
cross-validated, and the numbers are noisy by construction. Treat gaps under ~10 points as
inconclusive.

Compared against, on the same 125 documents:
    majority class (always "status_update")   24%
    rules baseline                            36%
    LLM (nemotron free / luna high)        64-67%,  proc-recall 22/22 and 21/22
"""
import collections
import json
import pathlib
import warnings

import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline
from sklearn.svm import LinearSVC

warnings.filterwarnings("ignore")
S = pathlib.Path("/tmp/claude-1000/-home-alex-toronto-bids/"
                 "29bdc5d2-8380-4ad0-af3a-6c0de52f2f55/scratchpad")

lab = {x["id"]: x for x in json.load(open("/home/alex/Downloads/document_classification.json"))["documents"]}
docs = [d for d in json.load(open(S / "class_public.json")) if d["id"] in lab]
X = [d["head"] for d in docs]                      # same 2,500-char view the LLM gets
y = np.array([lab[d["id"]].get("kind") or "" for d in docs])
flag = np.array([bool(lab[d["id"]].get("contains_bid_or_award")) for d in docs])

print(f"documents: {len(X)}   classes: {len(set(y))}")
counts = collections.Counter(y)
print("class sizes:", dict(counts.most_common()))
thin = [k for k, v in counts.items() if v < 5]
print(f"classes with <5 examples (cannot be 5-fold stratified reliably): {thin}\n")

# 5-fold stratified is impossible where a class has <5 members; drop to the smallest viable k
k = max(2, min(5, min(counts.values())))
cv = StratifiedKFold(n_splits=k, shuffle=True, random_state=7)
print(f"using {k}-fold stratified cross-validation\n")

MODELS = {
    "majority class": DummyClassifier(strategy="most_frequent"),
    "word 1-2gram + LogReg": make_pipeline(
        TfidfVectorizer(lowercase=True, ngram_range=(1, 2), min_df=2, sublinear_tf=True,
                        strip_accents="unicode"),
        LogisticRegression(max_iter=2000, C=5, class_weight="balanced")),
    "word 1-2gram + LinearSVC": make_pipeline(
        TfidfVectorizer(lowercase=True, ngram_range=(1, 2), min_df=2, sublinear_tf=True,
                        strip_accents="unicode"),
        LinearSVC(C=1, class_weight="balanced")),
    "word unigram + NaiveBayes": make_pipeline(
        TfidfVectorizer(lowercase=True, min_df=1, sublinear_tf=True), MultinomialNB(alpha=.2)),
    "char 3-5gram + LinearSVC": make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True),
        LinearSVC(C=1, class_weight="balanced")),
}

print(f"{'model':30s} {'class acc':>10s} {'proc-recall':>12s} {'proc-prec':>10s}")
for name, pipe in MODELS.items():
    pred = cross_val_predict(pipe, X, y, cv=cv)
    acc = (pred == y).mean()
    tp = ((pred == "procurement_award") & (y == "procurement_award")).sum()
    fn = ((pred != "procurement_award") & (y == "procurement_award")).sum()
    fp = ((pred == "procurement_award") & (y != "procurement_award")).sum()
    rec = tp / max(1, tp + fn)
    pre = tp / max(1, tp + fp)
    print(f"{name:30s} {acc:9.0%} {tp:8d}/{tp+fn:<3d} {pre:9.0%}")

# the flag is a separate, binary, and much easier problem
print(f"\n{'--- contains_bid_or_award (binary) ---':30s}")
cvb = StratifiedKFold(n_splits=5, shuffle=True, random_state=7)
for name in ("word 1-2gram + LogReg", "word 1-2gram + LinearSVC"):
    pred = cross_val_predict(MODELS[name], X, flag, cv=cvb)
    tp = (pred & flag).sum(); fn = (~pred & flag).sum(); fp = (pred & ~flag).sum()
    print(f"{name:30s} acc {(pred == flag).mean():.0%}   "
          f"recall {tp}/{tp+fn}   precision {tp/max(1,tp+fp):.0%}")

# what is it keying on? a linear model will tell you, unlike the LLM
best = MODELS["word 1-2gram + LinearSVC"].fit(X, y)
vec, clf = best.steps[0][1], best.steps[1][1]
names = np.array(vec.get_feature_names_out())
idx = list(clf.classes_).index("procurement_award")
top = names[np.argsort(clf.coef_[idx])[-14:]][::-1]
print(f"\ntop features for procurement_award: {', '.join(top)}")
