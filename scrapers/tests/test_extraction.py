"""Tests for the extraction orchestrator — offline, fixture-based, no network."""

import json

import pytest

from toronto_bids.extraction import (
    CORPORA,
    check_declared_counts,
    extract_corpus,
    load_classification_labels,
)


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


def test_all_seven_corpora_are_defined():
    assert set(CORPORA.keys()) == {
        "trca",
        "ep",
        "zoo",
        "award_summary",
        "committee",
        "composite",
        "ba_report",
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


def test_extract_corpus_extracts_unlabeled_docs_without_a_classifier(conn):
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
        classifier=None,
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
        classifier=None,
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
        classifier=None,
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
        classifier=None,
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


# ── declared-count invariant ──


def test_check_declared_counts_shortfall():
    extraction = {
        "contracts": [
            {
                "reference": "RFT 123",
                "declared_submissions": 5,
                "bids": [
                    {"supplier_name": "A", "amount_raw": "$1"},
                    {"supplier_name": "B", "amount_raw": "$2"},
                    {"supplier_name": "C", "amount_raw": "$3"},
                ],
                "awards": [],
            }
        ]
    }
    flags = check_declared_counts(extraction)
    assert len(flags) == 1
    assert flags[0]["declared"] == 5
    assert flags[0]["actual"] == 3
    assert flags[0]["delta"] == -2


def test_check_declared_counts_overshoot_is_kept():
    extraction = {
        "contracts": [
            {
                "reference": "RFT 456",
                "declared_submissions": 2,
                "declared_compliant": 2,
                "bids": [
                    {"supplier_name": "A", "amount_raw": "$1", "status": "compliant"},
                    {"supplier_name": "B", "amount_raw": "$2", "status": "compliant"},
                    {
                        "supplier_name": "C",
                        "amount_raw": "$0",
                        "status": "non_compliant",
                    },
                ],
                "awards": [],
            }
        ]
    }
    flags = check_declared_counts(extraction)
    assert len(flags) == 0


def test_check_declared_counts_no_declared_count():
    extraction = {
        "contracts": [
            {
                "reference": "RFT 789",
                "declared_submissions": None,
                "bids": [{"supplier_name": "A", "amount_raw": "$1"}],
                "awards": [],
            }
        ]
    }
    flags = check_declared_counts(extraction)
    assert len(flags) == 0


def test_extract_corpus_stores_flags_on_shortfall(conn):
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import get_extraction

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/flagged.pdf', 'agency_board', 'ggg', 'text')"
    )
    conn.commit()

    result = {
        "contracts": [
            {
                "reference": "RFT 999",
                "declared_submissions": 5,
                "bids": [
                    {"supplier_name": "A", "amount_raw": "$1"},
                    {"supplier_name": "B", "amount_raw": "$2"},
                ],
                "awards": [],
            }
        ]
    }
    client = FakeClient(result)
    stats = extract_corpus(
        conn,
        "trca",
        client=client,
        labels={},
        where="kind='agency_board'",
        classifier=None,
    )
    assert stats["count_flags"] == 1

    cached = json.loads(get_extraction(conn, "ggg", EXTRACTOR_VERSION))
    assert len(cached["_flags"]) == 1
    assert cached["_flags"][0]["declared"] == 5
    assert cached["_flags"][0]["actual"] == 2


# ── document splitting ──


def test_split_document_small_passes_through():
    from toronto_bids.extraction import split_document

    chunks = split_document("short text", max_chars=1000)
    assert chunks == ["short text"]


def test_split_document_splits_on_double_newline():
    from toronto_bids.extraction import split_document

    sections = ["Section " + str(i) + "\n" + "x" * 200 for i in range(10)]
    text = "\n\n".join(sections)
    chunks = split_document(text, max_chars=500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 500


def test_extract_corpus_splits_large_doc_and_merges(conn):
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import get_extraction

    sections = ["x" * 200 for _ in range(10)]
    big_text = "\n\n".join(sections)

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/big.pdf', 'agency_board', 'hhh', ?)",
        (big_text,),
    )
    conn.commit()

    call_count = [0]
    results_per_call = [
        {
            "contracts": [
                {
                    "reference": "RFT A",
                    "bids": [{"supplier_name": "X", "amount_raw": "$1"}],
                    "awards": [],
                }
            ]
        },
        {
            "contracts": [
                {
                    "reference": "RFT B",
                    "bids": [{"supplier_name": "Y", "amount_raw": "$2"}],
                    "awards": [],
                }
            ]
        },
        {"contracts": []},
    ]

    class ChunkedClient:
        def extract(self, text):
            idx = min(call_count[0], len(results_per_call) - 1)
            call_count[0] += 1
            return results_per_call[idx]

    stats = extract_corpus(
        conn,
        "trca",
        client=ChunkedClient(),
        labels={},
        where="kind='agency_board'",
        max_chars=500,
        classifier=None,
    )
    assert stats["split"] >= 1
    assert call_count[0] > 1

    cached = json.loads(get_extraction(conn, "hhh", EXTRACTOR_VERSION))
    refs = [c["reference"] for c in cached["contracts"]]
    assert "RFT A" in refs
    assert "RFT B" in refs


