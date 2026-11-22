import json

import pytest

from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import (
    CORPORA,
    backfill_from_extraction,
    check_declared_counts,
    corpus_validation_report,
    dedup_contracts,
    extract_and_backfill,
    extract_corpus,
    load_classification_labels,
    split_document,
    validate_against_ground_truth,
)
from toronto_bids.store.db import get_extraction, is_extracted, mark_extracted


class FakeClient:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"contracts": []}

    def extract(self, text):
        self.calls.append(text)
        return self.result


class _SequenceClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def extract(self, text):
        self.calls.append(text)
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class _FailingClient:
    def extract(self, text):
        raise ValueError("model retired")


def _insert_pdf(conn, *, url, sha, text="text", kind="agency_board", doc_num=None, reference=None):
    conn.execute(
        "INSERT INTO background_pdf (url, kind, sha256, text, document_number, reference) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (url, kind, sha, text, doc_num, reference),
    )
    conn.commit()


def _cache_doc(conn, *, url, sha, extraction, kind="agency_board", text="text", doc_num=None, reference=None, version=EXTRACTOR_VERSION):
    _insert_pdf(conn, url=url, sha=sha, text=text, kind=kind, doc_num=doc_num, reference=reference)
    mark_extracted(conn, sha, version, result_json=json.dumps(extraction))


def _write_gt(tmp_path, entries, url="https://example.com/d01.pdf"):
    gt = {
        "labeller": "test",
        "documents": [
            {
                "id": "D01",
                "url": url,
                "none_present": False,
                "completed": True,
                "entries": entries,
            }
        ],
    }
    path = tmp_path / "gt.json"
    path.write_text(json.dumps(gt))
    return path


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


def _cache_award_summary(conn, sha, version, *bidders):
    result = {"contracts": [{"reference": "RFT 1", "bids": [{"supplier_name": b} for b in bidders]}]}
    mark_extracted(conn, sha, version, result_json=json.dumps(result))


def _award_summary_bidders(conn):
    return {
        r["bidder_name_raw"]
        for r in conn.execute("SELECT bidder_name_raw FROM bid WHERE source='award_summary'")
    }


def test_load_classification_labels(tmp_path):
    assert load_classification_labels(tmp_path / "nonexistent.json") == {}
    path = tmp_path / "labels.json"
    path.write_text(
        json.dumps(
            {
                "labels": [
                    {"url": "https://example.com/procurement.pdf", "contains_bid_or_award": True},
                    {"url": "https://example.com/governance.pdf", "contains_bid_or_award": False},
                ]
            }
        )
    )
    assert load_classification_labels(path) == {
        "https://example.com/procurement.pdf": True,
        "https://example.com/governance.pdf": False,
    }


def test_unknown_corpus_raises(conn):
    with pytest.raises(ValueError, match="Unknown corpus 'bogus'"):
        extract_corpus(conn, "bogus", client=FakeClient(), labels={})


@pytest.mark.parametrize(
    "text, label, precache, expected_calls, stat_key",
    [
        ("some text", False, False, 0, "skipped_classification"),
        ("contract text", True, False, 1, "extracted"),
        ("mystery text", None, False, 1, "extracted"),
        ("already done", None, True, 0, "cached"),
        (None, None, False, 0, "no_text"),
    ],
)
def test_extract_corpus_gating_and_caching(conn, text, label, precache, expected_calls, stat_key):
    url = "https://example.com/doc.pdf"
    _insert_pdf(conn, url=url, sha="sha1", text=text)
    if precache:
        mark_extracted(conn, "sha1", EXTRACTOR_VERSION, result_json='{"contracts": []}')

    labels = {} if label is None else {url: label}
    result = {"contracts": [{"reference": "RFT 999", "bids": [], "awards": []}]}
    client = FakeClient(result)
    stats = extract_corpus(
        conn,
        "trca",
        client=client,
        labels=labels,
        where="kind='agency_board'",
        classifier=None,
    )

    assert len(client.calls) == expected_calls
    assert stats[stat_key] == 1
    if stat_key == "extracted":
        cached = json.loads(get_extraction(conn, "sha1", EXTRACTOR_VERSION))
        assert cached["contracts"][0]["reference"] == "RFT 999"


def test_extract_corpus_respects_limit(conn):
    for i in range(5):
        _insert_pdf(conn, url=f"https://example.com/{i}.pdf", sha=f"sha{i}", text=f"text {i}")

    client = FakeClient()
    stats = extract_corpus(
        conn, "trca", client=client, labels={}, where="kind='agency_board'", limit=2, classifier=None
    )
    assert len(client.calls) == 2
    assert stats["extracted"] == 2


