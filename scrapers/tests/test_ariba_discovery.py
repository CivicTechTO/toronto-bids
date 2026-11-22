import base64
import json
from pathlib import Path

import httpx
import pytest

from toronto_bids import pipeline
from toronto_bids.http import HttpClient
from toronto_bids.linking.ariba import bridge_postings_to_spine, rfx_id_from_link
from toronto_bids.models import AribaPosting, Solicitation
from toronto_bids.sources import ariba
from toronto_bids.sources.ariba import AribaDiscoverySource
from toronto_bids.store import db

FIXTURES = Path(__file__).parent / "fixtures"

SEARCH_BODY = {
    "totalNumberOfRecords": 3,
    "records": [
        {"rfxID": "1110015885", "customerName": "City of Toronto", "title": "RFT Watermain"},
        {"rfxID": "1110099999", "customerName": "City of Toronto", "title": "RFQ Widgets"},
        {"rfxID": "1110000001", "customerName": "TransLink", "title": "Other buyer"},
    ],
}

_PREVIEW_LINK = (
    "https://portal.us.bn.cloud.ariba.com/dashboard/public/appext/comsapsbncdiscoveryui"
    "#/RfxEvent/preview/1110017742?anId=ANONYMOUS"
)


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _http(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


def _filter(request):
    assert request.method == "GET"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Accept"] == "application/json"
    assert request.url.host == "portal.us.bn.cloud.ariba.com"
    return json.loads(base64.b64decode(request.url.params["filter"]))


def _seed_bridge(conn, *, link=_PREVIEW_LINK, doc="5732578927", rfx="1110017742", bridged=None):
    if doc is not None:
        db.upsert_row(
            conn,
            Solicitation(document_number=doc, status="Open", ariba_posting_link=link, source="odata"),
            overwrite=True,
        )
    db.upsert_row(
        conn,
        AribaPosting(rfx_id=rfx, document_number=bridged, source="ariba_discovery"),
        overwrite=True,
    )
    conn.commit()


def test_fetch_keeps_only_toronto_and_pairs_detail():
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=SEARCH_BODY)
        if "1110015885" in request.url.path:
            return httpx.Response(
                200, json={"ID": "1110015885", "anid": "AN01050912625", "rfxIdExternal": "Doc5672751291"}
            )
        return httpx.Response(500, text="boom")

    raws = list(AribaDiscoverySource().fetch(_http(handler)))
    assert len(raws) == 2
    by_id = {r["search"]["rfxID"]: r for r in raws}
    assert by_id["1110015885"]["detail"]["rfxIdExternal"] == "Doc5672751291"
    assert by_id["1110099999"]["detail"] is None


def test_normalize_with_detail_bridges_and_snapshots():
    search = _fixture("ariba_search_record.json")
    search.pop("customerName")
    detail = _fixture("ariba_detail.json")
    posts = list(ariba.normalize_posting({"search": search, "detail": detail}))
    assert len(posts) == 1
    p = posts[0]
    assert isinstance(p, AribaPosting)
    assert p.rfx_id == "1110015885"
    assert p.document_number == "5672751291"
    assert p.external_rfx_id == "Doc5672751291"
    assert p.status == "Published"
    assert p.customer_name == "City of Toronto"
    assert p.close_date == "2026-07-17T09:00:00-07:00"
    assert p.currency == "CAD"
    assert p.amount_min == "1000"
    assert p.amount_max == "99000000"
    assert p.public_posting_url == "https://discovery.ariba.com/rfx/1110015885"
    assert p.sourcing_url is None
    assert json.loads(p.categories) == [
        "Sidewalk construction and repair service",
        "Water main construction service",
    ]
    assert json.loads(p.raw_json) == detail
    assert p.source == "ariba_discovery"


def test_normalize_without_detail_archives_search_only():
    raw = {"search": _fixture("ariba_search_record.json"), "detail": None}
    p = list(ariba.normalize_posting(raw))[0]
    assert p.rfx_id == "1110015885"
    assert p.document_number is None
    assert p.raw_json is None
    assert p.external_rfx_id is None
    assert p.title == "Request for Tenders for Watermain and Sewer Replacement on various roads"
    assert p.customer_name == "City of Toronto"
    assert p.amount_max == "70542967.0799487"
    assert p.currency is None
    assert json.loads(p.categories) == [
        "Sidewalk construction and repair service",
        "Sewer line construction service",
    ]


def test_normalize_without_detail_bridges_title_embedded_doc():
    search = dict(_fixture("ariba_search_record.json"))
    search["title"] = "Doc5581608073 - Request for Quotations for supplies"
    p = list(ariba.normalize_posting({"search": search, "detail": None}))[0]
    assert p.document_number == "5581608073"