# ── contract dedup ──


def test_dedup_contracts_merges_on_reference():
    from toronto_bids.extraction import dedup_contracts

    contracts = [
        {"reference": "RFT 1", "bids": [{"supplier_name": "A"}], "awards": []},
        {
            "reference": "RFT 1",
            "bids": [{"supplier_name": "A"}, {"supplier_name": "B"}],
            "awards": [],
        },
        {"reference": "RFT 2", "bids": [{"supplier_name": "C"}], "awards": []},
    ]
    result = dedup_contracts(contracts)
    assert len(result) == 2
    rft1 = next(c for c in result if c["reference"] == "RFT 1")
    assert len(rft1["bids"]) == 2


# ── backfill from extraction cache ──


def test_backfill_agency_bids_from_extraction(conn):
    """Backfill maps LLM extraction → agency_bid/agency_award rows."""
    from toronto_bids.extraction import backfill_from_extraction

    # Set up a buyer
    conn.execute("INSERT INTO buyer (id, slug, name) VALUES (2, 'trca', 'TRCA')")
    # Set up a background_pdf with a cached extraction
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://pub-trca.escribemeetings.com/report.pdf', 'agency_board', 'aaa', 'text')"
    )
    conn.commit()

    extraction = {
        "contracts": [
            {
                "reference": "10037330",
                "bids": [
                    {"supplier_name": "Acme Ltd.", "amount_raw": "$100.00"},
                    {"supplier_name": "Beta Inc.", "amount_raw": "$200.00"},
                ],
                "awards": [
                    {"supplier_name": "Acme Ltd.", "amount_raw": "$100.00"},
                ],
            }
        ]
    }
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import mark_extracted

    mark_extracted(conn, "aaa", EXTRACTOR_VERSION, result_json=json.dumps(extraction))

    result = backfill_from_extraction(conn, "trca")
    assert result["bids_written"] == 2
    assert result["awards_written"] == 1

    bids = conn.execute(
        "SELECT bidder_name_raw, bid_price FROM agency_bid WHERE source='trca_board'"
    ).fetchall()
    assert len(bids) == 2
    names = {b["bidder_name_raw"] for b in bids}
    assert names == {"Acme Ltd.", "Beta Inc."}

    awards = conn.execute(
        "SELECT supplier_name_raw, award_amount FROM agency_award WHERE source='trca_board'"
    ).fetchall()
    assert len(awards) == 1
    assert awards[0]["supplier_name_raw"] == "Acme Ltd."


def test_backfill_bid_table_from_extraction(conn):
    """Backfill maps LLM extraction → bid rows for award_summary corpus."""
    from toronto_bids.extraction import backfill_from_extraction

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, document_number) "
        "VALUES ('https://example.com/form.pdf', 'award_summary', 'bbb', 'text', '5247418372')"
    )
    conn.commit()

    extraction = {
        "contracts": [
            {
                "reference": "RFT 999",
                "bids": [
                    {"supplier_name": "Alpha Co.", "amount_raw": "$500.00"},
                    {"supplier_name": "Gamma Inc.", "amount_raw": "$600.00"},
                ],
                "awards": [],
            }
        ]
    }
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import mark_extracted

    mark_extracted(conn, "bbb", EXTRACTOR_VERSION, result_json=json.dumps(extraction))

    result = backfill_from_extraction(conn, "award_summary")
    assert result["bids_written"] == 2

    bids = conn.execute(
        "SELECT bidder_name_raw, bid_price, document_number FROM bid "
        "WHERE source='award_summary'"
    ).fetchall()
    assert len(bids) == 2
    assert bids[0]["document_number"] == "5247418372"


def _cache_city_doc(conn, *, kind, sha, doc_num, extraction):
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import mark_extracted

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, document_number) "
        "VALUES (?, ?, ?, 'text', ?)",
        (f"https://example.com/{sha}.pdf", kind, sha, doc_num),
    )
    conn.commit()
    mark_extracted(conn, sha, EXTRACTOR_VERSION, result_json=json.dumps(extraction))


@pytest.mark.parametrize(
    "amount_basis, hst_basis",
    [
        ("including_HST", "including"),
        ("plus_HST", "excluding"),
        # "Net of all applicable taxes" is neither: the old header regex returned None for it
        # too, and rule 7 of the prompt says it must not be conflated with plus-HST.
        ("net_of_taxes", None),
        ("unknown", None),
        (None, None),
        ("something_new", None),
    ],
)
def test_backfill_maps_amount_basis_onto_hst_basis(conn, amount_basis, hst_basis):
    """#217: the model's amount_basis restores the bid's hst_basis; anything else is NULL."""
    from toronto_bids.extraction import backfill_from_extraction

    bid = {"supplier_name": "Alpha Co.", "amount_raw": "$500.00"}
    if amount_basis is not None:
        bid["amount_basis"] = amount_basis
    _cache_city_doc(
        conn, kind="award_summary", sha="hhh", doc_num="5247418372",
        extraction={"contracts": [{"reference": "RFT 999", "bids": [bid], "awards": []}]},
    )

    backfill_from_extraction(conn, "award_summary")

    row = conn.execute(
        "SELECT hst_basis, price_header FROM bid WHERE source='award_summary'"
    ).fetchone()
    assert row["hst_basis"] == hst_basis
    assert row["price_header"] is None  # the model returns no header text


