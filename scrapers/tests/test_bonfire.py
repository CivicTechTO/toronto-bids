"""Exercise the complete anonymous adapter with actual public Library responses."""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from toronto_bids import cli, config
from toronto_bids.export.document import build_export_document
from toronto_bids.sources import bonfire as bf

FIXTURES = Path(__file__).parent / 'fixtures' / 'bonfire'
NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)
PORTAL = config.BONFIRE_PORTALS[0]
LANDING = '/portal/'
OPEN = bf.SECTIONS['open']
PAST = bf.SECTIONS['past']


def source_data():
    return {LANDING: (FIXTURES / 'library-portal.html').read_bytes(),
            OPEN: (FIXTURES / 'library-open.json').read_bytes(),
            PAST: (FIXTURES / 'library-past.json').read_bytes()}


def run(conn, tmp_path, *, bodies=None, responses=None, portal=PORTAL):
    bodies = bodies or source_data()
    calls, waits = [], []

    def handle(request):
        calls.append(str(request.url))
        if responses and request.url.path in responses and responses[request.url.path]:
            status, headers = responses[request.url.path].pop(0)
            return httpx.Response(status, headers=headers, content=b'{"error":"slow down"}')
        return httpx.Response(200, content=bodies[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        count = bf.capture_portal(conn, portal, client=client, data_dir=tmp_path,
                                  sleep=waits.append, clock=lambda: NOW)
    return count, calls, waits


def test_full_real_history_identity_dates_and_exports(conn, tmp_path):
    count, calls, waits = run(conn, tmp_path)
    assert count == 412 and len(calls) == 3 and waits == [1, 1, 1]
    assert {httpx.URL(u).path for u in calls} == {LANDING, OPEN, PAST}
    rows = [dict(r) for r in conn.execute('SELECT * FROM portal_listing ORDER BY portal_url')]
    assert Counter(r['status'] for r in rows) == {'Closed': 400, 'Cancelled': 11, 'Open': 1}
    assert all(r['procurement_id'] and r['source'] == 'bonfire' and r['posted_date'] is None for r in rows)
    assert sum(r['native_ref'] == '221-21-TPL-RFP' for r in rows) == 4
    assert len({r['procurement_id'] for r in rows}) == 412
    open_row = next(r for r in rows if r['status'] == 'Open')
    assert open_row['closing_date'] == '2026-11-13T16:00:00+00:00'
    assert open_row['native_ref'] == '210-26-TPL-RFP'
    assert all(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
               for table in ('agency_award', 'agency_bid', 'agency_solicitation', 'solicitation', 'award'))
    document = build_export_document(conn, generated_at='fixed')
    buyer = next(b for b in document['buyers'] if b['slug'] == PORTAL['slug'])
    assert len(buyer['portal_listings']) == 412 and not buyer['awards'] and not buyer['bids']
    assert any(a['slug'] == PORTAL['slug'] for a in document['meta']['attribution'])
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert len(list((tmp_path / 'agencies' / 'bonfire' / PORTAL['slug']).glob('*.bin'))) == 3


def test_refresh_preserves_ids_first_seen_and_old_raw_observations(conn, tmp_path):
    run(conn, tmp_path)
    before = dict(conn.execute("SELECT * FROM portal_listing WHERE native_ref='210-26-TPL-RFP'").fetchone())
    bodies = source_data()
    opened = json.loads(bodies[OPEN])
    record = opened['payload']['projects']['112329']
    record['ProjectName'] = 'Updated published title'
    record['DateClose'] = '2026-11-20 16:00:00'
    record['ReferenceID'] = None
    bodies[OPEN] = json.dumps(opened).encode()
    run(conn, tmp_path, bodies=bodies)
    after = dict(conn.execute('SELECT * FROM portal_listing WHERE id=?', (before['id'],)).fetchone())
    for field in ('id', 'procurement_id', 'first_seen', 'native_ref'):
        assert after[field] == before[field]
    assert after['title'] == 'Updated published title' and after['closing_date'] == '2026-11-20T16:00:00+00:00'
    assert len(list((tmp_path / 'agencies' / 'bonfire' / PORTAL['slug']).glob('*.bin'))) == 4
    bodies[OPEN] = b'{"success":true,"payload":{"projects":[]}}'
    run(conn, tmp_path, bodies=bodies)
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 412
    assert dict(conn.execute('SELECT * FROM portal_listing WHERE id=?', (before['id'],)).fetchone()) == after


@pytest.mark.parametrize('bad', [
    b'<html>Login required</html>', b'{"success":false,"message":"error"}',
    b'{"success":true,"payload":{}}', b'{"success":true,"payload":{"projects":null}}',
    b'{"success":true,"payload":{"projects":[],"total":411}}',
    b'{"success":true,"payload":{"projects":[],"projects":{}}}',
])
def test_bad_reply_retains_bytes_and_writes_no_partial_listings(conn, tmp_path, bad):
    bodies = source_data(); bodies[PAST] = bad
    with pytest.raises((ValueError, TypeError)):
        run(conn, tmp_path, bodies=bodies)
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 0
    assert bad in [p.read_bytes() for p in (tmp_path / 'agencies' / 'bonfire' / PORTAL['slug']).glob('*.bin')]


@pytest.mark.parametrize('field,value', [('ProjectVisibilityID', '2'), ('ProjectStatusID', '99'),
    ('DateClose', '2026-02-30 16:00:00'), ('ProjectName', ''), ('ReferenceID', 42),
    ('ProjectID', '../private'), ('IsPublicAward', 'false')])
def test_unknown_or_private_record_is_refused_before_any_listing_write(conn, tmp_path, field, value):
    bodies = source_data(); data = json.loads(bodies[OPEN])
    data['payload']['projects']['112329'][field] = value
    bodies[OPEN] = json.dumps(data).encode()
    with pytest.raises(ValueError):
        run(conn, tmp_path, bodies=bodies)
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 0


def test_429_honors_retry_after_seconds(conn, tmp_path):
    count, calls, waits = run(conn, tmp_path, responses={OPEN: [(429, {'Retry-After': '9'})]})
    assert count == 412 and len(calls) == 4 and waits == [1, 1, 9, 1, 1]


def test_retry_after_http_date_is_honored(conn, tmp_path):
    count, calls, waits = run(conn, tmp_path, responses={OPEN: [(503, {'Retry-After': 'Thu, 22 Oct 2026 15:00:12 GMT'})]})
    assert count == 412 and len(calls) == 4 and waits == [1, 1, 12, 1, 1]


def test_long_retry_after_defers_without_early_retry(conn, tmp_path):
    with pytest.raises(RuntimeError, match='retry deferred'):
        run(conn, tmp_path, responses={OPEN: [(429, {'Retry-After': '120'})]})
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 0
    manifests = [json.loads(p.read_text()) for p in (tmp_path / 'agencies' / 'bonfire' / PORTAL['slug']).glob('*.json')]
    assert any(p['status'] == 429 and p['retry_after'] == '120' for p in manifests)


def test_server_retries_are_bounded_and_exponential(conn, tmp_path):
    calls, waits = [], []
    def handle(request):
        calls.append(request)
        if request.url.path == LANDING:
            return httpx.Response(200, content=source_data()[LANDING])
        return httpx.Response(503, content=b'unavailable')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            bf.capture_portal(conn, PORTAL, client=client, data_dir=tmp_path, sleep=waits.append, clock=lambda: NOW)
    assert len(calls) == 5 and waits == [1, 1, 2, 1, 4, 1, 8, 1]


@pytest.mark.parametrize('status', [302, 401, 403, 404])
def test_redirects_and_client_errors_are_not_retried(conn, tmp_path, status):
    calls, waits = [], []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={'Location': '/login'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            bf.capture_portal(conn, PORTAL, client=client, data_dir=tmp_path, sleep=waits.append)
    assert len(calls) == 1 and waits == [1]


def test_disabled_portal_has_no_requests(conn, tmp_path):
    with pytest.raises(PermissionError):
        run(conn, tmp_path, portal=dict(PORTAL, enabled=False))
    assert not (tmp_path / 'agencies').exists()


def test_public_award_status_never_creates_an_award_fact(conn, tmp_path):
    bodies = source_data(); data = json.loads(bodies[PAST])
    data['payload']['projects']['925']['IsPublicAward'] = True
    bodies[PAST] = json.dumps(data).encode()
    run(conn, tmp_path, bodies=bodies)
    assert conn.execute("SELECT status FROM portal_listing WHERE portal_url LIKE '%/925'").fetchone()[0] == 'Awarded'
    assert conn.execute('SELECT COUNT(*) FROM agency_award').fetchone()[0] == 0


def test_standalone_propagates_per_buyer_failure(conn, monkeypatch):
    monkeypatch.setattr(cli, '_open_db', lambda: conn)
    monkeypatch.setattr(bf, 'run_bonfire_capture', lambda *a, **k: {PORTAL['slug']: 'FAILED: unavailable'})
    assert cli.main(['capture-bonfire-listings']) == 1


def test_explicit_empty_collections_are_success_and_still_archived(conn, tmp_path):
    bodies = source_data()
    for key in (OPEN, PAST):
        bodies[key] = b'{"success":1,"message":"Success","payload":{"projects":[],"departments":[]}}'
    count, calls, waits = run(conn, tmp_path, bodies=bodies)
    assert count == 0 and len(calls) == 3
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 0
    assert len(list((tmp_path / 'agencies' / 'bonfire' / PORTAL['slug']).glob('*.json'))) == 3


def test_transport_failure_uses_same_bounded_backoff(conn, tmp_path):
    calls, waits = [], []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout('response timed out', request=request)
        return httpx.Response(200, content=source_data()[request.url.path])
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert bf.capture_portal(conn, PORTAL, client=client, data_dir=tmp_path,
                                 sleep=waits.append, clock=lambda: NOW) == 412
    assert len(calls) == 4 and waits == [1, 2, 1, 1, 1]


def test_unlabelled_and_reused_references_remain_separate_pages(conn, tmp_path):
    bodies = source_data(); data = json.loads(bodies[OPEN])
    data['payload']['projects']['112329']['ReferenceID'] = None
    bodies[OPEN] = json.dumps(data).encode()
    assert run(conn, tmp_path, bodies=bodies)[0] == 412
    assert conn.execute("SELECT native_ref FROM portal_listing WHERE portal_url LIKE '%/112329'").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(DISTINCT procurement_id) FROM portal_listing WHERE native_ref='221-21-TPL-RFP'").fetchone()[0] == 4


def test_reopened_project_requires_unambiguous_original_anonymous_close(conn, tmp_path):
    bodies = source_data(); data = json.loads(bodies[OPEN])
    data['payload']['projects']['112329']['ProjectStatusID'] = '6'
    bodies[OPEN] = json.dumps(data).encode()
    with pytest.raises(ValueError, match='original close'):
        run(conn, tmp_path, bodies=bodies)
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 0
    data['payload']['pastScheduledProjectOpenPeriodsWhereNotIsReopen'] = [
        {'ProjectID': '112329', 'DateClose': '2026-10-15 16:00:00'}]
    bodies[OPEN] = json.dumps(data).encode()
    run(conn, tmp_path, bodies=bodies)
    row = conn.execute("SELECT status,closing_date FROM portal_listing WHERE portal_url LIKE '%/112329'").fetchone()
    assert tuple(row) == ('Closed', '2026-10-15T16:00:00+00:00')


def test_conflicting_open_past_observation_fails_with_no_partial_write(conn, tmp_path):
    bodies = source_data(); data = json.loads(bodies[PAST])
    opened = json.loads(bodies[OPEN])['payload']['projects']['112329']
    data['payload']['projects']['112329'] = dict(opened, ProjectName='Conflicting title')
    bodies[PAST] = json.dumps(data).encode()
    with pytest.raises(ValueError, match='changed between Open and Past'):
        run(conn, tmp_path, bodies=bodies)
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 0


@pytest.mark.parametrize('success', ['1', 1.0, 0, False, None])
def test_invalid_success_flags_do_not_become_empty_success(conn, tmp_path, success):
    bodies = source_data(); bodies[OPEN] = json.dumps({'success': success, 'payload': {'projects': []}}).encode()
    with pytest.raises(ValueError, match='success=true/1'):
        run(conn, tmp_path, bodies=bodies)


def test_server_deadline_persists_across_capture_runs_without_early_requests(conn, tmp_path):
    with pytest.raises(RuntimeError, match='retry deferred'):
        run(conn, tmp_path, responses={OPEN: [(429, {'Retry-After': '120'})]})
    def unexpected(request):
        pytest.fail('A second run must not contact the server during its cooldown')
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(RuntimeError, match='retry deferred until'):
            bf.capture_portal(conn, PORTAL, client=client, data_dir=tmp_path,
                              sleep=lambda _:None, clock=lambda:NOW)
    from datetime import timedelta
    with httpx.Client(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, content=source_data()[request.url.path]))) as client:
        assert bf.capture_portal(conn, PORTAL, client=client, data_dir=tmp_path,
                                 sleep=lambda _:None, clock=lambda:NOW+timedelta(seconds=121)) == 412


def test_shared_housing_portal_retains_all_public_postings_without_inventing_awards(conn, tmp_path):
    portal = next(p for p in config.BONFIRE_PORTALS if p['slug'] == 'toronto-community-housing')
    bodies = {LANDING: (FIXTURES / 'housing-portal.html').read_bytes(),
              OPEN: (FIXTURES / 'housing-open.json').read_bytes(),
              PAST: (FIXTURES / 'housing-past.json').read_bytes()}
    calls, waits = [], []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=bodies[request.url.path])
    observed = datetime(2026, 10, 23, 16, 30, tzinfo=timezone.utc)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        count = bf.capture_portal(conn, portal, client=client, data_dir=tmp_path,
                                  sleep=waits.append, clock=lambda: observed)
    assert count == 961 and len(calls) == 3 and waits == [1, 1, 1]
    assert all(httpx.URL(u).host == 'torontohousing.bonfirehub.ca' for u in calls)
    rows = list(conn.execute('SELECT * FROM portal_listing'))
    assert Counter(r['status'] for r in rows) == {'Open': 16, 'Awarded': 629, 'Closed': 226, 'Cancelled': 90}
    assert len({r['procurement_id'] for r in rows}) == 961
    repeated = [r for r in rows if r['native_ref'] == 'RFP 25270-PP']
    assert len(repeated) == len({r['procurement_id'] for r in repeated}) == 6
    assert min(r['closing_date'] for r in rows) == '2019-05-21T18:00:00+00:00'
    assert all(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
               for table in ('agency_award', 'agency_bid', 'agency_solicitation', 'solicitation', 'award'))
    export = build_export_document(conn, generated_at='fixed')
    housing = next(b for b in export['buyers'] if b['slug'] == portal['slug'])
    assert housing['name'] == 'Toronto Community Housing and Toronto Seniors Housing'
    assert housing['kind'] == 'corporation' and housing['partnered'] == 0
    assert len(housing['portal_listings']) == 961 and housing['awards'] == housing['bids'] == []
    assert any(a['slug'] == portal['slug'] and 'Toronto Seniors Housing' in a['statement']
               for a in export['meta']['attribution'])
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_enabled_bonfire_buyers_have_durable_permission_records():
    root = Path(__file__).resolve().parents[2]
    for portal in config.BONFIRE_PORTALS:
        if portal['enabled']:
            assert portal['permission'] and (root / portal['permission']).is_file()


