import base64
import json
from pathlib import Path

import httpx
import pytest

from toronto_bids import pipeline
from toronto_bids.http import HttpClient
from toronto_bids.models import AribaPosting
from toronto_bids.sources import ariba
from toronto_bids.sources.ariba import AribaDiscoverySource
from toronto_bids.store import db

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _http(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


SEARCH_BODY = {
    "totalNumberOfRecords": 3,
    "records": [
        {"rfxID": "1110015885", "customerName": "City of Toronto", "title": "RFT Watermain"},
        {"rfxID": "1110099999", "customerName": "City of Toronto", "title": "RFQ Widgets"},
        {"rfxID": "1110000001", "customerName": "TransLink", "title": "Other buyer"},
    ],
}


def test_fetch_keeps_only_toronto_and_pairs_detail():
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=SEARCH_BODY)
        # detail: 1110015885 succeeds, 1110099999 500s persistently
        if "1110015885" in request.url.path:
            return httpx.Response(200, json={"ID": "1110015885", "anid": "AN01050912625", "rfxIdExternal": "Doc5672751291"})
        return httpx.Response(500, text="boom")
    raws = list(AribaDiscoverySource().fetch(_http(handler)))
    # TransLink record dropped; two Toronto records kept.
    assert len(raws) == 2
    by_id = {r["search"]["rfxID"]: r for r in raws}
    assert by_id["1110015885"]["detail"]["rfxIdExternal"] == "Doc5672751291"
    # The 500'd posting is still yielded, with detail=None (per-posting isolation).
    assert by_id["1110099999"]["detail"] is None


def test_fetch_does_not_raise_when_all_details_fail():
    def handler(request):
        if request.url.path.endswith("/search"):
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
    assert p.status == "Published"
    assert p.customer_name == "City of Toronto"
    assert p.close_date == "2026-07-17T09:00:00-07:00"  # search endDate preferred
    assert p.currency == "CAD"
    assert p.amount_max == "99000000"                 # from detail opportunityAmount
    assert p.public_posting_url == "https://discovery.ariba.com/rfx/1110015885"
    assert p.sourcing_url is None  # new public detail does not expose the Sourcing URL
    assert json.loads(p.categories) == ["Sidewalk construction and repair service",
                                        "Water main construction service"]
    assert json.loads(p.raw_json)["rfxIdExternal"] == "Doc5672751291"  # snapshot present
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


def _filter(request):
    assert request.method == "GET"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Accept"] == "application/json"
    assert request.url.host == "portal.us.bn.cloud.ariba.com"
    return json.loads(base64.b64decode(request.url.params["filter"]))


def test_current_search_fetches_all_pages_without_buyer_names():
    filters = []
    def handler(request):
        if request.url.path.endswith("/search"):
            f = _filter(request)
            filters.append(f)
            assert f["orgAnid"] == "AN01050912625"
            assert f["freeText"] == ""
            assert f["searchType"] == "ALL_LEADS"
            if f["page"] == 1:
                assert f["searchAfter"] == []
                return httpx.Response(200, json={"totalNumberOfRecords": 2,
                    "records": [{"rfxID": "1110015885", "title": "First"}],
                    "searchAfter": ["1110015885"]})
            assert f["page"] == 2
            assert f["searchAfter"] == ["1110015885"]
            return httpx.Response(200, json={"totalNumberOfRecords": 2,
                "records": [{"rfxID": "1110099999", "title": "Second"}]})
        assert "RfxEvent('" in request.url.path
        assert request.url.params["$expand"] == "rfxEventSupCommodity,rfxEventTerritory,humanitarianAid"
        return httpx.Response(503)
    raws = list(AribaDiscoverySource().fetch(_http(handler)))
    assert [r["search"]["rfxID"] for r in raws] == ["1110015885", "1110099999"]
    assert all(r["detail"] is None for r in raws)
    assert [list(ariba.normalize_posting(r))[0].customer_name for r in raws] == ["City of Toronto"] * 2
    assert len(filters) == 2


