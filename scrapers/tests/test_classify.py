"""The runtime extraction-gate classifier (#229) — offline, no network.

The ground truth (`docs/ground-truth/document-classification/`) is in the repo, so the shipped
model is checked against it here. Those 125 documents are also its TRAINING set, so the
recall check below is in-sample: it proves the shipped weights and the pure-Python inference
reproduce the trained model, not that it generalizes. The out-of-fold measurement that chose
the threshold lives in `build-classifier.py` (30x repeated 2-fold CV: 750/750 bid/award
documents kept at threshold 0.3).
"""

import json
from pathlib import Path

import pytest

from toronto_bids.classify import char_wb_ngrams, default_classifier
from toronto_bids.extraction import _count_uncached, extract_corpus

GROUND_TRUTH = (
    Path(__file__).resolve().parents[2] / "docs" / "ground-truth" / "document-classification"
)


@pytest.fixture(scope="module")
def ground_truth():
    labels = {
        x["id"]: x for x in json.loads((GROUND_TRUTH / "labels-alex.json").read_text())["documents"]
    }
    docs = json.loads((GROUND_TRUTH / "documents.json").read_text())
    return {d["id"]: (d["head"], labels[d["id"]]) for d in docs if d["id"] in labels}


class FakeClient:
    def __init__(self):
        self.calls = []

    def extract(self, text):
        self.calls.append(text)
        return {"contracts": []}


PROCUREMENT_TEXT = (
    "STAFF REPORT\nAward of Contract - Request for Tender No. 10034756\n"
    "RECOMMENDATION: THAT the contract be awarded to Acme Construction Ltd., the lowest "
    "compliant bidder, at a total cost of $1,234,567.00, net of applicable taxes.\n"
    "Four (4) bids were received:\nAcme Construction Ltd. $1,234,567.00\n"
    "Beta Builders Inc. $1,300,000.00\nGamma Paving Ltd. $1,450,000.00\n"
)


# ── the model ──


def test_char_wb_ngrams_matches_scikit_learn():
    # scikit-learn's char_wb: words padded with spaces, n-grams never cross a word, and a
    # word shorter than n contributes itself once.
    assert char_wb_ngrams("Hi", 3, 5) == [" hi", "hi ", " hi "]
    assert char_wb_ngrams("a  b", 3, 3) == [" a ", " b "]


def test_shipped_threshold_is_the_measured_one():
    assert default_classifier().threshold == 0.3


def test_ground_truth_recall_at_shipped_threshold(ground_truth):
    """No human-flagged bid/award document is skipped (in-sample — see module docstring)."""
    clf = default_classifier()
    positives = [h for h, lab in ground_truth.values() if lab["contains_bid_or_award"]]
    negatives = [h for h, lab in ground_truth.values() if not lab["contains_bid_or_award"]]
    assert len(positives) == 25
    assert [h[:60] for h in positives if clf.is_confidently_non_procurement(h)] == []
    skipped = sum(clf.is_confidently_non_procurement(h) for h in negatives)
    # in-sample the gate skips nearly all of them; out of fold it is ~55% (build-classifier.py)
    assert skipped >= len(negatives) // 2


def test_procurement_text_is_not_skipped():
    assert not default_classifier().is_confidently_non_procurement(PROCUREMENT_TEXT)


# ── the gate in extract_corpus ──


def _insert(conn, url, sha, text, kind="agency_board"):
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) VALUES (?, ?, ?, ?)",
        (url, kind, sha, text),
    )
    conn.commit()


TRCA = "https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId="


def test_unlabelled_non_procurement_document_is_skipped(conn, ground_truth):
    permit_head, lab = ground_truth["C053"]
    assert lab["kind"] == "permit_regulatory"
    _insert(conn, TRCA + "1", "s1", permit_head)
    client = FakeClient()
    logged = []

    stats = extract_corpus(conn, "trca", client=client, labels={}, log=logged.append)

    assert client.calls == []
    assert stats["skipped_classifier"] == 1
    assert stats["skipped_classification"] == 0
    assert any("classifier: skipped" in m for m in logged)


def test_unlabelled_procurement_document_is_extracted(conn):
    _insert(conn, TRCA + "2", "s2", PROCUREMENT_TEXT)
    client = FakeClient()

    stats = extract_corpus(conn, "trca", client=client, labels={})

    assert len(client.calls) == 1
    assert stats["skipped_classifier"] == 0


def test_snapshot_true_wins_over_the_classifier(conn, ground_truth):
    permit_head, _ = ground_truth["C053"]
    _insert(conn, TRCA + "3", "s3", permit_head)
    client = FakeClient()

    stats = extract_corpus(conn, "trca", client=client, labels={TRCA + "3": True})

    assert len(client.calls) == 1
    assert stats["skipped_classifier"] == 0


def test_snapshot_false_wins_over_the_classifier(conn):
    _insert(conn, TRCA + "4", "s4", PROCUREMENT_TEXT)
    client = FakeClient()

    stats = extract_corpus(conn, "trca", client=client, labels={TRCA + "4": False})

    assert client.calls == []
    assert stats["skipped_classification"] == 1
    assert stats["skipped_classifier"] == 0


def test_award_summary_corpus_is_never_classifier_gated(conn, ground_truth):
    """An Award Summary Form exists only for an award: the classifier may not drop one."""
    permit_head, _ = ground_truth["C053"]
    _insert(conn, "https://example.com/form.pdf", "s5", permit_head, kind="award_summary")
    client = FakeClient()

    stats = extract_corpus(conn, "award_summary", client=client, labels={})

    assert len(client.calls) == 1
    assert stats["skipped_classifier"] == 0


def test_count_uncached_agrees_with_extract_corpus(conn, ground_truth):
    """#220: whatever the gate skips must not count as uncached."""
    permit_head, _ = ground_truth["C053"]
    _insert(conn, TRCA + "6", "s6", permit_head)  # classifier skips
    _insert(conn, TRCA + "7", "s7", PROCUREMENT_TEXT)  # extracted
    _insert(conn, TRCA + "8", "s8", PROCUREMENT_TEXT)  # snapshot False
    labels = {TRCA + "8": False}

    uncached = _count_uncached(conn, "trca", labels)
    client = FakeClient()
    stats = extract_corpus(conn, "trca", client=client, labels=labels)

    assert uncached == stats["extracted"] == len(client.calls) == 1


def test_keyless_run_with_only_classifier_skipped_docs_does_not_raise(conn, monkeypatch, ground_truth):
    from toronto_bids.extraction import extract_and_backfill

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    permit_head, _ = ground_truth["C053"]
    _insert(conn, TRCA + "9", "s9", permit_head)
    logged = []

    extract_and_backfill(conn, "trca", log=logged.append)

    assert any("all documents cached" in m for m in logged)