def test_validate_against_ground_truth(conn, tmp_path):
    entries = [
        {"company": "Acme Ltd.", "amount": "$100", "outcome": "won", "contract": "RFT 123"},
        {"company": "Beta Inc.", "amount": "$200", "outcome": "lost", "contract": "RFT 123"},
    ]
    gt_path = _write_gt(tmp_path, entries)

    _insert_pdf(conn, url="https://example.com/d01.pdf", sha="sha_d01")
    unextracted = validate_against_ground_truth(conn, gt_path)
    assert unextracted["documents"][0]["status"] == "not_extracted"

    partial = {
        "contracts": [
            {"reference": "RFT 123", "awards": [{"supplier_name": "Acme Ltd.", "amount_raw": "$100"}], "bids": []}
        ]
    }
    mark_extracted(conn, "sha_d01", EXTRACTOR_VERSION, result_json=json.dumps(partial))
    missed = validate_against_ground_truth(conn, gt_path)
    assert missed["aggregate"]["recall"] == 0.5
    assert missed["aggregate"]["fn"] == 1
    assert missed["documents"][0]["missed"][0]["supplier"] == "beta inc."

    full = {
        "contracts": [
            {
                "reference": "RFT 123",
                "bids": [{"supplier_name": "Beta Inc.", "amount_raw": "$200", "status": "compliant"}],
                "awards": [{"supplier_name": "Acme Ltd.", "amount_raw": "$100"}],
            }
        ]
    }
    mark_extracted(conn, "sha_d01", EXTRACTOR_VERSION, result_json=json.dumps(full))
    perfect = validate_against_ground_truth(conn, gt_path)
    assert perfect["aggregate"]["recall"] == 1.0
    assert perfect["aggregate"]["precision"] == 1.0
    assert perfect["aggregate"]["fn"] == 0


def test_corpus_validation_report_metrics_and_unknown_corpus(conn):
    with pytest.raises(ValueError, match="Unknown corpus 'bogus'"):
        corpus_validation_report(conn, "bogus")

    conn.execute("INSERT INTO buyer (id, slug, name) VALUES (2, 'trca', 'TRCA')")
    url = "https://pub-trca.escribemeetings.com/r1.pdf"
    _cache_doc(
        conn,
        url=url,
        sha="r1",
        extraction={
            "contracts": [
                {
                    "reference": "RFT 1",
                    "bids": [{"supplier_name": "Acme Ltd."}],
                    "awards": [{"supplier_name": "Extra Co."}],
                }
            ],
            "_flags": [{"reference": "RFT 1", "declared": 3, "actual": 1, "delta": -2}],
        },
    )
    conn.execute(
        "INSERT INTO agency_bid (buyer_id, native_ref, bidder_name_raw, report_url, source) "
        "VALUES (2, 'RFT 1', 'Acme Ltd.', ?, 'trca_board'), (2, 'RFT 1', 'Missed Inc.', ?, 'trca_board')",
        (url, url),
    )
    conn.commit()

    report = corpus_validation_report(conn, "trca")
    assert report["extracted_docs"] == 1
    assert report["docs_with_content"] == 1
    assert report["count_flags"] == 1
    assert report["llm"] == {"contracts": 1, "bids": 1, "awards": 1, "unique_names": 2}
    assert report["incumbent"] == {"total_names": 2, "docs_joinable": 1}
    assert report["comparison"] == {
        "shared_docs": 1,
        "tp": 1,
        "fn": 1,
        "fp": 1,
        "recall": 0.5,
        "precision": 0.5,
    }


@pytest.mark.parametrize(
    "extraction, expected_delta",
    [
        (
            {
                "contracts": [
                    {
                        "reference": "RFT 123",
                        "declared_submissions": 5,
                        "bids": [{"supplier_name": "A"}, {"supplier_name": "B"}, {"supplier_name": "C"}],
                        "awards": [],
                    }
                ]
            },
            -2,
        ),
        (_one_contract("8"), -7),
        (
            {
                "contracts": [
                    {
                        "reference": "RFT 456",
                        "declared_submissions": 2,
                        "declared_compliant": 2,
                        "bids": [
                            {"supplier_name": "A", "status": "compliant"},
                            {"supplier_name": "B", "status": "compliant"},
                            {"supplier_name": "C", "status": "non_compliant"},
                        ],
                        "awards": [],
                    }
                ]
            },
            None,
        ),
        (_one_contract(None), None),
        (_one_contract("eight"), None),
        (_one_contract(""), None),
        (_one_contract("8.5"), None),
        (_one_contract(True), None),
        (_one_contract([8]), None),
        (_one_contract({"n": 8}), None),
    ],
)
def test_check_declared_counts(extraction, expected_delta):
    flags = check_declared_counts(extraction)
    if expected_delta is None:
        assert flags == []
    else:
        assert len(flags) == 1
        assert flags[0]["delta"] == expected_delta