def test_current_detail_normalizes_public_fields():
    detail = _fixture("ariba_detail.json")
    search = _fixture("ariba_search_record.json")
    search.pop("customerName")
    p = list(ariba.normalize_posting({"search": search, "detail": detail}))[0]
    assert p.customer_name == "City of Toronto"
    assert p.document_number == "5672751291"
    assert p.amount_min == "1000"
    assert p.amount_max == "99000000"
    assert p.currency == "CAD"
    assert json.loads(p.raw_json) == detail


@pytest.mark.parametrize("body", [
    {}, {"records": [], "totalNumberOfRecords": "0"},
    {"records": None, "totalNumberOfRecords": 0},
    {"records": [], "totalNumberOfRecords": 1},
    {"records": [{"rfxID": "1"}], "totalNumberOfRecords": 2},
])
def test_malformed_or_truncated_search_is_failure_not_empty_success(body):
    with pytest.raises(ValueError, match="Ariba"):
        list(AribaDiscoverySource().fetch(_http(lambda r: httpx.Response(200, json=body))))


def test_repeated_search_page_fails_instead_of_looping():
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"totalNumberOfRecords": 2,
                "records": [{"rfxID": "1"}], "searchAfter": ["1"]})
        return httpx.Response(503)
    with pytest.raises(ValueError, match="Ariba"):
        list(AribaDiscoverySource().fetch(_http(handler)))


def test_empty_search_records_success_and_preserves_archive(conn):
    db.upsert_row(conn, AribaPosting(rfx_id="old", source="ariba_discovery"), overwrite=True)
    http = _http(lambda r: httpx.Response(200, json={"totalNumberOfRecords": 0, "records": []}))
    assert pipeline.run_source(conn, http, AribaDiscoverySource()) == (0, 0, None)
    assert conn.execute("SELECT status FROM sync_run").fetchone()["status"] == "ok"
    assert db.counts(conn)["ariba_posting"] == 1


def test_search_failure_preserves_archive_and_other_sources_continue(conn):
    detail = _fixture("ariba_detail.json")
    for row in ariba.normalize_posting({"search": _fixture("ariba_search_record.json"), "detail": detail}):
        db.upsert_row(conn, row, overwrite=True)
    class FollowingSource:
        name = "following"
        overwrite = True
        def fetch(self, http):
            return [{"search": {"rfxID": "2"}, "detail": None}]
        normalize = staticmethod(ariba.normalize_posting)
    failures = pipeline.sync(conn, _http(lambda r: httpx.Response(503)),
        sources=[AribaDiscoverySource(), FollowingSource()])
    assert [name for name, _ in failures] == ["ariba_discovery"]
    rows = conn.execute("SELECT source, status FROM sync_run ORDER BY id LIMIT 2").fetchall()
    assert [(r["source"], r["status"]) for r in rows] == [("ariba_discovery", "failed"), ("following", "ok")]
    assert db.counts(conn)["ariba_posting"] == 2
    assert conn.execute("SELECT raw_json FROM ariba_posting WHERE rfx_id='1110015885'").fetchone()[0] == json.dumps(detail)


def test_detail_failure_does_not_erase_existing_snapshot(conn):
    search = _fixture("ariba_search_record.json")
    detail = _fixture("ariba_detail.json")
    for row in ariba.normalize_posting({"search": search, "detail": detail}):
        db.upsert_row(conn, row, overwrite=True)
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"totalNumberOfRecords": 1, "records": [search]})
        return httpx.Response(503)
    assert pipeline.run_source(conn, _http(handler), AribaDiscoverySource()) == (1, 1, None)
    saved = conn.execute("SELECT document_number,raw_json FROM ariba_posting").fetchone()
    assert saved["document_number"] == "5672751291"
    assert json.loads(saved["raw_json"]) == detail


def test_detail_for_another_buyer_is_rejected():
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"totalNumberOfRecords": 1, "records": [{"rfxID": "1"}]})
        return httpx.Response(200, json={"ID": "1", "anid": "AN_OTHER_BUYER"})
    with pytest.raises(ValueError, match="Ariba.*buyer"):
        list(AribaDiscoverySource().fetch(_http(handler)))
