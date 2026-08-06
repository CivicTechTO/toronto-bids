"""Tests for the extraction orchestrator — offline, fixture-based, no network."""

import json

import pytest

from toronto_bids.extraction import CORPORA, extract_corpus, load_classification_labels


@pytest.fixture
def labels_file(tmp_path):
    data = {
        "labels": [
            {
                "url": "https://example.com/procurement.pdf",
                "contains_bid_or_award": True,
            },
            {
                "url": "https://example.com/governance.pdf",
                "contains_bid_or_award": False,
            },
        ]
    }
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(data))
    return path


class FakeClient:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"contracts": []}

    def extract(self, text):
        self.calls.append(text)
        return self.result


# ── classification gate ──


def test_load_labels_builds_url_to_flag_dict(labels_file):
    labels = load_classification_labels(labels_file)
    assert labels["https://example.com/procurement.pdf"] is True
    assert labels["https://example.com/governance.pdf"] is False


def test_load_labels_returns_empty_dict_when_file_missing(tmp_path):
    labels = load_classification_labels(tmp_path / "nonexistent.json")
    assert labels == {}


# ── corpus definitions ──


def test_all_six_corpora_are_defined():
    assert set(CORPORA.keys()) == {
        "trca",
        "ep",
        "zoo",
        "award_summary",
        "committee",
        "composite",
    }


def test_unknown_corpus_raises(conn):
    client = FakeClient()
    with pytest.raises(ValueError, match="Unknown corpus 'bogus'"):
        extract_corpus(conn, "bogus", client=client, labels={})


# ── orchestrator ──


def test_extract_corpus_skips_false_classification(conn):
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/governance.pdf', 'agency_board', 'aaa', 'some text')"
    )
    conn.commit()

    labels = {"https://example.com/governance.pdf": False}
    client = FakeClient()
    stats = extract_corpus(
        conn,
        "trca",
        client=client,
        labels=labels,
        where="kind='agency_board'",
    )
    assert client.calls == []
    assert stats["skipped_classification"] == 1


def test_extract_corpus_extracts_true_classification(conn):
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/procurement.pdf', 'agency_board', 'bbb', 'contract text')"
    )
    conn.commit()

    labels = {"https://example.com/procurement.pdf": True}
    client = FakeClient(
        {"contracts": [{"reference": "RFT 123", "bids": [], "awards": []}]}
    )
    stats = extract_corpus(
        conn,
        "trca",
        client=client,
        labels=labels,
        where="kind='agency_board'",
    )
    assert len(client.calls) == 1
    assert stats["extracted"] == 1


def test_extract_corpus_extracts_unlabeled_docs(conn):
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/unknown.pdf', 'agency_board', 'ccc', 'mystery text')"
    )
    conn.commit()

    client = FakeClient()
    extract_corpus(
        conn,
        "trca",
        client=client,
        labels={},
        where="kind='agency_board'",
    )
    assert len(client.calls) == 1


def test_extract_corpus_skips_cached_documents(conn):
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import mark_extracted

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/done.pdf', 'agency_board', 'ddd', 'already done')"
    )
    conn.commit()
    mark_extracted(conn, "ddd", EXTRACTOR_VERSION, result_json='{"contracts": []}')

    client = FakeClient()
    stats = extract_corpus(
        conn,
        "trca",
        client=client,
        labels={},
        where="kind='agency_board'",
    )
    assert client.calls == []
    assert stats["cached"] == 1


def test_extract_corpus_skips_documents_without_text(conn):
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/empty.pdf', 'agency_board', 'eee', NULL)"
    )
    conn.commit()

    client = FakeClient()
    stats = extract_corpus(
        conn,
        "trca",
        client=client,
        labels={},
        where="kind='agency_board'",
    )
    assert client.calls == []
    assert stats["no_text"] == 1


def test_extract_corpus_respects_limit(conn):
    for i in range(5):
        conn.execute(
            "INSERT INTO background_pdf (url, kind, sha256, text) "
            f"VALUES ('https://example.com/{i}.pdf', 'agency_board', 'sha{i}', 'text {i}')"
        )
    conn.commit()

    client = FakeClient()
    stats = extract_corpus(
        conn,
        "trca",
        client=client,
        labels={},
        where="kind='agency_board'",
        limit=2,
    )
    assert len(client.calls) == 2
    assert stats["extracted"] == 2


