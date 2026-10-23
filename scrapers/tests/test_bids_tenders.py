import json
import pathlib
import sqlite3

import pytest

from toronto_bids.buyers import seed_buyers
from toronto_bids.models import AgencySolicitation
from toronto_bids.sources.bids_tenders import parse_listing, store_listings
from toronto_bids.store import db

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "agencies"


def _legacy_sample():
    return json.loads((FIXTURES / "bids_tenders_legacy_synthetic.json").read_text())


def test_legacy_listing_field_aliases_remain_supported():
    row = parse_listing(_legacy_sample(), buyer_id=7)
    assert row.buyer_id == 7
    assert row.native_ref == "RFT-2026-014"          # ReferenceNumber, normalized
    assert row.title == "Trail Bridge Replacement - Example Creek"
    assert row.status == "Open"
    assert row.closing_date == "2026-08-15T14:00:00"
    assert row.posted_date == "2026-07-20T09:00:00"
    assert row.portal_url.endswith("/Tender/Detail/0f9a1b2c-3d4e-5f60-7182-93a4b5c6d7e8")
    assert row.source == "bids_tenders"




@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_store_listings_inserts_portal_row(conn):
    ids = seed_buyers(conn)
    n = store_listings(conn, [_legacy_sample()], ids)
    assert n == 1
    row = conn.execute("SELECT native_ref, title, status, source FROM portal_listing").fetchone()
    assert row["native_ref"] == "RFT-2026-014"
    assert row["source"] == "bids_tenders"
    assert row["title"] == "Trail Bridge Replacement - Example Creek"


def test_portal_reference_alone_does_not_enrich_or_join_a_board_row(conn):
    ids = seed_buyers(conn)
    db.upsert_row(conn, AgencySolicitation(
        buyer_id=ids["trca"], native_ref="RFT-2026-014", title="Board title",
        status="awarded", source="trca_board"), overwrite=False)
    store_listings(conn, [_legacy_sample()], ids)
    board = conn.execute('SELECT * FROM agency_solicitation').fetchone()
    portal = conn.execute('SELECT * FROM portal_listing').fetchone()
    assert board['title'] == 'Board title' and board['closing_date'] is None
    assert portal['closing_date'] == '2026-08-15T14:00:00'
    assert board['procurement_id'] != portal['procurement_id']


def test_store_listings_empty_is_noop(conn):
    ids = seed_buyers(conn)
    assert store_listings(conn, [], ids) == 0


def test_record_listings_writes_one_file_per_record(tmp_path):
    from toronto_bids.sources.bids_tenders import record_listings
    recs = [dict(_legacy_sample(), status_code=1), dict(_legacy_sample(), status_code=3)]
    n = record_listings(recs, tmp_path)
    assert n == 2
    written = sorted(p.name for p in tmp_path.glob("*.json"))
    assert all(name.startswith("trca-") for name in written)


def test_run_portal_capture_isolates_a_failing_body(conn, monkeypatch):
    from toronto_bids.sources import bids_tenders as bt
    ids = seed_buyers(conn)

    def fake_fetch(portal, **_kw):
        if portal["slug"] == "trca":
            raise RuntimeError("boom")            # one body fails
        yield dict(_legacy_sample(), buyer_slug="toronto-zoo")

    monkeypatch.setattr(bt, "fetch_listings", fake_fetch)
    result = bt.run_portal_capture(conn, log=lambda _m: None)
    assert result["toronto-zoo"] == 1             # zoo still captured
    assert "trca" in result and result["trca"] == "FAILED: boom"   # trca isolated, recorded


def test_nightly_wires_up_the_portal_step():
    """Doc-smoke: `tb nightly` (cli._run_nightly) actually calls into this module, isolated
    the same way sync/award_summary are (#135 asks it never stop the export)."""
    import inspect

    from toronto_bids import cli
    src = inspect.getsource(cli._run_nightly)
    assert "bids_tenders import run_portal_capture" in src
    assert "run_portal_capture(conn" in src


# --- #226: a broken reply is a FAILURE, never "no open bids" -------------------------------

_LANDING_HTML = ('<input id="NodeId" type="hidden" value="node-1">'
                 '<input name="__RequestVerificationToken" type="hidden" value="tok-1">')

# The shape fetch_listings reads: a `success` flag beside the `data` page and a `total`.
_EMPTY = {"success": True, "data": [], "total": 0}