def test_backfill_committee_keys_each_contract_on_its_own_document_number(conn):
    """#217: a two-contract committee report attaches each contract's bids to its own doc."""
    from toronto_bids.extraction import backfill_from_extraction

    _cache_city_doc(
        conn, kind="committee_award", sha="ccc", doc_num="1111122222",
        extraction={
            "contracts": [
                {
                    "reference": "Request for Tender No. 1111122222",
                    "bids": [{"supplier_name": "Alpha Co.", "amount_raw": "$1.00"}],
                },
                {
                    "reference": "Doc3333344444",
                    "bids": [
                        {"supplier_name": "Beta Inc.", "amount_raw": "$2.00"},
                        {"supplier_name": "Alpha Co.", "amount_raw": "$1.00"},
                    ],
                },
            ]
        },
    )

    result = backfill_from_extraction(conn, "committee")
    assert result["bids_written"] == 3

    got = sorted(
        (r["document_number"], r["bidder_name_raw"])
        for r in conn.execute(
            "SELECT document_number, bidder_name_raw FROM bid WHERE source='committee_award'"
        )
    )
    assert got == [
        ("1111122222", "Alpha Co."),
        ("3333344444", "Alpha Co."),
        ("3333344444", "Beta Inc."),
    ]


@pytest.mark.parametrize(
    "reference",
    [
        "",
        None,
        "RFP 999",
        # A trailing Contract No. is a different identifier; its digits push the string past
        # exactly 10, so the strict normalizer refuses rather than splicing the two together.
        "Doc3333344444, Contract No. 22TE-17WS",
        "Tender Call No. 317-2010, Contract No. 10TE-17WS",
        # A pre-Ariba Call Number strips to exactly 10 digits — a call number, not a doc.
        "Request for Quotation 3905-10-0097",
        "1111111111",  # placeholder denylist
    ],
)
def test_backfill_committee_unusable_reference_falls_back_to_pdf_document(conn, reference):
    from toronto_bids.extraction import backfill_from_extraction

    _cache_city_doc(
        conn, kind="committee_award", sha="ddd", doc_num="5555566666",
        extraction={
            "contracts": [
                {
                    "reference": reference,
                    "bids": [{"supplier_name": "Alpha Co.", "amount_raw": "$1.00"}],
                }
            ]
        },
    )

    backfill_from_extraction(conn, "committee")

    rows = conn.execute(
        "SELECT document_number FROM bid WHERE source='committee_award'"
    ).fetchall()
    assert [r["document_number"] for r in rows] == ["5555566666"]


def test_backfill_award_summary_ignores_contract_reference(conn):
    """One form is one document: its bids key on the form's own document number."""
    from toronto_bids.extraction import backfill_from_extraction

    _cache_city_doc(
        conn, kind="award_summary", sha="eee", doc_num="5247418372",
        extraction={
            "contracts": [
                {
                    "reference": "Doc3333344444",
                    "bids": [{"supplier_name": "Alpha Co.", "amount_raw": "$1.00"}],
                }
            ]
        },
    )

    backfill_from_extraction(conn, "award_summary")

    rows = conn.execute(
        "SELECT document_number FROM bid WHERE source='award_summary'"
    ).fetchall()
    assert [r["document_number"] for r in rows] == ["5247418372"]


def _cache_bgrd(conn, *, sha, reference, extraction):
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import mark_extracted

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, reference) "
        "VALUES (?, 'bgrd', ?, 'text', ?)",
        (f"https://example.com/{sha}.pdf", sha, reference),
    )
    conn.commit()
    mark_extracted(conn, sha, EXTRACTOR_VERSION, result_json=json.dumps(extraction))


def test_backfill_composite_awards_from_extraction(conn):
    """#216: a composite award keys on the NORMALIZED call number, keeps the raw, and carries
    the contract's title."""
    from toronto_bids.extraction import backfill_from_extraction

    _cache_bgrd(
        conn, sha="ccc", reference="2011.BD5.1",
        extraction={
            "contracts": [
                {
                    # The trailing Contract No. is a different identifier (CLAUDE.md, third
                    # keyspace) and must not survive into the key.
                    "reference": "Request for Tender No. 3905-10-0097, Contract No. 10TE-17WS",
                    "title": "Watermain Replacement on Main St",
                    "awards": [
                        {"supplier_name": "Builder Co.", "amount_raw": "$1,000,000.00"},
                    ],
                    "bids": [],
                }
            ]
        },
    )

    result = backfill_from_extraction(conn, "composite")
    assert result["awards_written"] == 1

    awards = conn.execute(
        "SELECT call_number, call_number_raw, title, supplier_name_raw, award_value, "
        "reference FROM composite_award"
    ).fetchall()
    assert len(awards) == 1
    assert awards[0]["call_number"] == "3905-10-0097"
    assert (
        awards[0]["call_number_raw"]
        == "Request for Tender No. 3905-10-0097, Contract No. 10TE-17WS"
    )
    assert awards[0]["title"] == "Watermain Replacement on Main St"
    assert awards[0]["supplier_name_raw"] == "Builder Co."
    assert awards[0]["reference"] == "2011.BD5.1"


