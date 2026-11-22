"""Source-bound 2014–2024 TTC Board award verification against held decision PDFs (#341)."""

import json
from pathlib import Path

import pytest

from toronto_bids.buyers import seed_buyers
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking import agency_identity
from toronto_bids.store import db

DOCUMENTS = json.loads(
    (Path(__file__).parent / "fixtures/agencies/ttc_award_decisions_2026-11-22.json").read_text()
)


def hold(conn):
    seed_buyers(conn)
    for d in DOCUMENTS:
        conn.execute(
            "INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)",
            (d["url"], d["kind"], d["sha256"], d["text"]),
        )
        if "result_json" in d:
            db.mark_extracted(conn, d["sha256"], d["extractor_version"], d["result_json"])
    conn.commit()


def test_verified_ttc_decisions_admit_approved_awards_and_withhold_amended_and_uncorroborated(conn):
    hold(conn)
    cache = [tuple(r) for r in conn.execute("SELECT * FROM extraction_cache ORDER BY sha256")]
    result = backfill_from_extraction(conn, "ttc")

    awards = [dict(r) for r in conn.execute("SELECT * FROM agency_award WHERE source='ttc_board'")]
    refs_admitted = {r["native_ref"] for r in awards}

    # Adopted contracts enter agency_award with explicit Board decision dates:
    # N1-24 (1), G85-404 (5), S45-11/S45-12/D45-14 (3), E85-24 (1), plus 1 report_url_fallback.
    assert {"N1-24", "G85-404", "S45-11", "S45-12", "D45-14", "E85-24"} < refs_admitted
    assert any(r.startswith("https://cdn.ttc.ca/") for r in refs_admitted)

    # Amended (P32PL17757: 440 -> 325 buses), amount-omitted (M60-88), and Notice of Award
    # received for information (W45-11) MUST remain withheld.
    assert not ({"P32PL17757", "M60-88", "W45-11"} & refs_admitted)

    n1 = next(r for r in awards if r["native_ref"] == "N1-24")
    assert (n1["supplier_name_raw"], n1["award_amount"], n1["award_date"]) == (
        "EllisDon Civil Ltd.",
        "$8,520,553.13",
        "2024-07-17",
    )

    g404 = [r for r in awards if r["native_ref"] == "G85-404"]
    assert len(g404) == 5
    assert all(r["award_date"] == "2021-09-15" for r in g404)
    # Existing G85-404 bid-metadata withholding still enforced alongside award admission.
    g404_bids = conn.execute(
        "SELECT status FROM agency_bid WHERE native_ref='G85-404' AND source='ttc_board'"
    ).fetchall()
    assert len(g404_bids) == 14 and all(r["status"] is None for r in g404_bids)

    # Three-contract report admits all three stations independently.
    assert {r["native_ref"]: r["award_date"] for r in awards if r["native_ref"] in {"S45-11", "S45-12", "D45-14"}} == {
        "S45-11": "2021-07-07",
        "S45-12": "2021-07-07",
        "D45-14": "2021-07-07",
    }

    # E85-24 binds short-form "AECOM" and "$15 million" in decision PDF.
    e85 = next(r for r in awards if r["native_ref"] == "E85-24")
    assert e85["supplier_name_raw"] == "AECOM Canada Ltd." and e85["award_date"] == "2021-02-10"

    # Withheld recommendations keep explicit reasons in agency_derivation and non-awarded status.
    for withheld_ref in ("P32PL17757", "M60-88", "W45-11"):
        sol = conn.execute(
            "SELECT status FROM agency_solicitation WHERE native_ref=? AND source='ttc_board'",
            (withheld_ref,),
        ).fetchone()
        assert sol is not None and sol["status"] is None
        deriv = conn.execute(
            "SELECT reason FROM agency_derivation WHERE corpus='ttc' AND active=1 AND native_ref=?",
            (withheld_ref,),
        ).fetchone()
        assert deriv is not None and deriv["reason"]

    # Raw extraction cache is immutable.
    assert [tuple(r) for r in conn.execute("SELECT * FROM extraction_cache ORDER BY sha256")] == cache
    assert result["coverage"]["withheld_award_facts"] == 3
    assert result["coverage"]["accepted_award_facts"] == len(awards)


