"""Reconciliation tests for the 5 new agency board corpora (tchc, tpl, tpsb, ttc_tmmis, tpa)."""
import hashlib
import json
from pathlib import Path

import pytest

from toronto_bids import known_gaps
from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking import agency_identity
from toronto_bids.store import db


_DATA = Path(__file__).resolve().parents[1] / "toronto_bids" / "data"

_TCHC_DOCS = [
    {
        "url": "https://torontohousing.ca/sites/default/files/2023-11/item_8a_-_multiple_vendor_award_-_rfp_21125_rfq_21247_fire_services_211026_a.pdf",
        "sha256": "5b2e3d2867e7e27f14c3fa1766a26a910c3f3d91bb034130be08149ec7c9b208",
        "contract": {
            "awards": [
                {"amount_basis": None, "amount_raw": None, "contingency_raw": None, "supplier_name": "Oak Ridge Building Solutions Inc.", "value_confidential": False},
                {"amount_basis": None, "amount_raw": None, "contingency_raw": None, "supplier_name": "Greater Toronto Fire Protection", "value_confidential": False},
                {"amount_basis": None, "amount_raw": None, "contingency_raw": None, "supplier_name": "Eurotech Safety Inc.", "value_confidential": False},
            ],
            "bids": [
                {"amount_basis": None, "amount_raw": None, "rank": None, "status": "not_stated", "supplier_name": "Oak Ridge Building Solutions Inc."},
                {"amount_basis": None, "amount_raw": None, "rank": None, "status": "not_stated", "supplier_name": "T.B.R. Mechanical/Electrical Inc."},
                {"amount_basis": None, "amount_raw": None, "rank": None, "status": "not_stated", "supplier_name": "Greater Toronto Fire Protection"},
                {"amount_basis": None, "amount_raw": None, "rank": None, "status": "not_stated", "supplier_name": "Eurotech Safety Inc."},
            ],
            "buyer": "Toronto Community Housing Corporation (TCHC)",
            "closed_date": "2021-07-27",
            "decision_body": "Building Investment, Finance and Audit Committee",
            "decision_date": "2021-11-03",
            "declared_compliant": None,
            "declared_submissions": 4,
            "documents_taken": None,
            "funding_source": "2022 Operating Budget (SLA.5291 and OU Budgets) and 2022 Capital Budget (CO04)",
            "invited_to_tender": [],
            "posted_date": "2021-06-29",
            "prequalified": [],
            "reference": "RFP 21125 and RFQ 21247",
            "solicitation_type": "RFP",
            "stage": "single_stage",
            "title": "Preventative Maintenance Services and Demand Repairs for Fire Alarm/Suppression Systems",
        },
        "flags": None,
        "result_sha256": "8a5e977bbe0523f9dc32b179fc5c9ce7dc4a4e5f818a040fc68f5b94da04eb30",
    },
    {
        "url": "https://torontohousing.ca/sites/default/files/2023-10/item_4_-_2023-125_-_vendor_award_supply_delivery_and_installation_of_new_appliances.pdf",
        "sha256": "cbabe07271af983650d6efe3b5f31d85d79c53408e05cd61987fcc41d0a78da8",
        "contract": {
            "awards": [
                {"amount_basis": None, "amount_raw": None, "contingency_raw": None, "supplier_name": 'Canadian Appliance Recycling Enterprise ("CARE")', "value_confidential": False},
                {"amount_basis": None, "amount_raw": None, "contingency_raw": None, "supplier_name": "HD Supply Canada Inc.", "value_confidential": False},
            ],
            "bids": [
                {"amount_basis": None, "amount_raw": None, "rank": None, "status": "compliant", "supplier_name": "HD Supply Canada Inc."},
                {"amount_basis": None, "amount_raw": None, "rank": None, "status": "compliant", "supplier_name": 'Canadian Appliance Recycling Enterprise ("CARE")'},
            ],
            "buyer": "Toronto Community Housing Corporation (TCHC)",
            "closed_date": "2023-02-24",
            "decision_body": "Building Investment, Finance and Audit Committee",
            "decision_date": "2023-10-24",
            "declared_compliant": 2,
            "declared_submissions": 3,
            "documents_taken": None,
            "funding_source": "2023 Operating Budget (program budget); future years to be requested in the 2024-2028 Operating Budgets",
            "invited_to_tender": [],
            "posted_date": "2023-01-18",
            "prequalified": [],
            "reference": "RFP 22278 / RFQ 23145",
            "solicitation_type": "RFP",
            "stage": "single_stage",
            "title": "Supply, Delivery, and Installation of New Appliances (and removal and disposal of old appliances) for all TCHC and TSHC units west of Yonge Street",
        },
        "flags": [{"actual": 2, "declared": 3, "delta": -1, "reference": "RFP 22278 / RFQ 23145"}],
        "result_sha256": "f3d38a94ce1d56b686adea61644228171282885e77a487db778924c805467720",
    },
    {
        "url": "https://torontohousing.ca/sites/default/files/2023-11/item_9b_-_2023-54_-_vendor_award_supply_delivery_and_installation_of_appliances_r_-_updated.pdf",
        "sha256": "d5bf44509028160b3856ab82edec0490f7d2916338241ba7cbdd29ac5559f961",
        "contract": {
            "awards": [
                {"amount_basis": None, "amount_raw": None, "contingency_raw": None, "supplier_name": "Canadian Appliance Recycling Enterprise", "value_confidential": True},
                {"amount_basis": None, "amount_raw": None, "contingency_raw": None, "supplier_name": "HD Supply Canada Inc.", "value_confidential": True},
            ],
            "bids": [],
            "buyer": "Toronto Community Housing Corporation",
            "closed_date": "2023-02-24",
            "decision_body": "Board of Directors",
            "decision_date": "2023-10-27",
            "declared_compliant": 2,
            "declared_submissions": 3,
            "documents_taken": None,
            "funding_source": "2023 Operating Budget (program budget); future years requested within 2024-2028 Operating Budgets",
            "invited_to_tender": [],
            "posted_date": "2023-01-18",
            "prequalified": [],
            "reference": "RFP 22278 / RFQ 23145",
            "solicitation_type": "RFP",
            "stage": "single_stage",
            "title": "Supply, Delivery, and Installation of New Appliances (and removal and disposal of old appliances) for all TCHC and TSHC units west of Yonge Street",
        },
        "flags": [{"actual": 0, "declared": 3, "delta": -3, "reference": "RFP 22278 / RFQ 23145"}],
        "result_sha256": "135224535c59e17b4fabe5e33cb0c4911fa15a50a1d9b07d7dd1a759a0f07022",
    },
]