@pytest.mark.parametrize('slug,prefix,expected,statuses', [
    ('waterfront-toronto', 'waterfront', 78, {'Open': 1, 'Closed': 77}),
    ('exhibition-place', 'exhibition', 94, {'Open': 1, 'Awarded': 77, 'Cancelled': 12, 'Closed': 4}),
    ('the-atmospheric-fund', 'taf', 16, {'Awarded': 8, 'Closed': 6, 'Cancelled': 2}),
])
def test_remaining_bonfire_buyers_use_real_public_responses(conn, tmp_path, slug, prefix, expected, statuses):
    portal = next(p for p in config.BONFIRE_PORTALS if p['slug'] == slug)
    bodies = {LANDING: (FIXTURES/f'{prefix}-portal.html').read_bytes(),
              OPEN: (FIXTURES/f'{prefix}-open.json').read_bytes(),
              PAST: (FIXTURES/f'{prefix}-past.json').read_bytes()}
    calls, waits = [], []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=bodies[request.url.path])
    observed = datetime(2026, 10, 24, 4, tzinfo=timezone.utc)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        count = bf.capture_portal(conn, portal, client=client, data_dir=tmp_path,
                                  sleep=waits.append, clock=lambda: observed)
    assert count == expected and len(calls) == 3 and waits == [1, 1, 1]
    assert all(str(httpx.URL(u).copy_with(path='/', query=None)).rstrip('/') == portal['portal_url'] for u in calls)
    rows = list(conn.execute('SELECT * FROM portal_listing'))
    assert Counter(r['status'] for r in rows) == statuses
    assert len({r['procurement_id'] for r in rows}) == expected
    if slug == 'exhibition-place':
        assert len({r['procurement_id'] for r in rows if r['native_ref'] == 'EPRFP2025-7745'}) == 2
    # Public Awarded badges carry no winning-supplier/value evidence.
    assert all(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
               for table in ('agency_award', 'agency_bid', 'agency_solicitation', 'solicitation', 'award'))
    export = build_export_document(conn, generated_at='fixed')
    buyer = next(b for b in export['buyers'] if b['slug'] == slug)
    assert len(buyer['portal_listings']) == expected
    assert any(a['slug'] == slug and a['source_url'] == portal['portal_url'] for a in export['meta']['attribution'])
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