def test_extract_corpus_stores_result_in_cache(conn):
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import get_extraction

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/new.pdf', 'agency_board', 'fff', 'new doc')"
    )
    conn.commit()

    result = {"contracts": [{"reference": "RFT 999", "bids": [], "awards": []}]}
    client = FakeClient(result)
    extract_corpus(
        conn,
        "trca",
        client=client,
        labels={},
        where="kind='agency_board'",
    )

    cached = get_extraction(conn, "fff", EXTRACTOR_VERSION)
    assert cached is not None
    assert json.loads(cached)["contracts"][0]["reference"] == "RFT 999"


# ── ground-truth validation ──


def _seed_gt_doc(conn, url, sha256, extraction_result):
    """Insert a background_pdf row and cache an extraction result for it."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import mark_extracted

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES (?, 'agency_board', ?, 'text')",
        (url, sha256),
    )
    conn.commit()
    mark_extracted(
        conn, sha256, EXTRACTOR_VERSION, result_json=json.dumps(extraction_result)
    )


def test_validate_perfect_recall(conn, tmp_path):
    from toronto_bids.extraction import validate_against_ground_truth

    gt = {
        "labeller": "test",
        "documents": [
            {
                "id": "D01",
                "url": "https://example.com/d01.pdf",
                "none_present": False,
                "completed": True,
                "entries": [
                    {
                        "company": "Acme Ltd.",
                        "amount": "$100",
                        "outcome": "won",
                        "contract": "RFT 123",
                    },
                    {
                        "company": "Beta Inc.",
                        "amount": "$200",
                        "outcome": "lost",
                        "contract": "RFT 123",
                    },
                ],
            }
        ],
    }
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps(gt))

    extraction = {
        "contracts": [
            {
                "reference": "RFT 123",
                "bids": [
                    {
                        "supplier_name": "Beta Inc.",
                        "amount_raw": "$200",
                        "status": "compliant",
                    },
                ],
                "awards": [
                    {"supplier_name": "Acme Ltd.", "amount_raw": "$100"},
                ],
            }
        ]
    }
    _seed_gt_doc(conn, "https://example.com/d01.pdf", "sha_d01", extraction)

    result = validate_against_ground_truth(conn, gt_path)
    assert result["aggregate"]["recall"] == 1.0
    assert result["aggregate"]["precision"] == 1.0
    assert result["aggregate"]["fn"] == 0


def test_validate_missed_bid_lowers_recall(conn, tmp_path):
    from toronto_bids.extraction import validate_against_ground_truth

    gt = {
        "labeller": "test",
        "documents": [
            {
                "id": "D01",
                "url": "https://example.com/d01.pdf",
                "none_present": False,
                "completed": True,
                "entries": [
                    {
                        "company": "Acme Ltd.",
                        "amount": "$100",
                        "outcome": "won",
                        "contract": "RFT 123",
                    },
                    {
                        "company": "Beta Inc.",
                        "amount": "$200",
                        "outcome": "lost",
                        "contract": "RFT 123",
                    },
                ],
            }
        ],
    }
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps(gt))

    extraction = {
        "contracts": [
            {
                "reference": "RFT 123",
                "awards": [{"supplier_name": "Acme Ltd.", "amount_raw": "$100"}],
                "bids": [],
            }
        ]
    }
    _seed_gt_doc(conn, "https://example.com/d01.pdf", "sha_d01", extraction)

    result = validate_against_ground_truth(conn, gt_path)
    assert result["aggregate"]["recall"] == 0.5
    assert result["aggregate"]["fn"] == 1
    assert result["documents"][0]["missed"][0]["supplier"] == "beta inc."


def test_validate_not_extracted_is_reported(conn, tmp_path):
    from toronto_bids.extraction import validate_against_ground_truth

    gt = {
        "labeller": "test",
        "documents": [
            {
                "id": "D01",
                "url": "https://example.com/d01.pdf",
                "none_present": False,
                "completed": True,
                "entries": [
                    {"company": "X", "amount": "$1", "outcome": "won", "contract": "C1"}
                ],
            }
        ],
    }
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps(gt))

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/d01.pdf', 'agency_board', 'sha_d01', 'text')"
    )
    conn.commit()

    result = validate_against_ground_truth(conn, gt_path)
    assert result["documents"][0]["status"] == "not_extracted"
