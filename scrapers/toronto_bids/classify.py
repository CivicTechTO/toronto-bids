"""Stage-one document classifier for the extraction gate (#229).

The static machine-label snapshot (`config.CLASSIFICATION_LABELS_PATH`) only covers documents
captured before it was taken; every later document would go straight to the paid model. This
module judges those documents offline with the bag-of-words model #206 measured against the
human ground truth (`docs/ground-truth/document-classification/`): character 3-5-gram TF-IDF
(`char_wb`, sublinear tf, min_df=2) into a one-vs-rest LinearSVC (C=1, balanced) over the
13-class `kind` taxonomy, reading the first `HEAD_CHARS` characters — the same view it was
trained and measured on.

Inference only, in pure Python: the trained vocabulary, idf and weights ship as
`data/document_classifier.json.gz`, written by
`docs/ground-truth/document-classification/build-classifier.py` (the one place scikit-learn
is used; regenerate there, never edit the file by hand).

The gate asks a binary question of a multi-class model. The `margin` is the best score among
classes the archive does NOT extract from, minus the best score among classes that can carry
a bid or award (`procurement_award`, `procurement_other`, `minutes`, and the untested
`agenda`/`meeting_package`). A document is skipped only when the margin exceeds the stored
threshold — chosen for recall on procurement, never for savings. See the generator for the
measurement.
"""

from __future__ import annotations

import functools
import gzip
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent / "data" / "document_classifier.json.gz"

_WHITE_SPACES = re.compile(r"\s\s+")


def char_wb_ngrams(text: str, min_n: int, max_n: int) -> list[str]:
    """scikit-learn's `char_wb` analyzer (lowercase, no accent stripping), verbatim.

    N-grams never cross a word boundary; each word is padded with one space either side, and
    a word shorter than `n` contributes itself once.
    """
    text = _WHITE_SPACES.sub(" ", text.lower())
    grams: list[str] = []
    for w in text.split():
        w = " " + w + " "
        w_len = len(w)
        for n in range(min_n, max_n + 1):
            offset = 0
            grams.append(w[offset : offset + n])
            while offset + n < w_len:
                offset += 1
                grams.append(w[offset : offset + n])
            if offset == 0:
                break
    return grams


@dataclass(frozen=True)
class DocumentClassifier:
    classes: tuple[str, ...]
    keep: frozenset[str]
    intercepts: tuple[float, ...]
    features: dict[str, list[float]]  # ngram -> [idf, weight per class]
    ngram_range: tuple[int, int]
    head_chars: int
    threshold: float

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "DocumentClassifier":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls(
            classes=tuple(data["classes"]),
            keep=frozenset(data["keep"]),
            intercepts=tuple(data["intercepts"]),
            features=data["features"],
            ngram_range=tuple(data["ngram_range"]),
            head_chars=data["head_chars"],
            threshold=data["threshold"],
        )

    def scores(self, text: str) -> dict[str, float]:
        """Per-class decision function, as the trained LinearSVC computes it."""
        counts: dict[str, int] = {}
        for g in char_wb_ngrams(text[: self.head_chars], *self.ngram_range):
            if g in self.features:
                counts[g] = counts.get(g, 0) + 1
        n = len(self.classes)
        totals = [0.0] * n
        norm_sq = 0.0
        for g, tf in counts.items():
            row = self.features[g]
            value = (1.0 + math.log(tf)) * row[0]
            norm_sq += value * value
            for i in range(n):
                totals[i] += value * row[i + 1]
        norm = math.sqrt(norm_sq) or 1.0
        return {
            c: totals[i] / norm + self.intercepts[i] for i, c in enumerate(self.classes)
        }

    def margin(self, text: str) -> float:
        """How much more the document looks non-procurement than procurement (>0: non)."""
        s = self.scores(text)
        skip = max(v for c, v in s.items() if c not in self.keep)
        keep = max(v for c, v in s.items() if c in self.keep)
        return skip - keep

    def is_confidently_non_procurement(self, text: str) -> bool:
        return self.margin(text) > self.threshold


@functools.cache
def default_classifier() -> DocumentClassifier:
    return DocumentClassifier.load()
