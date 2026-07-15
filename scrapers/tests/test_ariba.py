import httpx

from toronto_bids.http import HttpClient
from toronto_bids.sources.ariba import AribaDiscoverySource


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