@pytest.mark.parametrize("reference", ["", None, "RFP 999", "Contract No. 10TE-17WS"])
def test_backfill_composite_refuses_a_reference_that_is_not_a_call_number(conn, reference):
    """#216: refuse and log, never store the model's raw string as the key."""
    from toronto_bids.extraction import backfill_from_extraction

    _cache_bgrd(
        conn, sha="ccc", reference="2010.BD3.4",
        extraction={
            "contracts": [
                {
                    "reference": reference,
                    "title": "Something",
                    "awards": [{"supplier_name": "Refused Co.", "amount_raw": "$5.00"}],
                },
                {
                    "reference": "Tender Call No. 317-2010",
                    "awards": [{"supplier_name": "Kept Co.", "amount_raw": "$6.00"}],
                },
            ]
        },
    )
    logged = []

    result = backfill_from_extraction(conn, "composite", log=logged.append)

    assert result["awards_written"] == 1
    rows = conn.execute("SELECT call_number, supplier_name_raw FROM composite_award").fetchall()
    assert [(r["call_number"], r["supplier_name_raw"]) for r in rows] == [
        ("317-2010", "Kept Co.")
    ]
    assert any("refused 1 award" in m for m in logged)


@pytest.mark.parametrize(
    "reference, in_composite, in_ba_report",
    [
        ("2009.BD1.1", True, False),
        ("2011.BD5.1", True, False),
        ("2012.BD40.2", True, False),
        ("2017.BA3.1", False, True),
        ("2019.BA12.3", False, True),
        ("2025.BA190.4", False, True),
        # Bid Committee 2013-2016 agendas tabulate their own bids; their reports are in
        # neither corpus, exactly as before e936004.
        ("2014.BD20.1", False, False),
    ],
)
def test_composite_and_ba_report_corpora_partition_bgrd(conn, reference, in_composite, in_ba_report):
    """#216: composite is the 2009-2012 composite reports only, not every bgrd PDF."""
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, reference) "
        "VALUES ('https://example.com/r.pdf', 'bgrd', 'rrr', 'text', ?)",
        (reference,),
    )
    conn.commit()

    def members(corpus):
        return conn.execute(
            f"SELECT COUNT(*) FROM background_pdf WHERE {CORPORA[corpus]}"
        ).fetchone()[0]

    assert members("composite") == int(in_composite)
    assert members("ba_report") == int(in_ba_report)


def test_ba_report_bids_land_in_bid_not_composite_award(conn):
    """#216: a 2019 Bid Award Panel staff report is not a composite report. Its bids go to
    `bid` keyed on the council reference; its awards are already on the spine."""
    from toronto_bids.extraction import backfill_from_extraction

    _cache_bgrd(
        conn, sha="bab", reference="2019.BA12.3",
        extraction={
            "contracts": [
                {
                    "reference": "Request for Tender Doc3333344444",
                    "title": "Road resurfacing",
                    "bids": [
                        {"supplier_name": "Alpha Co.", "amount_raw": "$1.00",
                         "amount_basis": "plus_HST"},
                        {"supplier_name": "Beta Inc.", "amount_raw": "$2.00"},
                    ],
                    "awards": [{"supplier_name": "Alpha Co.", "amount_raw": "$1.00"}],
                },
                {
                    # The Call Number trap: strips to exactly 10 digits, is not a doc number.
                    "reference": "Request for Quotation 3905-10-0097",
                    "bids": [{"supplier_name": "Gamma Ltd.", "amount_raw": "$3.00"}],
                },
                {
                    "reference": None,
                    "bids": [{"supplier_name": "Delta LLC", "amount_raw": "$4.00"}],
                },
            ]
        },
    )

    assert backfill_from_extraction(conn, "composite")["awards_written"] == 0
    result = backfill_from_extraction(conn, "ba_report")

    assert result["bids_written"] == 4
    assert result["awards_written"] == 0
    assert conn.execute("SELECT COUNT(*) FROM composite_award").fetchone()[0] == 0
    got = sorted(
        tuple(r)
        for r in conn.execute(
            "SELECT bidder_name_raw, reference, document_number, hst_basis FROM bid "
            "WHERE source='ba_report'"
        )
    )
    assert got == [
        ("Alpha Co.", "2019.BA12.3", "3333344444", "excluding"),
        ("Beta Inc.", "2019.BA12.3", "3333344444", None),
        ("Delta LLC", "2019.BA12.3", None, None),
        ("Gamma Ltd.", "2019.BA12.3", None, None),
    ]