def test_extract_corpus_stores_flags_on_shortfall(conn):
    _insert_pdf(conn, url="https://example.com/flagged.pdf", sha="ggg")
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

    cached = json.loads(get_extraction(conn, "ggg", EXTRACTOR_VERSION))
    assert cached["_flags"][0]["declared"] == 8
    assert cached["_flags"][0]["actual"] == 1


@pytest.mark.parametrize(
    "text, max_chars, expect_split",
    [
        ("short text", 1000, False),
        ("\n\n".join(f"Section {i}\n" + "x" * 200 for i in range(10)), 500, True),
    ],
)
def test_split_document(text, max_chars, expect_split):
    chunks = split_document(text, max_chars=max_chars)
    if expect_split:
        assert len(chunks) > 1
        assert all(len(c) <= max_chars for c in chunks)
    else:
        assert chunks == [text]


def test_extract_corpus_splits_large_doc_and_merges(conn):
    big_text = "\n\n".join("x" * 200 for _ in range(10))
    _insert_pdf(conn, url="https://example.com/big.pdf", sha="hhh", text=big_text)

    client = _SequenceClient(
        {"contracts": [{"reference": "RFT A", "bids": [{"supplier_name": "X", "amount_raw": "$1"}], "awards": []}]},
        {"contracts": [{"reference": "RFT B", "bids": [{"supplier_name": "Y", "amount_raw": "$2"}], "awards": []}]},
        {"contracts": []},
        {"contracts": []},
        {"contracts": []},
    )
    stats = extract_corpus(
        conn, "trca", client=client, labels={}, where="kind='agency_board'", max_chars=500, classifier=None
    )
    assert stats["split"] >= 1
    assert len(client.calls) > 1

    cached = json.loads(get_extraction(conn, "hhh", EXTRACTOR_VERSION))
    refs = {c["reference"] for c in cached["contracts"]}
    assert {"RFT A", "RFT B"} <= refs


def test_dedup_contracts_merges_on_reference_and_preserves_unreferenced():
    contracts = [
        {"reference": "RFT 1", "bids": [{"supplier_name": "A"}], "awards": []},
        {"reference": "RFT 1", "bids": [{"supplier_name": "A"}, {"supplier_name": "B"}], "awards": []},
        {"reference": "RFT 2", "bids": [{"supplier_name": "C"}], "awards": []},
        {"reference": None, "bids": [{"supplier_name": "D"}], "awards": []},
        {"reference": "", "bids": [{"supplier_name": "E"}], "awards": []},
    ]
    result = dedup_contracts(contracts)
    assert len(result) == 4
    rft1 = next(c for c in result if c["reference"] == "RFT 1")
    assert len(rft1["bids"]) == 2
    no_ref_names = [c["bids"][0]["supplier_name"] for c in result if not c.get("reference")]
    assert no_ref_names == ["D", "E"]


def test_backfill_agency_bids_from_extraction(conn):
    conn.execute("INSERT INTO buyer (id, slug, name) VALUES (2, 'trca', 'TRCA')")
    _cache_doc(
        conn,
        url="https://pub-trca.escribemeetings.com/report.pdf",
        sha="aaa",
        extraction={
            "contracts": [
                {
                    "reference": "10037330",
                    "bids": [
                        {"supplier_name": "Acme Ltd.", "amount_raw": "$100.00"},
                        {"supplier_name": "Beta Inc.", "amount_raw": "$200.00"},
                    ],
                    "awards": [{"supplier_name": "Acme Ltd.", "amount_raw": "$100.00"}],
                }
            ]
        },
    )

    result = backfill_from_extraction(conn, "trca")
    assert result["bids_written"] == 2
    assert result["awards_written"] == 1
    names = {r["bidder_name_raw"] for r in conn.execute("SELECT bidder_name_raw FROM agency_bid WHERE source='trca_board'")}
    assert names == {"Acme Ltd.", "Beta Inc."}
    awards = conn.execute("SELECT supplier_name_raw FROM agency_award WHERE source='trca_board'").fetchall()
    assert [a["supplier_name_raw"] for a in awards] == ["Acme Ltd."]


