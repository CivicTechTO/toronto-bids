import json

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.models import Award, NonCompetitive, Solicitation
from toronto_bids.sources import odata
from tests.conftest import FIXTURES


def _http(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


def _value(fixture_name):
    return json.loads((FIXTURES / fixture_name).read_text())["value"][0]


def test_fetch_entityset_pages_with_skip_top():
    page = json.loads((FIXTURES / "odata_solicitation.json").read_text())
    empty = {"@odata.count": 1, "value": []}
    seen_skip = []
    def handler(request):
        params = dict(request.url.params)
        skip = int(params.get("$skip", "0"))
        seen_skip.append(skip)
        return httpx.Response(200, json=page if skip == 0 else empty)
    records = list(odata.fetch_entityset(_http(handler), "feis_solicitation_published", page_size=1))
    assert len(records) == 1
    assert records[0]["Solicitation_Document_Number"] == "3303123110"
    assert seen_skip == [0, 1]


def test_normalize_solicitation_yields_spine_and_award():
    rows = list(odata.normalize_solicitation(_value("odata_solicitation.json")))
    sol = next(r for r in rows if isinstance(r, Solicitation))
    award = next(r for r in rows if isinstance(r, Award))
    assert sol.document_number == "3303123110"
    assert sol.status == "Awarded"
    assert sol.rfx_type == "RFQ"
    assert sol.title == "Toner Cartridges"
    assert sol.category == "Goods and Services"
    assert sol.division == "Purchasing & Materials Management"  # first of Client_Division array
    assert sol.submission_deadline == "2012-07-30"
    assert sol.ariba_posting_link is None  # "" normalized to None
    assert sol.odata_id == "da83db29-e4fc-4651-a9a3-d6bedd042e8c"
    assert sol.source == "odata"
    assert sol.wards is None
    assert award.supplier_name_raw == "Computer Media Group"
    assert award.award_amount == "26773.58"
    assert award.award_date == "2012-10-04"
    assert award.source == "odata"


def test_normalize_solicitation_awarded_suppliers_expansion_rules():
    from toronto_bids.models import Award
    rows = list(odata.normalize_solicitation(_value("odata_solicitation.json")))
    awards = [r for r in rows if isinstance(r, Award)]
    # Blank Successful_Bidder entry is skipped: 3 entries -> 2 awards.
    assert len(awards) == 2
    fallback = next(a for a in awards if a.supplier_name_raw == "Fallback Date Supplier")
    # Date_Awarded is "" -> falls back to AwardedDate.
    assert fallback.award_date == "2013-01-15"


def test_normalize_solicitation_skips_invalid_docnum_but_still_none_safe():
    raw = dict(_value("odata_solicitation.json"))
    raw["Solicitation_Document_Number"] = ""
    assert list(odata.normalize_solicitation(raw)) == []


def test_normalize_noncompetitive_uses_workspace_number():
    rows = list(odata.normalize_noncompetitive(_value("odata_noncompetitive.json")))
    nc = rows[0]
    assert isinstance(nc, NonCompetitive)
    assert nc.workspace_number == "8614"
    assert nc.reason == "Emergency"
    assert nc.supplier_name_raw == "Accuworx Inc"
    assert nc.contract_amount == "67896.4"
    assert nc.contract_date == "2015-07-29"
    assert nc.source == "odata"
    assert nc.odata_id == "66e002b8-5d66-4df0-b0d8-6cb628fda467"


def test_sources_are_spine_overwrite_true():
    assert odata.ODataSolicitationSource().overwrite is True
    assert odata.ODataNonCompetitiveSource().overwrite is True
