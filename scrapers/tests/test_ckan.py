import json

import httpx

from toronto_bids.http import HttpClient
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
