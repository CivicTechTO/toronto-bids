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


def test_char_wb_ngrams_matches_scikit_learn():
    assert char_wb_ngrams("Hi", 3, 5) == [" hi", "hi ", " hi "]
    assert char_wb_ngrams("a  b", 3, 3) == [" a ", " b "]


def test_ground_truth_recall_at_shipped_threshold(ground_truth):
    clf = default_classifier()
    positives = [h for h, lab in ground_truth.values() if lab["contains_bid_or_award"]]
    negatives = [h for h, lab in ground_truth.values() if not lab["contains_bid_or_award"]]
    assert len(positives) == 25
    assert [h[:60] for h in positives if clf.is_confidently_non_procurement(h)] == []
    skipped = sum(clf.is_confidently_non_procurement(h) for h in negatives)
    assert skipped >= len(negatives) // 2


def _insert(conn, url, sha, text, kind="agency_board"):
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) VALUES (?, ?, ?, ?)",
        (url, kind, sha, text),
    )
    conn.commit()


TRCA = "https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId="


@pytest.mark.parametrize(
    "doc_kind, label, expected_calls, expected_clf_skip, expected_label_skip",
    [
        ("permit", None, 0, 1, 0),
        ("procurement", None, 1, 0, 0),
        ("permit", True, 1, 0, 0),
        ("procurement", False, 0, 0, 1),
    ],
)
def test_label_vs_classifier_precedence(
    conn, ground_truth, doc_kind, label, expected_calls, expected_clf_skip, expected_label_skip
):
    permit_head, lab = ground_truth["C053"]
    assert lab["kind"] == "permit_regulatory"
    text = permit_head if doc_kind == "permit" else PROCUREMENT_TEXT
    url = f"{TRCA}{doc_kind}_{label}"
    _insert(conn, url, f"sha_{doc_kind}_{label}", text)
    labels = {} if label is None else {url: label}
    client = FakeClient()
    logged = []

    stats = extract_corpus(conn, "trca", client=client, labels=labels, log=logged.append)

    assert len(client.calls) == expected_calls
    assert stats["skipped_classifier"] == expected_clf_skip
    assert stats["skipped_classification"] == expected_label_skip
    assert any("classifier: skipped" in m for m in logged) is bool(expected_clf_skip)


def test_award_summary_corpus_is_never_classifier_gated(conn, ground_truth):
    permit_head, _ = ground_truth["C053"]
    _insert(conn, "https://example.com/form.pdf", "s5", permit_head, kind="award_summary")
    client = FakeClient()

    stats = extract_corpus(conn, "award_summary", client=client, labels={})

    assert len(client.calls) == 1
    assert stats["skipped_classifier"] == 0


def test_count_uncached_agrees_with_extract_corpus(conn, ground_truth):
    permit_head, _ = ground_truth["C053"]
    _insert(conn, TRCA + "6", "s6", permit_head)
    _insert(conn, TRCA + "7", "s7", PROCUREMENT_TEXT)
    _insert(conn, TRCA + "8", "s8", PROCUREMENT_TEXT)
    labels = {TRCA + "8": False}

    uncached = _count_uncached(conn, "trca", labels)
    client = FakeClient()
    stats = extract_corpus(conn, "trca", client=client, labels=labels)

    assert uncached == stats["extracted"] == len(client.calls) == 1


def test_keyless_run_with_only_classifier_skipped_docs_does_not_raise(conn, monkeypatch, ground_truth):
    from toronto_bids.extraction import extract_and_backfill

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    permit_head, _ = ground_truth["C053"]
    _insert(conn, TRCA + "9", "s9", permit_head)
    logged = []

    extract_and_backfill(conn, "trca", log=logged.append)

    assert any("all documents cached" in m for m in logged)
