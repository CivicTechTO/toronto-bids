import json
from pathlib import Path

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.models import AribaPosting
from toronto_bids.sources import ariba
from toronto_bids.sources.ariba import AribaDiscoverySource

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _http(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


SEARCH_BODY = {
    "totalNumberOfRecords": 3,
    "solarRecords": [
        {"rfxID": "1110015885", "customerName": "City of Toronto", "title": "RFT Watermain"},
        {"rfxID": "1110099999", "customerName": "City of Toronto", "title": "RFQ Widgets"},
        {"rfxID": "1110000001", "customerName": "TransLink", "title": "Other buyer"},
    ],
}


def test_fetch_keeps_only_toronto_and_pairs_detail():
    def handler(request):
        if "doIndexedSearch" in str(request.url):
            return httpx.Response(200, json=SEARCH_BODY)
        # detail: 1110015885 succeeds, 1110099999 500s persistently
        if request.url.path.endswith("1110015885"):
            return httpx.Response(200, json={"id": "1110015885", "externalRfxId": "Doc5672751291"})
        return httpx.Response(500, text="boom")
    raws = list(AribaDiscoverySource().fetch(_http(handler)))
    # TransLink record dropped; two Toronto records kept.
    assert len(raws) == 2
    by_id = {r["search"]["rfxID"]: r for r in raws}
    assert by_id["1110015885"]["detail"]["externalRfxId"] == "Doc5672751291"
    # The 500'd posting is still yielded, with detail=None (per-posting isolation).
    assert by_id["1110099999"]["detail"] is None


def test_fetch_does_not_raise_when_all_details_fail():
    def handler(request):
        if "doIndexedSearch" in str(request.url):
            return httpx.Response(200, json=SEARCH_BODY)
        return httpx.Response(500, text="boom")
    raws = list(AribaDiscoverySource().fetch(_http(handler)))
    assert len(raws) == 2
    assert all(r["detail"] is None for r in raws)


def test_source_attributes():
    src = AribaDiscoverySource()
    assert src.name == "ariba_discovery"
    assert src.overwrite is True


def test_normalize_with_detail_bridges_and_snapshots():
    raw = {"search": _fixture("ariba_search_record.json"), "detail": _fixture("ariba_detail.json")}
    posts = list(ariba.normalize_posting(raw))
    assert len(posts) == 1
    p = posts[0]
    assert isinstance(p, AribaPosting)
    assert p.rfx_id == "1110015885"
    assert p.document_number == "5672751291"          # bridged from externalRfxId
    assert p.external_rfx_id == "Doc5672751291"
    assert p.status == "PUBLISHED"
    assert p.customer_name == "City of Toronto"
    assert p.close_date == "2026-07-17T09:00:00-07:00"  # search endDate preferred
    assert p.currency == "CAD"
    assert p.amount_max == "99000000"                 # from detail opportunityAmount
    assert p.public_posting_url == "https://discovery.ariba.com/rfx/1110015885"
    assert "s1.ariba.com" in p.sourcing_url
    assert json.loads(p.categories) == ["Sidewalk construction and repair service",
                                        "Water main construction service"]
    assert json.loads(p.raw_json)["externalRfxId"] == "Doc5672751291"  # snapshot present
    assert p.source == "ariba_discovery"


def test_normalize_without_detail_archives_search_only():
    raw = {"search": _fixture("ariba_search_record.json"), "detail": None}
    p = list(ariba.normalize_posting(raw))[0]
    assert p.rfx_id == "1110015885"
    assert p.document_number is None          # no externalRfxId, no Doc in this title
    assert p.raw_json is None                 # nothing to snapshot
    assert p.external_rfx_id is None
    assert p.title == "Request for Tenders for Watermain and Sewer Replacement on various roads"
    assert p.customer_name == "City of Toronto"
    assert p.amount_max == "70542967.0799487"  # falls back to search maxAmount
    assert p.currency is None
    assert json.loads(p.categories) == ["Sidewalk construction and repair service",
                                        "Sewer line construction service"]


def test_normalize_without_detail_bridges_title_embedded_doc():
    search = dict(_fixture("ariba_search_record.json"))
    search["title"] = "Doc5581608073 - Request for Quotations for supplies"
    p = list(ariba.normalize_posting({"search": search, "detail": None}))[0]
    assert p.document_number == "5581608073"   # bridged from the title