def test_ba_report_reuses_the_composite_era_cache(conn):
    """No API cost: these reports were extracted when they sat in `composite`, and the cache
    is keyed (sha256, extractor_version), so the new corpus has nothing to extract and the
    swap-coverage floor is met."""
    from toronto_bids.extraction import _count_uncached, backfill_from_extraction

    for i in range(3):
        _cache_bgrd(
            conn, sha=f"ba{i}", reference=f"2020.BA{i}.1",
            extraction={"contracts": [{"reference": "RFT", "bids": [{"supplier_name": f"B{i}"}]}]},
        )

    assert _count_uncached(conn, "ba_report", {}) == 0
    assert backfill_from_extraction(conn, "ba_report")["docs_processed"] == 3


def test_composite_rebuild_clears_ba_era_rows_from_the_pre_feed_keyspace(conn):
    """The rows the unscoped corpus wrote for BA-era reports go on the next rebuild."""
    from toronto_bids.extraction import backfill_from_extraction

    conn.execute(
        "INSERT INTO composite_award (call_number, reference, supplier_name_raw, source) "
        "VALUES ('Doc3333344444', '2019.BA12.3', 'Wrong Keyspace Inc.', "
        "'bid_committee_composite')"
    )
    _cache_bgrd(
        conn, sha="old", reference="2010.BD2.2",
        extraction={"contracts": [{"reference": "317-2010", "awards": [{"supplier_name": "X"}]}]},
    )

    backfill_from_extraction(conn, "composite")

    assert [r[0] for r in conn.execute("SELECT reference FROM composite_award")] == [
        "2010.BD2.2"
    ]


# ── backfill: derive first, delete only on success ──


def _award_summary_doc(conn, sha, doc_num, url):
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, document_number) "
        "VALUES (?, 'award_summary', ?, 'text', ?)",
        (url, sha, doc_num),
    )
    conn.commit()


def _cache(conn, sha, version, *bidders):
    from toronto_bids.store.db import mark_extracted

    result = {
        "contracts": [
            {"reference": "RFT 1", "bids": [{"supplier_name": b} for b in bidders]}
        ]
    }
    mark_extracted(conn, sha, version, result_json=json.dumps(result))


def _bidders(conn):
    return {
        r["bidder_name_raw"]
        for r in conn.execute("SELECT bidder_name_raw FROM bid WHERE source='award_summary'")
    }


def test_backfill_rebuild_drops_rows_no_longer_derived(conn):
    """A successful rebuild replaces the source's rows — stale ones go."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction

    _award_summary_doc(conn, "s1", "1111111111", "https://example.com/a.pdf")
    _cache(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "Stale Ltd.")
    backfill_from_extraction(conn, "award_summary")
    _cache(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.")

    backfill_from_extraction(conn, "award_summary")

    assert _bidders(conn) == {"Alpha Co."}


def test_backfill_empty_derived_set_deletes_nothing(conn):
    """No current-version extractions anywhere (cold cache): existing rows survive."""
    from toronto_bids.extraction import backfill_from_extraction

    conn.execute(
        "INSERT INTO bid (bidder_name_raw, document_number, source) "
        "VALUES ('Kept Inc.', '1111111111', 'award_summary')"
    )
    conn.commit()

    result = backfill_from_extraction(conn, "award_summary")

    assert result["bids_written"] == 0
    assert _bidders(conn) == {"Kept Inc."}


def test_backfill_refuses_partial_reextraction_after_version_bump(conn):
    """A prompt edit bumps EXTRACTOR_VERSION; until re-extraction catches up, the
    rebuild must not replace the archive with the fraction re-extracted so far."""
    import pytest

    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction

    for i in range(4):
        _award_summary_doc(conn, f"s{i}", f"{i}" * 10, f"https://example.com/{i}.pdf")
        _cache(conn, f"s{i}", "v0-old", f"Old Bidder {i}")
    conn.execute(
        "INSERT INTO bid (bidder_name_raw, document_number, source) VALUES "
        "('Old Bidder 0', '0000000000', 'award_summary'), "
        "('Old Bidder 1', '1111111111', 'award_summary'), "
        "('Old Bidder 2', '2222222222', 'award_summary'), "
        "('Old Bidder 3', '3333333333', 'award_summary')"
    )
    conn.commit()
    _cache(conn, "s0", EXTRACTOR_VERSION, "New Bidder 0")  # 1 of 4 re-extracted

    with pytest.raises(RuntimeError, match="1 of 4"):
        backfill_from_extraction(conn, "award_summary")

    assert _bidders(conn) == {f"Old Bidder {i}" for i in range(4)}


def test_backfill_proceeds_once_coverage_reaches_floor(conn):
    """A few documents failing re-extraction must not freeze the tables."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction

    for i in range(4):
        _award_summary_doc(conn, f"s{i}", f"{i}" * 10, f"https://example.com/{i}.pdf")
        _cache(conn, f"s{i}", "v0-old", f"Old Bidder {i}")
    for i in range(3):  # 3 of 4 re-extracted; s3 keeps failing
        _cache(conn, f"s{i}", EXTRACTOR_VERSION, f"New Bidder {i}")

    result = backfill_from_extraction(conn, "award_summary")

    assert result["bids_written"] == 3
    assert _bidders(conn) == {f"New Bidder {i}" for i in range(3)}


