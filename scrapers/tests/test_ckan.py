import json

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.models import Award, NonCompetitive, Solicitation
from toronto_bids.sources import ckan
from tests.conftest import FIXTURES


def _http(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


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
    http = _http(handler)
    assert ckan.resolve_resource_id(http, "tobids-awarded-contracts") == "e211f003-5909-4bea-bd96-d75899d8e612"


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
    http = _http(handler)
    records = list(ckan.fetch_datastore(http, "res-id", page_size=2))
    assert [r["_id"] for r in records] == [1, 2]
    assert seen_offsets == [0, 2]


def _records(fixture_name):
    data = json.loads((FIXTURES / fixture_name).read_text())
    return data["result"]["records"]


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
    rows = list(ckan.normalize_awarded(_records("ckan_awarded.json")[1]))  # "xxxxxxxx"
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


def test_ckan_source_dispatches_to_kind():
    src = ckan.CkanSource(name="ckan_awarded", slug="tobids-awarded-contracts", kind="awarded")
    rows = list(src.normalize(_records("ckan_awarded.json")[0]))
    assert any(isinstance(r, Award) for r in rows)