def _fake_portal(monkeypatch, search_reply):
    """Route bids_tenders' own httpx.Client through a MockTransport: the landing GET serves a
    page carrying NodeId + token, and every search POST is answered by
    `search_reply(slug, request) -> httpx.Response`. No network, no rate-limit sleep."""
    import types

    import httpx

    from toronto_bids.sources import bids_tenders as bt

    real_client = httpx.Client

    def handler(request):
        slug = "toronto-zoo" if request.url.host.startswith("torontozoo") else "trca"
        if request.method == "GET":
            return httpx.Response(200, text=_LANDING_HTML)
        return search_reply(slug, request)

    def client_factory(**kw):
        return real_client(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(bt.httpx, "Client", client_factory)
    monkeypatch.setattr(bt, "time", types.SimpleNamespace(sleep=lambda _s: None))
    return bt


def test_empty_portal_reply_is_zero_rows_not_a_failure(conn, monkeypatch):
    import httpx
    bt = _fake_portal(monkeypatch, lambda _slug, _req: httpx.Response(200, json=_EMPTY))
    assert bt.run_portal_capture(conn, log=lambda _m: None) == {"trca": 0, "toronto-zoo": 0}


def test_payload_without_success_field_is_not_a_failure(conn, monkeypatch):
    import httpx
    bt = _fake_portal(monkeypatch, lambda _slug, _req: httpx.Response(
        200, json={"data": [], "total": 0}))
    assert bt.run_portal_capture(conn, log=lambda _m: None) == {"trca": 0, "toronto-zoo": 0}


def test_non_json_reply_is_reported_failed_and_other_portal_still_runs(conn, monkeypatch):
    import httpx

    def reply(slug, _req):
        if slug == "trca":   # e.g. the Error?aspxerrorpath HTML page a bad request lands on
            return httpx.Response(200, text="<html>Error</html>")
        return httpx.Response(200, json=_EMPTY)

    bt = _fake_portal(monkeypatch, reply)
    result = bt.run_portal_capture(conn, log=lambda _m: None)
    assert result["toronto-zoo"] == 0                    # the other portal still ran
    assert isinstance(result["trca"], str) and result["trca"].startswith("FAILED")
    assert "trca" in result["trca"] and "non-JSON" in result["trca"]
    assert "HTTP 200" in result["trca"] and "status=All" in result["trca"]


def test_success_false_payload_is_reported_failed(conn, monkeypatch):
    import httpx
    bt = _fake_portal(monkeypatch, lambda _slug, _req: httpx.Response(
        200, json={"success": False, "data": [], "total": 0}))
    result = bt.run_portal_capture(conn, log=lambda _m: None)
    for slug in ("trca", "toronto-zoo"):
        assert result[slug].startswith("FAILED") and "success=false" in result[slug]


def test_non_2xx_reply_is_reported_failed(conn, monkeypatch):
    import httpx
    bt = _fake_portal(monkeypatch, lambda _slug, _req: httpx.Response(500, json=_EMPTY))
    result = bt.run_portal_capture(conn, log=lambda _m: None)
    assert result["trca"].startswith("FAILED") and "HTTP 500" in result["trca"]


@pytest.fixture(autouse=True)
def isolated_portal_recordings(monkeypatch, tmp_path):
    from toronto_bids import config
    monkeypatch.setattr(config, 'PORTAL_RECORDINGS_DIR', tmp_path / 'recordings')


def _real_trca_records():
    return [dict(r, buyer_slug='trca') for r in json.loads(
        (FIXTURES / 'bids_tenders_trca_2026-10-20.json').read_text())]


def test_real_trca_search_uses_the_ui_all_filter_and_stable_sort(monkeypatch):
    import httpx
    from toronto_bids import config
    records = _real_trca_records()
    requests = []
    def reply(slug, req):
        requests.append(req)
        valid = req.url.params.get('status') == ''
        return httpx.Response(200, json={'success': True, 'data': records if valid else [],
                                         'total': len(records) if valid else 0})
    bt = _fake_portal(monkeypatch, reply)
    portal = next(p for p in config.BIDS_TENDERS_PORTALS if p['slug'] == 'trca')
    got = list(bt.fetch_listings(portal))
    assert len(got) == len(records)
    assert len(requests) == 1
    assert requests[0].url.params['sort'] == 'DateClosing DESC,Id'
    assert requests[0].url.params['from'] == requests[0].url.params['to'] == ''


@pytest.mark.parametrize('index,reference,posted,closed', [
    (0, '10044383', '2026-03-24T19:10:00+00:00', '2026-05-19T20:00:00+00:00'),
    (1, '10042501', '2025-02-05T16:15:00+00:00', '2025-03-07T21:00:00+00:00'),
    (2, '10022400_33', '2024-06-07T18:15:00+00:00', '2024-06-19T15:00:00+00:00'),
])
def test_real_trca_listing_identity_and_epoch_dates(index, reference, posted, closed):
    row = parse_listing(_real_trca_records()[index], buyer_id=7)
    assert row.native_ref == reference
    assert row.posted_date == posted and row.closing_date == closed
    assert row.status == _real_trca_records()[index]['Status']


def test_real_trca_call_suffix_is_not_merged_with_the_parent_call():
    from toronto_bids.linking.agency_reference import normalize_agency_reference
    assert normalize_agency_reference('trca', '10022400_33') == '10022400_33'
    assert normalize_agency_reference('trca', 'RFT 10022400_32') == '10022400_32'


def test_identifierless_page_does_not_invent_a_reference():
    rec = _legacy_sample()
    del rec['ReferenceNumber']
    row = parse_listing(rec, buyer_id=7)
    assert row.native_ref is None
    assert row.portal_url.endswith(rec['Id'])


def test_nonempty_capture_records_automatically_before_parsing(conn, monkeypatch, tmp_path):
    from toronto_bids import config
    from toronto_bids.sources import bids_tenders as bt
    portal = next(p for p in config.BIDS_TENDERS_PORTALS if p['slug'] == 'trca')
    monkeypatch.setattr(config, 'BIDS_TENDERS_PORTALS', [portal])
    invalid = dict(_legacy_sample())
    invalid['DateAvailable'] = 'invalid date'
    monkeypatch.setattr(bt, 'fetch_listings', lambda *a, **k: iter([invalid]))
    result = bt.run_portal_capture(conn)
    assert result['trca'].startswith('FAILED')
    recordings = list((tmp_path / 'recordings').glob('*.json'))
    assert len(recordings) == 1 and json.loads(recordings[0].read_text()) == invalid
    assert conn.execute('SELECT count(*) FROM agency_solicitation').fetchone()[0] == 0


@pytest.mark.parametrize('payload', [{}, {'data': []}, {'total': 0},
    {'data': {}, 'total': 0}, {'data': [], 'total': -1}, {'data': [], 'total': '0'},
    {'data': [None], 'total': 1}, {'data': [], 'total': 1}])
def test_malformed_or_incomplete_pages_cannot_look_empty(conn, monkeypatch, payload):
    import httpx
    bt = _fake_portal(monkeypatch, lambda *a: httpx.Response(200, json=payload))
    assert all(str(v).startswith('FAILED') for v in bt.run_portal_capture(conn).values())


def test_incomplete_pagination_is_a_failure(conn, monkeypatch, tmp_path):
    import httpx
    rows = _real_trca_records()
    def reply(slug, req):
        return httpx.Response(200, json={'data': rows if req.url.params['start'] == '0' else [],
                                         'total': len(rows) + 1})
    bt = _fake_portal(monkeypatch, reply)
    assert all(str(v).startswith('FAILED') for v in bt.run_portal_capture(conn).values())
    assert len(list((tmp_path / 'recordings').glob('*.json'))) == 2 * len(rows)


def test_repeated_pages_are_refused_instead_of_inflating_recall(conn, monkeypatch):
    import httpx
    rows = _real_trca_records()
    bt = _fake_portal(monkeypatch, lambda *a: httpx.Response(200, json={'data': rows, 'total': len(rows) * 2}))
    assert all(str(v).startswith('FAILED') for v in bt.run_portal_capture(conn).values())


def test_recording_keeps_changed_snapshots_and_deduplicates_identical_ones(tmp_path):
    from toronto_bids.sources.bids_tenders import record_listings
    first = _legacy_sample()
    record_listings([first], tmp_path)
    record_listings([first], tmp_path)
    record_listings([dict(first, StatusText='Closed')], tmp_path)
    assert len(list(tmp_path.glob('*.json'))) == 2
    assert {json.loads(p.read_text())['StatusText'] for p in tmp_path.glob('*.json')} == {'Open', 'Closed'}


def test_batch_identity_validation_happens_before_any_upsert(conn):
    ids = seed_buyers(conn)
    invalid = dict(_legacy_sample(), Id=None)
    with pytest.raises(ValueError, match='identity'):
        store_listings(conn, [_legacy_sample(), invalid], ids)
    assert conn.execute('SELECT count(*) FROM portal_listing').fetchone()[0] == 0


def test_real_hyphenated_trca_call_suffix_is_preserved():
    from toronto_bids.linking.agency_reference import normalize_agency_reference
    row = parse_listing(_real_trca_records()[3], buyer_id=7)
    assert row.native_ref == '10044944-1'
    assert normalize_agency_reference('trca', 'Contract 10044944-1') == '10044944-1'