def test_backfill_bid_table_from_extraction(conn):
    _cache_doc(
        conn,
        url="https://example.com/form.pdf",
        kind="award_summary",
        sha="bbb",
        doc_num="5247418372",
        extraction={
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
        },
    )

    result = backfill_from_extraction(conn, "award_summary")
    assert result["bids_written"] == 2
    bids = conn.execute("SELECT document_number FROM bid WHERE source='award_summary'").fetchall()
    assert len(bids) == 2
    assert bids[0]["document_number"] == "5247418372"


@pytest.mark.parametrize(
    "amount_basis, hst_basis",
    [
        ("including_HST", "including"),
        ("plus_HST", "excluding"),
        ("net_of_taxes", None),
        ("unknown", None),
        (None, None),
        ("something_new", None),
    ],
)
def test_backfill_maps_amount_basis_onto_hst_basis(conn, amount_basis, hst_basis):
    bid = {"supplier_name": "Alpha Co.", "amount_raw": "$500.00"}
    if amount_basis is not None:
        bid["amount_basis"] = amount_basis
    _cache_doc(
        conn,
        url="https://example.com/hhh.pdf",
        kind="award_summary",
        sha="hhh",
        doc_num="5247418372",
        extraction={"contracts": [{"reference": "RFT 999", "bids": [bid], "awards": []}]},
    )

    backfill_from_extraction(conn, "award_summary")
    row = conn.execute("SELECT hst_basis, price_header FROM bid WHERE source='award_summary'").fetchone()
    assert row["hst_basis"] == hst_basis
    assert row["price_header"] is None


def test_backfill_committee_keys_each_contract_on_its_own_document_number(conn):
    _cache_doc(
        conn,
        url="https://example.com/ccc.pdf",
        kind="committee_award",
        sha="ccc",
        doc_num="1111122222",
        extraction={
            "contracts": [
                {"reference": "Request for Tender No. 1111122222", "bids": [{"supplier_name": "Alpha Co.", "amount_raw": "$1.00"}]},
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
        for r in conn.execute("SELECT document_number, bidder_name_raw FROM bid WHERE source='committee_award'")
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
        "Doc3333344444, Contract No. 22TE-17WS",
        "Tender Call No. 317-2010, Contract No. 10TE-17WS",
        "Request for Quotation 3905-10-0097",
        "1111111111",
    ],
)
def test_backfill_committee_unusable_reference_falls_back_to_pdf_document(conn, reference):
    _cache_doc(
        conn,
        url="https://example.com/ddd.pdf",
        kind="committee_award",
        sha="ddd",
        doc_num="5555566666",
        extraction={"contracts": [{"reference": reference, "bids": [{"supplier_name": "Alpha Co.", "amount_raw": "$1.00"}]}]},
    )

    backfill_from_extraction(conn, "committee")
    rows = conn.execute("SELECT document_number FROM bid WHERE source='committee_award'").fetchall()
    assert [r["document_number"] for r in rows] == ["5555566666"]


def test_backfill_award_summary_ignores_contract_reference(conn):
    _cache_doc(
        conn,
        url="https://example.com/eee.pdf",
        kind="award_summary",
        sha="eee",
        doc_num="5247418372",
        extraction={"contracts": [{"reference": "Doc3333344444", "bids": [{"supplier_name": "Alpha Co.", "amount_raw": "$1.00"}]}]},
    )

    backfill_from_extraction(conn, "award_summary")
    rows = conn.execute("SELECT document_number FROM bid WHERE source='award_summary'").fetchall()
    assert [r["document_number"] for r in rows] == ["5247418372"]


def test_backfill_composite_awards_from_extraction(conn):
    _cache_doc(
        conn,
        url="https://example.com/ccc.pdf",
        kind="bgrd",
        sha="ccc",
        reference="2011.BD5.1",
        extraction={
            "contracts": [
                {
                    "reference": "Request for Tender No. 3905-10-0097, Contract No. 10TE-17WS",
                    "title": "Watermain Replacement on Main St",
                    "awards": [{"supplier_name": "Builder Co.", "amount_raw": "$1,000,000.00"}],
                    "bids": [],
                }
            ]
        },
    )

    result = backfill_from_extraction(conn, "composite")
    assert result["awards_written"] == 1
    row = conn.execute(
        "SELECT call_number, call_number_raw, title, supplier_name_raw, reference FROM composite_award"
    ).fetchone()
    assert tuple(row) == (
        "3905-10-0097",
        "Request for Tender No. 3905-10-0097, Contract No. 10TE-17WS",
        "Watermain Replacement on Main St",
        "Builder Co.",
        "2011.BD5.1",
    )


@pytest.mark.parametrize("reference", ["", None, "RFP 999", "Contract No. 10TE-17WS"])
def test_backfill_composite_refuses_a_reference_that_is_not_a_call_number(conn, reference):
    _cache_doc(
        conn,
        url="https://example.com/ccc.pdf",
        kind="bgrd",
        sha="ccc",
        reference="2010.BD3.4",
        extraction={
            "contracts": [
                {"reference": reference, "title": "Something", "awards": [{"supplier_name": "Refused Co.", "amount_raw": "$5.00"}]},
                {"reference": "Tender Call No. 317-2010", "awards": [{"supplier_name": "Kept Co.", "amount_raw": "$6.00"}]},
            ]
        },
    )
    logged = []

    result = backfill_from_extraction(conn, "composite", log=logged.append)
    assert result["awards_written"] == 1
    rows = conn.execute("SELECT call_number, supplier_name_raw FROM composite_award").fetchall()
    assert [(r["call_number"], r["supplier_name_raw"]) for r in rows] == [("317-2010", "Kept Co.")]
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
        ("2014.BD20.1", False, False),
    ],
)
def test_composite_and_ba_report_corpora_partition_bgrd(conn, reference, in_composite, in_ba_report):
    _insert_pdf(conn, url="https://example.com/r.pdf", kind="bgrd", sha="rrr", reference=reference)

    def members(corpus):
        return conn.execute(f"SELECT COUNT(*) FROM background_pdf WHERE {CORPORA[corpus]}").fetchone()[0]

    assert members("composite") == int(in_composite)
    assert members("ba_report") == int(in_ba_report)