def test_backfill_rolls_back_when_the_swap_fails(conn, monkeypatch):
    """An error mid-insert must not leave the source's rows deleted."""
    import pytest

    import toronto_bids.store.db as db
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction

    _award_summary_doc(conn, "s1", "1111111111", "https://example.com/a.pdf")
    _cache(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "Beta Inc.")
    backfill_from_extraction(conn, "award_summary")

    _cache(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "Gamma Ltd.")
    real = db._rebuild_upsert
    calls = []

    def flaky(c, row):
        calls.append(row)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return real(c, row)

    monkeypatch.setattr(db, "_rebuild_upsert", flaky)
    with pytest.raises(RuntimeError, match="disk full"):
        backfill_from_extraction(conn, "award_summary")

    assert _bidders(conn) == {"Alpha Co.", "Beta Inc."}
    assert len(calls) == 2  # the failure really landed mid-swap


def test_backfill_rolls_back_when_the_sweep_fails(conn):
    """An error in the sweep, after every upsert, also leaves the table as it was."""
    import pytest

    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction

    _award_summary_doc(conn, "s1", "1111111111", "https://example.com/a.pdf")
    _cache(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "Beta Inc.")
    backfill_from_extraction(conn, "award_summary")
    _cache(conn, "s1", EXTRACTOR_VERSION, "Gamma Ltd.")

    class FailingSweep:
        """Delegates to the real connection, but the sweep's DELETE raises."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def execute(self, sql, *args):
            if sql.startswith("DELETE FROM bid WHERE"):
                raise RuntimeError("disk full")
            return self._real.execute(sql, *args)

    with pytest.raises(RuntimeError, match="disk full"):
        backfill_from_extraction(FailingSweep(conn), "award_summary")

    assert _bidders(conn) == {"Alpha Co.", "Beta Inc."}


# ── backfill: surviving rows keep their archive history (#218) ──

_OLD = "2020-01-01 00:00:00"


def _age_rows(conn, table):
    """Backdate every row so a rebuild that re-mints first_seen cannot pass by accident."""
    conn.execute(f"UPDATE {table} SET first_seen = ?, last_seen = ?", (_OLD, _OLD))
    conn.commit()


def _seen(conn, table, name_col, **where):
    clause = " AND ".join(f"{k} = ?" for k in where) or "1"
    return {
        r[name_col]: (r["id"], r["first_seen"], r["last_seen"])
        for r in conn.execute(
            f"SELECT id, {name_col}, first_seen, last_seen FROM {table} WHERE {clause}",
            tuple(where.values()),
        )
    }


def test_backfill_rebuild_keeps_first_seen_of_surviving_rows(conn):
    """A row derived again keeps its first_seen (and id) and gets a fresh last_seen; a
    row no longer derived is removed; a genuinely new row is stamped now. The bid key
    has a NULL part here (reference), which the COALESCE conflict target must match."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction

    _award_summary_doc(conn, "s1", "1111111111", "https://example.com/a.pdf")
    _cache(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "Stale Ltd.")
    backfill_from_extraction(conn, "award_summary")
    _age_rows(conn, "bid")
    before = _seen(conn, "bid", "bidder_name_raw", source="award_summary")
    assert conn.execute(
        "SELECT reference FROM bid WHERE bidder_name_raw = 'Alpha Co.'"
    ).fetchone()[0] is None

    _cache(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "New Inc.")
    backfill_from_extraction(conn, "award_summary")
    after = _seen(conn, "bid", "bidder_name_raw", source="award_summary")

    assert set(after) == {"Alpha Co.", "New Inc."}
    alpha_id, alpha_first, alpha_last = after["Alpha Co."]
    assert alpha_id == before["Alpha Co."][0]
    assert alpha_first == _OLD
    assert alpha_last > _OLD
    assert after["New Inc."][1] > _OLD


