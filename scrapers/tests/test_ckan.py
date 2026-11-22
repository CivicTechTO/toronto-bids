import json

import httpx
import pytest

from toronto_bids import config, pipeline
from toronto_bids.http import HttpClient
from toronto_bids.models import Award, NonCompetitive, Solicitation
from toronto_bids.sources import ckan
from toronto_bids.store import db
from tests.conftest import FIXTURES


CAPITAL_RAW = {
    "_id": "1",
    "No.": "2",
    "Name and Construction Contract Number":
        "Dufferin Transfer Station - Compactors Replacement  - 25ECS-MI-02SW",
    "Type of Work": "Construction",
    "Scope of Work: Detailed Description": "Replace the compactors at Dufferin.",
    "Delivery Division": "Solid Waste Management Services",
    "Project Owner (Division)": "Solid Waste Management Services",
    "Target Sourcing Year": "2026",
    "Target Award Year": "2026",
    "Sourcing Type": "RFT",
    "Estimated Range": "Up to $5M",
    "Estimated Contract Term (Months)": "24",
}


def _http(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


def _records(fixture_name):
    data = json.loads((FIXTURES / fixture_name).read_text())
    return data["result"]["records"]


def test_resolve_resource_id_picks_datastore_active_resource():
    def handler(request):
        assert "package_show" in str(request.url)
        return httpx.Response(200, json={
            "success": True,
            "result": {"resources": [
                {"id": "aaa", "format": "PDF", "datastore_active": False},
                {"id": "e211f003-5909-4bea-bd96-d75899d8e612", "format": "CSV", "datastore_active": True},
            ]},
        })
    assert ckan.resolve_resource_id(_http(handler), "tobids-awarded-contracts") == "e211f003-5909-4bea-bd96-d75899d8e612"


def test_resolve_resource_id_errors_on_failure_or_no_datastore():
    fail_http = _http(lambda _: httpx.Response(200, json={"success": False, "error": {"message": "bad"}}))
    with pytest.raises(RuntimeError):
        ckan.resolve_resource_id(fail_http, "bad-slug")

    no_ds_http = _http(lambda _: httpx.Response(200, json={
        "success": True,
        "result": {"resources": [{"id": "aaa", "datastore_active": False}]},
    }))
    with pytest.raises(LookupError):
        ckan.resolve_resource_id(no_ds_http, "no-datastore")


def test_fetch_datastore_paginates_until_empty():
    pages = [
        {"success": True, "result": {"records": [{"_id": 1}, {"_id": 2}]}},
        {"success": True, "result": {"records": []}},
    ]
    seen_offsets = []
    def handler(request):
        offset = int(dict(request.url.params).get("offset", "0"))
        seen_offsets.append(offset)
        return httpx.Response(200, json=pages[0] if offset == 0 else pages[1])
    records = list(ckan.fetch_datastore(_http(handler), "res-id", page_size=2))
    assert [r["_id"] for r in records] == [1, 2]
    assert seen_offsets == [0, 2]


def test_fetch_datastore_raises_on_unsuccessful_response():
    fail_http = _http(lambda _: httpx.Response(200, json={"success": False, "error": "gone"}))
    with pytest.raises(RuntimeError):
        list(ckan.fetch_datastore(fail_http, "res-id"))


def test_ckan_source_rejects_unknown_kind():
    with pytest.raises(ValueError):
        ckan.CkanSource(name="x", slug="s", kind="bogus")


def test_normalize_awarded_yields_solicitation_and_award():
    rows = list(ckan.normalize_awarded(_records("ckan_awarded.json")[0]))
    sol = next(r for r in rows if isinstance(r, Solicitation))
    award = next(r for r in rows if isinstance(r, Award))
    assert sol.document_number == "3303123110"
    assert sol.status == "Awarded"
    assert sol.rfx_type == "RFQ"
    assert sol.category == "Goods and Services"
    assert sol.source == "ckan_awarded"
    assert award.document_number == "3303123110"
    assert award.supplier_name_raw == "Computer Media Group"
    assert award.award_amount == "26773.58"
    assert award.award_date == "2012-10-04"


def test_awarded_source_uses_each_nested_award_line(caplog):
    src = ckan.CkanSource(name="ckan_awarded", slug="tobids-awarded-contracts", kind="awarded")
    raw = {
        "Document Number": "3303123110",
        "Award Authority Obtained Date": "2025-01-01",
        "Award": json.dumps([
            {"Successful_Bidder": "Acme", "Award_Amount": "1,000.00",
             "Date_Awarded": "2025-02-02", "AwardedDate": None},
            None,
            {"Successful_Bidder": "Acme", "Award_Amount": "400",
             "Date_Awarded": "2025-03-03", "AwardedDate": "2025-03-04"},
            {"Successful_Bidder": "Other Ltd", "Award_Amount": None,
             "Date_Awarded": None, "AwardedDate": "2025-04-04",
             "Initial_Contract_Val": "500"},
        ]),
    }
    solicitation, *awards = src.normalize(raw)
    assert isinstance(solicitation, Solicitation)
    assert solicitation.status == "Awarded"
    assert awards == [
        Award("3303123110", "Acme", "1,000.00", "2025-02-02", "ckan_awarded"),
        Award("3303123110", "Acme", "400", "2025-03-03", "ckan_awarded"),
        Award("3303123110", "Other Ltd", None, "2025-04-04", "ckan_awarded"),
    ]
    assert "3303123110: Award[1] must be an object" in caplog.text


def test_awarded_source_refuses_a_truncated_award_array_with_legacy_columns(caplog):
    raw = {**_records("ckan_awarded.json")[0], "Award": '[{"Successful_Bidder":'}
    rows = list(ckan.normalize_awarded(raw))
    assert len(rows) == 1
    assert isinstance(rows[0], Solicitation)
    assert "3303123110: Award must be a JSON array" in caplog.text


def test_normalize_awarded_skips_invalid_document_number():
    rows = list(ckan.normalize_awarded(_records("ckan_awarded.json")[1]))
    assert rows == []


def test_normalize_awarded_yields_no_award_when_supplier_blank():
    rows = list(ckan.normalize_awarded(_records("ckan_awarded.json")[2]))
    assert any(isinstance(r, Solicitation) for r in rows)
    assert not any(isinstance(r, Award) for r in rows)
    assert rows[0].document_number == "5749398870"


def test_normalize_open_yields_open_solicitation():
    rows = list(ckan.normalize_open(_records("ckan_solicitations.json")[0]))
    sol = rows[0]
    assert isinstance(sol, Solicitation)
    assert sol.document_number == "9117105139"
    assert sol.status == "Open"
    assert sol.rfx_type == "RFP"
    assert sol.noip_type == "Notice of Intended Procurement"
    assert sol.issue_date == "2010-10-28"
    assert sol.submission_deadline == "2010-11-24"
    assert sol.source == "ckan_open"
    assert sol.wards is None


def test_normalize_noncompetitive_yields_row():
    rows = list(ckan.normalize_noncompetitive(_records("ckan_noncompetitive.json")[0]))
    nc = rows[0]
    assert isinstance(nc, NonCompetitive)
    assert nc.workspace_number == "8614"
    assert nc.supplier_name_raw == "Accuworx Inc"
    assert nc.reason == "Emergency"
    assert nc.contract_amount == "67896.4"
    assert nc.source == "ckan_noncomp"


# --- capital pipeline ---


def test_normalizes_a_pipeline_row():
    project, = ckan.normalize_capital_project(CAPITAL_RAW)
    assert project.name == CAPITAL_RAW["Name and Construction Contract Number"]
    assert project.type_of_work == "Construction"
    assert project.target_sourcing_year == "2026"
    assert project.sourcing_type == "RFT"
    assert project.estimated_range == "Up to $5M"
    assert project.source == "ckan_pipeline"


@pytest.mark.parametrize("name,expected", [
    ("Dufferin Transfer Station - Compactors Replacement  - 25ECS-MI-02SW", "25ECS-MI-02SW"),
    ("Dufferin Transfer Station - Paving - 25SWM-IRM-042CDU", "25SWM-IRM-042CDU"),
    ("Basement Flooding Protection Program - 23ECS-LU-02FP", "23ECS-LU-02FP"),
    ("Sewage Pumping Station Planning and Environmental Assessment Study", None),
])
def test_teases_the_construction_contract_number_out_of_the_name(name, expected):
    project, = ckan.normalize_capital_project({"Name and Construction Contract Number": name})
    assert project.contract_number == expected


def test_capital_pipeline_row_with_no_name_is_skipped():
    assert list(ckan.normalize_capital_project({"Type of Work": "Construction"})) == []


def test_capital_pipeline_stores_idempotently_and_updates_on_refresh(conn):
    first, = ckan.normalize_capital_project(CAPITAL_RAW)
    db.upsert_row(conn, first, overwrite=True)
    db.upsert_row(conn, first, overwrite=True)
    conn.commit()
    assert db.counts(conn)["capital_project"] == 1

    moved, = ckan.normalize_capital_project(dict(CAPITAL_RAW, **{"Target Sourcing Year": "2027"}))
    db.upsert_row(conn, moved, overwrite=True)
    conn.commit()
    assert conn.execute("SELECT target_sourcing_year FROM capital_project").fetchone()[0] == "2027"


def test_the_pipeline_source_overwrites_unlike_the_other_ckan_sources():
    by_name = {s.name: s for s in pipeline.default_sources()}
    assert by_name["ckan_pipeline"].overwrite is True
    assert by_name["ckan_awarded"].overwrite is False
    assert by_name["ckan_open"].overwrite is False
    assert ckan.CkanSource(name="x", slug=config.CKAN_PIPELINE_SLUG,
                           kind="capital_pipeline").overwrite is False