def _seed_tchc(conn):
    seed_buyers(conn)
    for doc in _TCHC_DOCS:
        obj = {"contracts": [doc["contract"]]}
        if doc["flags"] is not None:
            obj["_flags"] = doc["flags"]
        payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert hashlib.sha256(payload.encode()).hexdigest() == doc["result_sha256"]
        conn.execute(
            "INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)",
            (doc["url"], "agency_board", doc["sha256"], "held source"),
        )
        db.mark_extracted(conn, doc["sha256"], EXTRACTOR_VERSION, payload)
    conn.commit()


def test_tchc_two_stage_rfp_identity_recovery_deduplicates_awards(conn):
    _seed_tchc(conn)
    agency_identity._evidence.cache_clear()
    res = backfill_from_extraction(conn, "tchc")

    assert res["solicitations_written"] == 3
    assert res["bids_written"] == 6
    assert res["awards_written"] == 2
    assert known_gaps.refused_contracts(conn, "tchc") == {}

    rfp_21125_bids = {
        r[0] for r in conn.execute("SELECT bidder_name_raw FROM agency_bid WHERE native_ref='RFP 21125'")
    }
    assert rfp_21125_bids == {
        "Oak Ridge Building Solutions Inc.",
        "T.B.R. Mechanical/Electrical Inc.",
        "Greater Toronto Fire Protection",
        "Eurotech Safety Inc.",
    }
    # Duplicate recommendation awards from BIFAC:2021-107 are withheld in favour of the Board award report.
    assert conn.execute("SELECT COUNT(*) FROM agency_award WHERE native_ref='RFP 21125'").fetchone()[0] == 0

    # RFP 22278 retains 2 compliant bids from BIFAC:2023-125 and 2 non-duplicated Board awards from TCHC:2023-54.
    assert conn.execute("SELECT COUNT(*) FROM agency_bid WHERE native_ref='RFP 22278'").fetchone()[0] == 2
    rfp_22278_awards = {
        r[0] for r in conn.execute("SELECT supplier_name_raw FROM agency_award WHERE native_ref='RFP 22278'")
    }
    assert rfp_22278_awards == {
        "Canadian Appliance Recycling Enterprise",
        "HD Supply Canada Inc.",
    }

    # Withheld recommendation claims classify cleanly as known coverage withholdings.
    unknown, known = known_gaps.classify(
        conn,
        [("coverage:tchc", "incomplete coverage: 5 individual award claim(s) withheld after source review; supported bids retained; inspect tb agency-coverage")],
    )
    assert unknown == [] and len(known) == 1


def test_tchc_reviewed_identity_aborts_when_extraction_changes(conn):
    _seed_tchc(conn)
    agency_identity._evidence.cache_clear()
    backfill_from_extraction(conn, "tchc")

    tampered = json.loads(json.dumps({"contracts": [_TCHC_DOCS[2]["contract"]]}))
    tampered["contracts"][0]["title"] += " (tampered)"
    db.mark_extracted(conn, _TCHC_DOCS[2]["sha256"], EXTRACTOR_VERSION, json.dumps(tampered))
    conn.commit()

    with pytest.raises(RuntimeError, match="existing rows kept"):
        backfill_from_extraction(conn, "tchc")


def test_known_agency_refusals_baseline_covers_all_eight_corpora():
    baseline = json.loads((_DATA / "known_agency_refusals.json").read_text())
    counts = {k: len(v) for k, v in baseline["corpora"].items()}
    assert counts == {
        "trca": 1020,
        "zoo": 29,
        "ep": 6,
        "tchc": 66,
        "tpl": 14,
        "tpsb": 964,
        "ttc_tmmis": 10,
        "tpa": 91,
    }
    for corpus, entries in baseline["corpora"].items():
        keys = [(e["sha256"], e["extractor_version"], e["result_sha256"], int(e["contract_index"])) for e in entries]
        assert len(keys) == len(set(keys)), f"duplicate baseline keys in {corpus}"
        assert all(e["reason"] == "ambiguous reference-less multi-contract report" for e in entries)