def test_backfill_rebuild_keeps_first_seen_with_null_key_parts(conn):
    """composite_award keys on COALESCE(award_value, ''): an award with no value must
    still be recognised as the same row on the next rebuild."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction
    from toronto_bids.store.db import mark_extracted

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, reference) "
        "VALUES ('https://example.com/bgrd.pdf', 'bgrd', 'ccc', 'text', '2011.BD5.1')"
    )
    conn.commit()
    extraction = {
        "contracts": [
            {
                "reference": "3905-10-0097",
                "awards": [{"supplier_name": "Builder Co.", "amount_raw": None}],
                "bids": [],
            }
        ]
    }
    mark_extracted(conn, "ccc", EXTRACTOR_VERSION, result_json=json.dumps(extraction))
    backfill_from_extraction(conn, "composite")
    _age_rows(conn, "composite_award")

    backfill_from_extraction(conn, "composite")

    rows = conn.execute(
        "SELECT supplier_name_raw, award_value, first_seen, last_seen FROM composite_award"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["award_value"] is None
    assert rows[0]["first_seen"] == _OLD
    assert rows[0]["last_seen"] > _OLD


def test_backfill_rebuild_keeps_first_seen_of_agency_rows(conn):
    """agency_bid (plain UNIQUE) and agency_award (expression key) both survive."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction
    from toronto_bids.store.db import mark_extracted

    conn.execute("INSERT INTO buyer (id, slug, name) VALUES (2, 'trca', 'TRCA')")
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) VALUES "
        "('https://pub-trca.escribemeetings.com/r.pdf', 'agency_board', 'aaa', 'text')"
    )
    conn.commit()
    extraction = {
        "contracts": [
            {
                "reference": "10037330",
                "bids": [{"supplier_name": "Acme Ltd.", "amount_raw": "$100.00"}],
                "awards": [{"supplier_name": "Acme Ltd.", "amount_raw": None}],
            }
        ]
    }
    mark_extracted(conn, "aaa", EXTRACTOR_VERSION, result_json=json.dumps(extraction))
    backfill_from_extraction(conn, "trca")
    _age_rows(conn, "agency_bid")
    _age_rows(conn, "agency_award")

    backfill_from_extraction(conn, "trca")

    for table in ("agency_bid", "agency_award"):
        rows = conn.execute(f"SELECT first_seen, last_seen FROM {table}").fetchall()
        assert len(rows) == 1, table
        assert rows[0]["first_seen"] == _OLD, table
        assert rows[0]["last_seen"] > _OLD, table


def test_backfill_rebuild_clears_a_column_the_derivation_no_longer_sets(conn):
    """A surviving row takes the derivation verbatim: a value the new extraction drops
    goes NULL, exactly as it would after a delete + reinsert."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction
    from toronto_bids.store.db import mark_extracted

    _award_summary_doc(conn, "s1", "1111111111", "https://example.com/a.pdf")

    def cache(basis):
        bid = {"supplier_name": "Alpha Co.", "amount_raw": "$5.00", "amount_basis": basis}
        result = {"contracts": [{"reference": "RFT 1", "bids": [bid]}]}
        mark_extracted(conn, "s1", EXTRACTOR_VERSION, result_json=json.dumps(result))

    cache("including_HST")
    backfill_from_extraction(conn, "award_summary")
    cache("unknown")
    backfill_from_extraction(conn, "award_summary")

    assert conn.execute("SELECT hst_basis FROM bid").fetchone()[0] is None


def test_backfill_rebuild_leaves_other_sources_alone(conn):
    """The sweep is scoped to the corpus's source; another source's rows are untouched."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction

    conn.execute(
        "INSERT INTO bid (bidder_name_raw, reference, source) "
        "VALUES ('Panel Bidder', '2018.BA1.1', 'bid_award_panel')"
    )
    conn.commit()
    _award_summary_doc(conn, "s1", "1111111111", "https://example.com/a.pdf")
    _cache(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.")

    backfill_from_extraction(conn, "award_summary")

    assert conn.execute(
        "SELECT COUNT(*) FROM bid WHERE source = 'bid_award_panel'"
    ).fetchone()[0] == 1


def test_extract_and_backfill_keyless_ignores_text_less_docs(conn, monkeypatch):
    """#220: a held doc with no text is never extracted, so it must not count as
    uncached — otherwise the keyless all-cached path re-raises forever."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import extract_and_backfill
    from toronto_bids.store.db import mark_extracted

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, reference) "
        "VALUES ('https://example.com/has-text.pdf', 'bgrd', 'ttt', 'text', '2011.BD5.1')"
    )
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, reference) "
        "VALUES ('https://example.com/image-only.pdf', 'bgrd', 'iii', NULL, '2011.BD5.2')"
    )
    conn.commit()
    extraction = {
        "contracts": [
            {
                "reference": "3905-10-0097",
                "awards": [{"supplier_name": "Builder Co.", "amount_raw": "$1,000.00"}],
                "bids": [],
            }
        ]
    }
    mark_extracted(conn, "ttt", EXTRACTOR_VERSION, result_json=json.dumps(extraction))

    result = extract_and_backfill(conn, "composite")

    assert result["awards_written"] == 1


# ── malformed model responses (#219) ──


def _one_contract(declared):
    return {
        "contracts": [
            {
                "reference": "RFT 1",
                "declared_submissions": declared,
                "bids": [{"supplier_name": "A", "amount_raw": "$1"}],
                "awards": [],
            }
        ]
    }


def test_check_declared_counts_coerces_a_numeric_string():
    """A model that returns `"8"` instead of `8` is still a shortfall of 7, not a crash."""
    flags = check_declared_counts(_one_contract("8"))
    assert len(flags) == 1
    assert flags[0]["declared"] == 8
    assert flags[0]["delta"] == -7


@pytest.mark.parametrize("declared", ["eight", "", "8.5", True, [8], {"n": 8}])
def test_check_declared_counts_ignores_a_non_numeric_declared_count(declared):
    assert check_declared_counts(_one_contract(declared)) == []


class _SequenceClient:
    """Returns (or raises) one scripted response per call, in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def extract(self, text):
        self.calls.append(text)
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def test_one_bad_response_does_not_block_the_rest_of_the_corpus(conn):
    """#219: a TypeError from one document's response used to escape extract_corpus, so
    every uncached document after it (ORDER BY url) was blocked on every run."""
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.store.db import is_extracted

    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/a.pdf', 'agency_board', 'a1', 'text a')"
    )
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/b.pdf', 'agency_board', 'b1', 'text b')"
    )
    conn.commit()
    client = _SequenceClient(TypeError("unexpected shape"), {"contracts": []})
    logged = []

    stats = extract_corpus(
        conn,
        "trca",
        client=client,
        labels={},
        where="kind='agency_board'",
        log=logged.append,
        classifier=None,
    )

    assert stats["errors"] == 1
    assert stats["extracted"] == 1
    assert client.calls == ["text a", "text b"]
    assert not is_extracted(conn, "a1", EXTRACTOR_VERSION)
    assert is_extracted(conn, "b1", EXTRACTOR_VERSION)
    assert any("FAILED https://example.com/a.pdf" in m for m in logged)