@pytest.mark.parametrize(
    "change",
    [
        "missing_decision",
        "changed_decision_hash",
        "changed_decision_passage",
        "changed_contract",
        "changed_other_contract",
        "changed_version",
    ],
)
def test_changed_ttc_decision_evidence_aborts_before_swapping_facts(conn, monkeypatch, change):
    hold(conn)
    backfill_from_extraction(conn, "ttc")
    before = {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
        for t in ["agency_bid", "agency_award", "agency_derivation"]
    }
    n1_dec = next(
        d for d in DOCUMENTS if d["kind"] == "ttc_archive_decision" and "Inglis" in d["url"]
    )
    if change == "missing_decision":
        conn.execute("DELETE FROM background_pdf WHERE url=?", (n1_dec["url"],))
    elif change == "changed_decision_hash":
        conn.execute("UPDATE background_pdf SET sha256=? WHERE url=?", ("e" * 64, n1_dec["url"]))
    elif change == "changed_decision_passage":
        conn.execute(
            "UPDATE background_pdf SET text='No reviewed TTC decision here' WHERE url=?",
            (n1_dec["url"],),
        )
    elif change in ("changed_contract", "changed_other_contract"):
        multi = next(
            d for d in DOCUMENTS if d.get("native_ref") in {"S45-11", "S45-12", "D45-14"}
        )
        raw = json.loads(multi["result_json"])
        raw["contracts"][0 if change == "changed_contract" else 2]["title"] += " changed"
        db.mark_extracted(conn, multi["sha256"], multi["extractor_version"], json.dumps(raw))
    elif change == "changed_version":
        monkeypatch.setattr(agency_identity, "EXTRACTOR_VERSION", "changed-review-version")
    conn.commit()

    with pytest.raises(RuntimeError, match="existing rows kept"):
        backfill_from_extraction(conn, "ttc")
    assert {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
        for t in before
    } == before


@pytest.mark.parametrize(
    "change",
    [
        "amended_marked_adopted",
        "missing_amount_marked_adopted",
        "unrelated_decision",
        "wrong_decision_date",
        "uncovered_award",
        "participation_review_on_ttc",
    ],
)
def test_ttc_award_review_guards_reject_forged_or_uncorroborated_admissions(
    conn, tmp_path, monkeypatch, change
):
    hold(conn)
    backfill_from_extraction(conn, "ttc")
    before = [tuple(r) for r in conn.execute("SELECT * FROM agency_award ORDER BY id")]

    path = Path(__file__).resolve().parents[1] / "toronto_bids/data/agency_identity_evidence.json"
    data = json.loads(path.read_text())

    if change == "amended_marked_adopted":
        proof = next(p for p in data["decisions"] if p["corpus"] == "ttc" and p["native_ref"] == "P32PL17757")
        proof["award_review"][0].update(disposition="adopted", decision_source=1, decision_date="2017-09-05")
    elif change == "missing_amount_marked_adopted":
        proof = next(p for p in data["decisions"] if p["corpus"] == "ttc" and p["native_ref"] == "M60-88")
        proof["award_review"][0].update(disposition="adopted", decision_source=1, decision_date="2021-09-15")
    elif change == "unrelated_decision":
        proof = next(p for p in data["decisions"] if p["corpus"] == "ttc" and p["native_ref"] == "N1-24")
        proof["sources"][1]["passages"] = ["The TTC Board on July 17, 2024, adopted the following:"]
    elif change == "wrong_decision_date":
        proof = next(p for p in data["decisions"] if p["corpus"] == "ttc" and p["native_ref"] == "N1-24")
        proof["award_review"][0]["decision_date"] = "2024-07-18"
    elif change == "uncovered_award":
        proof = next(p for p in data["decisions"] if p["corpus"] == "ttc" and p["native_ref"] == "G85-404")
        proof["award_review"].pop()
    elif change == "participation_review_on_ttc":
        proof = next(p for p in data["decisions"] if p["corpus"] == "ttc" and p["native_ref"] == "N1-24")
        proof["participation_review"] = {
            "prequalification_submissions": None,
            "qualified_for_tender": [],
            "invited_to_tender": [],
            "tender_submissions": [],
        }

    (tmp_path / "agency_identity_evidence.json").write_text(json.dumps(data))
    agency_identity._evidence.cache_clear()
    monkeypatch.setattr(agency_identity.resources, "files", lambda _: tmp_path)
    try:
        with pytest.raises(RuntimeError, match="existing rows kept"):
            backfill_from_extraction(conn, "ttc")
        assert [tuple(r) for r in conn.execute("SELECT * FROM agency_award ORDER BY id")] == before
    finally:
        agency_identity._evidence.cache_clear()


def test_replaying_ttc_award_review_preserves_fact_and_evidence_history(conn):
    hold(conn)
    backfill_from_extraction(conn, "ttc")
    tables = ["agency_award", "agency_bid", "agency_derivation"]
    before = {
        t: [tuple(r) for r in conn.execute(f"SELECT id,first_seen FROM {t} ORDER BY id")]
        for t in tables
    }
    backfill_from_extraction(conn, "ttc")
    assert {
        t: [tuple(r) for r in conn.execute(f"SELECT id,first_seen FROM {t} ORDER BY id")]
        for t in tables
    } == before