def test_current_search_fetches_all_pages_and_tolerates_detail_outage():
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
                return httpx.Response(
                    200,
                    json={
                        "totalNumberOfRecords": 2,
                        "records": [{"rfxID": "1110015885", "title": "First"}],
                        "searchAfter": ["1110015885"],
                    },
                )
            assert f["page"] == 2
            assert f["searchAfter"] == ["1110015885"]
            return httpx.Response(
                200,
                json={"totalNumberOfRecords": 2, "records": [{"rfxID": "1110099999", "title": "Second"}]},
            )
        assert "RfxEvent('" in request.url.path
        assert request.url.params["$expand"] == "rfxEventSupCommodity,rfxEventTerritory,humanitarianAid"
        return httpx.Response(503)

    raws = list(AribaDiscoverySource().fetch(_http(handler)))
    assert [r["search"]["rfxID"] for r in raws] == ["1110015885", "1110099999"]
    assert all(r["detail"] is None for r in raws)
    assert [list(ariba.normalize_posting(r))[0].customer_name for r in raws] == ["City of Toronto"] * 2
    assert len(filters) == 2


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"records": [], "totalNumberOfRecords": "0"},
        {"records": None, "totalNumberOfRecords": 0},
        {"records": [], "totalNumberOfRecords": 1},
        {"records": [{"rfxID": "1"}], "totalNumberOfRecords": 2},
    ],
)
def test_malformed_or_truncated_search_is_failure_not_empty_success(body):
    with pytest.raises(ValueError, match="Ariba"):
        list(AribaDiscoverySource().fetch(_http(lambda r: httpx.Response(200, json=body))))


def test_repeated_search_page_fails_instead_of_looping():
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={"totalNumberOfRecords": 2, "records": [{"rfxID": "1"}], "searchAfter": ["1"]},
            )
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

    failures = pipeline.sync(
        conn,
        _http(lambda r: httpx.Response(503)),
        sources=[AribaDiscoverySource(), FollowingSource()],
    )
    assert [name for name, _ in failures] == ["ariba_discovery"]
    rows = conn.execute("SELECT source, status FROM sync_run ORDER BY id LIMIT 2").fetchall()
    assert [(r["source"], r["status"]) for r in rows] == [("ariba_discovery", "failed"), ("following", "ok")]
    assert db.counts(conn)["ariba_posting"] == 2
    assert (
        conn.execute("SELECT raw_json FROM ariba_posting WHERE rfx_id='1110015885'").fetchone()[0]
        == json.dumps(detail)
    )


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
    saved = conn.execute("SELECT document_number, raw_json FROM ariba_posting").fetchone()
    assert saved["document_number"] == "5672751291"
    assert json.loads(saved["raw_json"]) == detail


def test_detail_for_another_buyer_is_rejected():
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"totalNumberOfRecords": 1, "records": [{"rfxID": "1"}]})
        return httpx.Response(200, json={"ID": "1", "anid": "AN_OTHER_BUYER"})

    with pytest.raises(ValueError, match="Ariba.*buyer"):
        list(AribaDiscoverySource().fetch(_http(handler)))


@pytest.mark.parametrize(
    "link,expected",
    [
        (_PREVIEW_LINK, "1110017742"),
        ("https://discovery.ariba.com/rfx/22538756", None),
        ("https://www.merx.com/solicitations/open-bids/Jack-Layton", None),
        ("n/a", None),
        (None, None),
    ],
)
def test_rfx_id_from_link(link, expected):
    assert rfx_id_from_link(link) == expected


def test_bridges_unbridged_posting_via_spine_link_idempotently(conn):
    _seed_bridge(conn)
    assert bridge_postings_to_spine(conn) == 1
    assert (
        conn.execute("SELECT document_number FROM ariba_posting WHERE rfx_id='1110017742'").fetchone()[0]
        == "5732578927"
    )
    assert bridge_postings_to_spine(conn) == 0


@pytest.mark.parametrize(
    "seed_kwargs,rfx,expected_doc",
    [
        pytest.param({"bridged": "5732578927"}, "1110017742", "5732578927", id="already_bridged"),
        pytest.param(
            {"link": "https://discovery.ariba.com/rfx/22538756", "rfx": "22538756"},
            "22538756",
            None,
            id="dead_link",
        ),
        pytest.param({"doc": None, "rfx": "9999999999"}, "9999999999", None, id="no_matching_spine"),
    ],
)
def test_bridge_noop_cases(conn, seed_kwargs, rfx, expected_doc):
    _seed_bridge(conn, **seed_kwargs)
    assert bridge_postings_to_spine(conn) == 0
    assert (
        conn.execute("SELECT document_number FROM ariba_posting WHERE rfx_id=?", (rfx,)).fetchone()[0]
        == expected_doc
    )