def test_string_declared_count_in_a_response_is_extracted_not_crashed(conn):
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text) "
        "VALUES ('https://example.com/s.pdf', 'agency_board', 's1', 'text')"
    )
    conn.commit()
    stats = extract_corpus(
        conn,
        "trca",
        client=FakeClient(_one_contract("8")),
        labels={},
        where="kind='agency_board'",
        classifier=None,
    )
    assert stats["extracted"] == 1
    assert stats["errors"] == 0
    assert stats["count_flags"] == 1


# ── store functions forward `log` (#219) ──


@pytest.mark.parametrize(
    "module, fn, corpus",
    [
        ("toronto_bids.sources.trca_board", "store_trca_reports", "trca"),
        ("toronto_bids.sources.zoo_board", "store_zoo_reports", "zoo"),
        ("toronto_bids.sources.ep_board", "store_ep_reports", "ep"),
        ("toronto_bids.sources.bid_award_panel", "store_composite_awards", "composite"),
        ("toronto_bids.sources.bid_award_panel", "store_ba_report_bids", "ba_report"),
    ],
)
def test_store_functions_forward_log_to_extraction(monkeypatch, module, fn, corpus):
    import importlib

    import toronto_bids.extraction as extraction

    seen = {}

    def fake_extract_and_backfill(
        conn, got_corpus, *, log=lambda _m: None, failures=None
    ):
        seen["corpus"] = got_corpus
        seen["failures"] = failures
        log("FAILED something")
        return {"solicitations_written": 1, "awards_written": 2, "bids_written": 3}

    monkeypatch.setattr(extraction, "extract_and_backfill", fake_extract_and_backfill)
    logged = []
    result = getattr(importlib.import_module(module), fn)(None, log=logged.append)

    assert seen["corpus"] == corpus
    assert seen["failures"] is None  # default keeps non-nightly callers unchanged
    assert logged == ["FAILED something"]
    if isinstance(result, dict):
        assert result["bids"] == 3

    failures = []
    getattr(importlib.import_module(module), fn)(None, failures=failures)
    assert seen["failures"] is failures


# ── extraction errors reach the caller's failures list (#219) ──


class _FailingClient:
    def extract(self, text):
        raise ValueError("model retired")


def _two_award_summary_docs(conn):
    for i in range(2):
        conn.execute(
            "INSERT INTO background_pdf (url, kind, sha256, text) "
            f"VALUES ('https://example.com/form{i}.pdf', 'award_summary', 's{i}', 'text')"
        )
    conn.commit()


def test_extract_and_backfill_appends_one_entry_per_corpus_on_errors(conn, monkeypatch):
    import toronto_bids.extract as extract_mod
    from toronto_bids.extraction import extract_and_backfill

    monkeypatch.setattr(extract_mod, "ExtractionClient", _FailingClient)
    _two_award_summary_docs(conn)
    failures = []
    logged = []

    extract_and_backfill(conn, "award_summary", log=logged.append, failures=failures)

    assert failures == [("extract:award_summary", "2 of 2 documents failed extraction")]
    # per-document lines are still logged; the summary line carries the flag count
    assert sum(m.startswith("  FAILED") for m in logged) == 2
    assert any("2 errors, 0 count flags" in m for m in logged)


def test_extract_and_backfill_appends_nothing_without_errors(conn, monkeypatch):
    import toronto_bids.extract as extract_mod
    from toronto_bids.extraction import extract_and_backfill

    monkeypatch.setattr(extract_mod, "ExtractionClient", lambda: FakeClient())
    _two_award_summary_docs(conn)
    failures = []

    extract_and_backfill(conn, "award_summary", failures=failures)

    assert failures == []


def test_extract_and_backfill_without_failures_list_still_returns(conn, monkeypatch):
    import toronto_bids.extract as extract_mod
    from toronto_bids.extraction import extract_and_backfill

    monkeypatch.setattr(extract_mod, "ExtractionClient", _FailingClient)
    _two_award_summary_docs(conn)

    assert extract_and_backfill(conn, "award_summary")["bids_written"] == 0