def test_ba_report_bids_land_in_bid_not_composite_award(conn):
    _cache_doc(
        conn,
        url="https://example.com/bab.pdf",
        kind="bgrd",
        sha="bab",
        reference="2019.BA12.3",
        extraction={
            "contracts": [
                {
                    "reference": "Request for Tender Doc3333344444",
                    "title": "Road resurfacing",
                    "bids": [
                        {"supplier_name": "Alpha Co.", "amount_raw": "$1.00", "amount_basis": "plus_HST"},
                        {"supplier_name": "Beta Inc.", "amount_raw": "$2.00"},
                    ],
                    "awards": [{"supplier_name": "Alpha Co.", "amount_raw": "$1.00"}],
                },
                {"reference": "Request for Quotation 3905-10-0097", "bids": [{"supplier_name": "Gamma Ltd.", "amount_raw": "$3.00"}]},
                {"reference": None, "bids": [{"supplier_name": "Delta LLC", "amount_raw": "$4.00"}]},
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
            "SELECT bidder_name_raw, reference, document_number, hst_basis FROM bid WHERE source='ba_report'"
        )
    )
    assert got == [
        ("Alpha Co.", "2019.BA12.3", "3333344444", "excluding"),
        ("Beta Inc.", "2019.BA12.3", "3333344444", None),
        ("Delta LLC", "2019.BA12.3", None, None),
        ("Gamma Ltd.", "2019.BA12.3", None, None),
    ]


def test_composite_rebuild_clears_ba_era_rows_from_the_pre_feed_keyspace(conn):
    conn.execute(
        "INSERT INTO composite_award (call_number, reference, supplier_name_raw, source) "
        "VALUES ('Doc3333344444', '2019.BA12.3', 'Wrong Keyspace Inc.', 'bid_committee_composite')"
    )
    _cache_doc(
        conn,
        url="https://example.com/old.pdf",
        kind="bgrd",
        sha="old",
        reference="2010.BD2.2",
        extraction={"contracts": [{"reference": "317-2010", "awards": [{"supplier_name": "X"}]}]},
    )

    backfill_from_extraction(conn, "composite")
    assert [r[0] for r in conn.execute("SELECT reference FROM composite_award")] == ["2010.BD2.2"]


def test_backfill_safety_guards(conn):
    with pytest.raises(ValueError, match="Unknown corpus 'bogus'"):
        backfill_from_extraction(conn, "bogus")

    # Unseeded buyer raises RuntimeError.
    _cache_doc(
        conn,
        url="https://pub-trca.escribemeetings.com/unseeded.pdf",
        sha="unseeded",
        extraction={"contracts": [{"reference": "10037330", "bids": [{"supplier_name": "Acme"}]}]},
    )
    with pytest.raises(RuntimeError, match="buyer is not seeded"):
        backfill_from_extraction(conn, "trca")

    # Empty supplier names are skipped cleanly across award_summary, ba_report, and agency corpora.
    conn.execute("INSERT INTO buyer (id, slug, name) VALUES (2, 'trca', 'TRCA')")
    _cache_doc(
        conn,
        url="https://pub-trca.escribemeetings.com/empty_name.pdf",
        sha="trca_empty",
        extraction={
            "contracts": [
                {
                    "reference": "10037331",
                    "bids": [{"supplier_name": ""}, {"supplier_name": "Valid Bid"}],
                    "awards": [{"supplier_name": None}, {"supplier_name": "Valid Award"}],
                }
            ]
        },
    )
    _cache_doc(
        conn,
        url="https://example.com/as_empty.pdf",
        kind="award_summary",
        sha="as_empty",
        doc_num="1234567890",
        extraction={"contracts": [{"reference": "RFT 1", "bids": [{"supplier_name": ""}, {"supplier_name": "AS Co."}]}]},
    )
    _cache_doc(
        conn,
        url="https://example.com/ba_empty.pdf",
        kind="bgrd",
        sha="ba_empty",
        reference="2020.BA1.1",
        extraction={"contracts": [{"reference": "RFT 1", "bids": [{"supplier_name": None}, {"supplier_name": "BA Co."}]}]},
    )

    assert backfill_from_extraction(conn, "trca")["bids_written"] == 2
    assert backfill_from_extraction(conn, "award_summary")["bids_written"] == 1
    assert backfill_from_extraction(conn, "ba_report")["bids_written"] == 1


def test_backfill_bd_report_mixed_accepted_and_refused_rolls_back(conn):
    conn.execute("INSERT INTO council_item (reference, title) VALUES ('2014.BD1.1', 'Tender Call No. 317-2014')")
    conn.execute(
        "INSERT INTO bid (bidder_name_raw, reference, source) VALUES ('Existing Co.', '2014.BD1.1', 'bd_report')"
    )
    _cache_doc(
        conn,
        url="https://example.com/bd_ok.pdf",
        kind="bgrd",
        sha="bd_ok",
        reference="2014.BD1.1",
        text="Tender Call No. 317-2014\nAcme Ltd. $100.00",
        extraction={
            "contracts": [
                {
                    "reference": "Tender Call No. 317-2014",
                    "bids": [{"supplier_name": "Acme Ltd.", "amount_raw": "$100.00"}],
                }
            ]
        },
    )
    _cache_doc(
        conn,
        url="https://example.com/bd_bad.pdf",
        kind="bgrd",
        sha="bd_bad",
        reference="2014.BD1.1",
        text="Unrelated addendum",
        extraction={"contracts": []},
    )

    with pytest.raises(RuntimeError, match="mixed accepted/refused"):
        backfill_from_extraction(conn, "bd_report")

    rows = conn.execute("SELECT bidder_name_raw FROM bid WHERE source='bd_report'").fetchall()
    assert [r["bidder_name_raw"] for r in rows] == ["Existing Co."]


def test_backfill_rebuild_drops_rows_no_longer_derived(conn):
    _insert_pdf(conn, url="https://example.com/a.pdf", kind="award_summary", sha="s1", doc_num="1111111111")
    _cache_award_summary(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "Stale Ltd.")
    backfill_from_extraction(conn, "award_summary")
    _cache_award_summary(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.")

    backfill_from_extraction(conn, "award_summary")
    assert _award_summary_bidders(conn) == {"Alpha Co."}


def test_backfill_empty_derived_set_deletes_nothing(conn):
    conn.execute(
        "INSERT INTO bid (bidder_name_raw, document_number, source) "
        "VALUES ('Kept Inc.', '1111111111', 'award_summary')"
    )
    conn.commit()

    result = backfill_from_extraction(conn, "award_summary")
    assert result["bids_written"] == 0
    assert _award_summary_bidders(conn) == {"Kept Inc."}


def test_backfill_coverage_floor_guards_partial_reextraction(conn):
    for i in range(4):
        _insert_pdf(conn, url=f"https://example.com/{i}.pdf", kind="award_summary", sha=f"s{i}", doc_num=f"{i}" * 10)
        _cache_award_summary(conn, f"s{i}", "v0-old", f"Old Bidder {i}")
        conn.execute(
            "INSERT INTO bid (bidder_name_raw, document_number, source) VALUES (?, ?, 'award_summary')",
            (f"Old Bidder {i}", f"{i}" * 10),
        )
    conn.commit()

    _cache_award_summary(conn, "s0", EXTRACTOR_VERSION, "New Bidder 0")
    with pytest.raises(RuntimeError, match="1 of 4"):
        backfill_from_extraction(conn, "award_summary")
    assert _award_summary_bidders(conn) == {f"Old Bidder {i}" for i in range(4)}

    for i in (1, 2):
        _cache_award_summary(conn, f"s{i}", EXTRACTOR_VERSION, f"New Bidder {i}")
    result = backfill_from_extraction(conn, "award_summary")
    assert result["bids_written"] == 3
    assert _award_summary_bidders(conn) == {f"New Bidder {i}" for i in range(3)}


@pytest.mark.parametrize("failure_point", ["upsert", "sweep"])
def test_backfill_rolls_back_on_swap_or_sweep_failure(conn, monkeypatch, failure_point):
    import toronto_bids.store.db as db

    _insert_pdf(conn, url="https://example.com/a.pdf", kind="award_summary", sha="s1", doc_num="1111111111")
    _cache_award_summary(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "Beta Inc.")
    backfill_from_extraction(conn, "award_summary")
    _cache_award_summary(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "Gamma Ltd.")

    target = conn
    if failure_point == "upsert":
        real = db._rebuild_upsert
        calls = []

        def flaky(c, row):
            calls.append(row)
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return real(c, row)

        monkeypatch.setattr(db, "_rebuild_upsert", flaky)
    else:
        class FailingSweep:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def execute(self, sql, *args):
                if sql.startswith("DELETE FROM bid WHERE"):
                    raise RuntimeError("disk full")
                return self._real.execute(sql, *args)

        target = FailingSweep(conn)

    with pytest.raises(RuntimeError, match="disk full"):
        backfill_from_extraction(target, "award_summary")

    assert _award_summary_bidders(conn) == {"Alpha Co.", "Beta Inc."}


_OLD = "2020-01-01 00:00:00"


@pytest.mark.parametrize("corpus", ["award_summary", "composite", "trca"])
def test_backfill_rebuild_preserves_first_seen_of_surviving_rows(conn, corpus):
    if corpus == "award_summary":
        _insert_pdf(conn, url="https://example.com/a.pdf", kind="award_summary", sha="s1", doc_num="1111111111")
        _cache_award_summary(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "Stale Ltd.")
        tables = ("bid",)
    elif corpus == "composite":
        _cache_doc(
            conn,
            url="https://example.com/bgrd.pdf",
            kind="bgrd",
            sha="ccc",
            reference="2011.BD5.1",
            extraction={"contracts": [{"reference": "3905-10-0097", "awards": [{"supplier_name": "Builder Co.", "amount_raw": None}], "bids": []}]},
        )
        tables = ("composite_award",)
    else:
        conn.execute("INSERT INTO buyer (id, slug, name) VALUES (2, 'trca', 'TRCA')")
        _cache_doc(
            conn,
            url="https://pub-trca.escribemeetings.com/r.pdf",
            sha="aaa",
            extraction={
                "contracts": [
                    {
                        "reference": "10037330",
                        "bids": [{"supplier_name": "Acme Ltd.", "amount_raw": "$100.00"}],
                        "awards": [{"supplier_name": "Acme Ltd.", "amount_raw": None}],
                    }
                ]
            },
        )
        tables = ("agency_bid", "agency_award")

    backfill_from_extraction(conn, corpus)
    for table in tables:
        conn.execute(f"UPDATE {table} SET first_seen = ?, last_seen = ?", (_OLD, _OLD))
    conn.commit()

    if corpus == "award_summary":
        before_id = conn.execute("SELECT id FROM bid WHERE bidder_name_raw = 'Alpha Co.'").fetchone()[0]
        _cache_award_summary(conn, "s1", EXTRACTOR_VERSION, "Alpha Co.", "New Inc.")
        backfill_from_extraction(conn, corpus)
        rows = {
            r["bidder_name_raw"]: (r["id"], r["first_seen"], r["last_seen"])
            for r in conn.execute("SELECT id, bidder_name_raw, first_seen, last_seen FROM bid WHERE source='award_summary'")
        }
        assert set(rows) == {"Alpha Co.", "New Inc."}
        assert rows["Alpha Co."][0] == before_id
        assert rows["Alpha Co."][1] == _OLD and rows["Alpha Co."][2] > _OLD
        assert rows["New Inc."][1] > _OLD
    else:
        backfill_from_extraction(conn, corpus)
        for table in tables:
            rows = conn.execute(f"SELECT first_seen, last_seen FROM {table}").fetchall()
            assert len(rows) == 1, table
            assert rows[0]["first_seen"] == _OLD and rows[0]["last_seen"] > _OLD


def test_backfill_rebuild_clears_unset_columns_and_leaves_other_sources_alone(conn):
    conn.execute(
        "INSERT INTO bid (bidder_name_raw, reference, source) VALUES ('Panel Bidder', '2018.BA1.1', 'bid_award_panel')"
    )
    _insert_pdf(conn, url="https://example.com/a.pdf", kind="award_summary", sha="s1", doc_num="1111111111")

    def cache(basis):
        bid = {"supplier_name": "Alpha Co.", "amount_raw": "$5.00", "amount_basis": basis}
        mark_extracted(conn, "s1", EXTRACTOR_VERSION, result_json=json.dumps({"contracts": [{"reference": "RFT 1", "bids": [bid]}]}))

    cache("including_HST")
    backfill_from_extraction(conn, "award_summary")
    cache("unknown")
    backfill_from_extraction(conn, "award_summary")

    assert conn.execute("SELECT hst_basis FROM bid WHERE source='award_summary'").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM bid WHERE source = 'bid_award_panel'").fetchone()[0] == 1


def test_extract_and_backfill_keyless_behavior(conn, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _cache_doc(
        conn,
        url="https://example.com/has-text.pdf",
        kind="bgrd",
        sha="ttt",
        reference="2011.BD5.1",
        extraction={"contracts": [{"reference": "3905-10-0097", "awards": [{"supplier_name": "Builder Co.", "amount_raw": "$1,000.00"}], "bids": []}]},
    )
    _insert_pdf(conn, url="https://example.com/image-only.pdf", kind="bgrd", sha="iii", text=None, reference="2011.BD5.2")

    assert extract_and_backfill(conn, "composite")["awards_written"] == 1

    # With an uncached document that has text, missing credentials must raise.
    _insert_pdf(conn, url="https://example.com/uncached.pdf", kind="award_summary", sha="uuu", text="contract text")
    with pytest.raises(ValueError):
        extract_and_backfill(conn, "award_summary")


def test_one_bad_response_does_not_block_the_rest_of_the_corpus(conn):
    _insert_pdf(conn, url="https://example.com/a.pdf", sha="a1", text="text a")
    _insert_pdf(conn, url="https://example.com/b.pdf", sha="b1", text="text b")
    client = _SequenceClient(TypeError("unexpected shape"), {"contracts": []})
    logged = []

    stats = extract_corpus(
        conn, "trca", client=client, labels={}, where="kind='agency_board'", log=logged.append, classifier=None
    )

    assert stats["errors"] == 1
    assert stats["extracted"] == 1
    assert client.calls == ["text a", "text b"]
    assert not is_extracted(conn, "a1", EXTRACTOR_VERSION)
    assert is_extracted(conn, "b1", EXTRACTOR_VERSION)
    assert any("FAILED https://example.com/a.pdf" in m for m in logged)


@pytest.mark.parametrize(
    "client_factory, track_failures, expected_failures",
    [
        (_FailingClient, True, [("extract:award_summary", "2 of 2 documents failed extraction")]),
        (lambda: FakeClient(), True, []),
        (_FailingClient, False, None),
    ],
)
def test_extract_and_backfill_failure_reporting(conn, monkeypatch, client_factory, track_failures, expected_failures):
    import toronto_bids.extract as extract_mod

    monkeypatch.setattr(extract_mod, "ExtractionClient", client_factory)
    for i in range(2):
        _insert_pdf(conn, url=f"https://example.com/form{i}.pdf", kind="award_summary", sha=f"s{i}")

    failures = [] if track_failures else None
    logged = []
    result = extract_and_backfill(conn, "award_summary", log=logged.append, failures=failures)
    assert result["bids_written"] == 0
    if track_failures:
        assert failures == expected_failures
        if expected_failures:
            assert sum(m.startswith("  FAILED") for m in logged) == 2
            assert any("2 errors, 0 count flags" in m for m in logged)
