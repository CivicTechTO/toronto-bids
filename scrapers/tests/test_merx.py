"""Public MERX contract, using retained TTC HTML and declared source counts."""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from lxml import html

from toronto_bids import config, cli
from toronto_bids.buyers import seed_buyers
from toronto_bids.export.document import build_export_document
from toronto_bids.sources import merx as mx

F = Path(__file__).parent / 'fixtures' / 'merx'
PORTAL = config.MERX_PORTALS[0]
NOW = datetime(2026, 10, 24, 20, tzinfo=timezone.utc)


def body(name):
    return (F / name).read_bytes()


def test_real_listing_tabs_have_distinct_date_semantics():
    for section, name, total, n in [('open', 'ttc', 10, 10), ('closed', 'closed', 2938, 25),
                                    ('results', 'bidresults', 1967, 25), ('awarded', 'awarded', 1921, 25)]:
        page = mx.parse_page(body(name + '.html'), PORTAL, section, 1)
        assert (page['total'], len(page['listings'])) == (total, n)
        assert all(r.posted_date for r in page['listings'].values())
        assert all((r.closing_date is None) == (section in ('results', 'awarded')) for r in page['listings'].values())
        assert all(r.status != 'Awarded' for r in page['listings'].values())


def test_complete_real_positive_query_includes_legacy_and_missing_middle():
    seen = set()
    for n in range(1, 10):
        page = mx.parse_page(body(f't11-{n}.html'), PORTAL, 'closed', 1)
        assert page['total'] == 210 and page['page'] == n
        assert not seen.intersection(page['listings'])
        seen.update(page['listings'])
    assert len(seen) == 210 and '879704954' in seen
    assert any(r.posted_date.startswith('2019') for r in mx.parse_page(body('t11-7.html'), PORTAL, 'closed', 1)['listings'].values())


def test_real_bids_no_bid_is_not_zero_and_results_are_unofficial():
    rows = mx.parse_bids(body('bid-fragment.js'), 1, 'held-public-page', 'fragment-url')
    assert len(rows) == 8
    assert sum(r.status == 'No Bid' and r.bid_price is None for r in rows) == 5
    assert sum(r.bid_price_numeric is not None for r in rows) == 3
    assert {r.result_stage for r in rows} == {'Unofficial Results'}
    assert {r.hst_basis for r in rows} == {None}
    assert next(r.bid_price for r in rows if r.bidder_name_raw == 'The Gear Centre Group of Companies') == '$108,480.00'


def test_award_date_and_publication_date_are_independent():
    row, = mx.parse_awards(body('award-fragment.js'), 1, 'held-public-page', 'fragment-url', '0000331101')
    assert row.supplier_name_raw == 'Reefer Sales and Service Toronto'
    assert row.award_amount == '$226,605.49'
    assert row.award_date == '2026-10-05' and row.publication_date == '2026-10-08'
    with pytest.raises(ValueError, match='reference'):
        mx.parse_awards(body('award-fragment.js'), 1, 'page', 'fragment', '0000333265')


def run(conn, tmp_path, handler, **kw):
    waits, calls = [], []
    def handle(request):
        assert request.headers['User-Agent'] == mx.USER_AGENT
        calls.append(request)
        return handler(request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = mx.capture_portal(conn, PORTAL, client=client, data_dir=tmp_path,
                                   sleep=waits.append, clock=lambda: NOW, **kw)
    return result, calls, waits


def test_open_capture_is_idempotent_archived_and_exported(conn, tmp_path):
    handler = lambda req: httpx.Response(200, content=body('ttc.html'))
    report, calls, waits = run(conn, tmp_path, handler, sections=['open'])
    assert report['complete'] and report['listings'] == 10 and waits == [5.2]
    before = [tuple(r) for r in conn.execute('SELECT id,first_seen,procurement_id FROM portal_listing')]
    run(conn, tmp_path, handler, sections=['open'])
    assert [tuple(r) for r in conn.execute('SELECT id,first_seen,procurement_id FROM portal_listing')] == before
    doc = build_export_document(conn, generated_at='fixed')
    buyer = next(b for b in doc['buyers'] if b['slug'] == PORTAL['slug'])
    assert len(buyer['portal_listings']) == 10 and len(buyer['merx_captures']) == 2
    assert len(list((tmp_path / 'agencies' / 'merx' / PORTAL['slug']).glob('*.bin'))) == 1
    assert not conn.execute('SELECT * FROM agency_award').fetchall()


def test_incremental_window_is_explicitly_not_complete_history(conn, tmp_path):
    report, calls, _ = run(conn, tmp_path, lambda req: httpx.Response(200, content=body('closed.html')), sections=['closed'])
    assert len(calls) == 1 and report['listings'] == 25
    assert not report['complete'] and not report['failures']
    assert report['sections']['closed']['scope'] == 'recent window'


def test_backfill_detects_clamping_retains_evidence_and_does_not_delete(conn, tmp_path):
    run(conn, tmp_path, lambda req: httpx.Response(200, content=body('ttc.html')), sections=['open'])
    report, calls, _ = run(conn, tmp_path, lambda req: httpx.Response(200, content=body('closed.html')), sections=['closed'], backfill=True)
    assert any('clamped' in f for f in report['failures'])
    assert not report['complete'] and len(calls) == 2
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 10


def test_budget_failure_has_saved_report_and_nonzero_cli(conn, tmp_path, monkeypatch):
    report, calls, _ = run(conn, tmp_path, lambda req: httpx.Response(200, content=body('closed.html')),
                           sections=['closed'], backfill=True, max_requests=1)
    assert len(calls) == 1 and 'budget' in report['failures'][0] and not report['complete']
    monkeypatch.setattr(cli, '_open_db', lambda: conn)
    monkeypatch.setattr(mx, 'capture_portal', lambda *a, **kw: report)
    assert cli.main(['capture-merx']) == 1


@pytest.mark.parametrize('status', [302, 401, 403, 429])
def test_access_failure_never_turns_into_empty_success(conn, tmp_path, status):
    report, calls, waits = run(conn, tmp_path, lambda req: httpx.Response(status, headers={'Retry-After': '120'}), sections=['open'])
    assert len(calls) == 1 and report['failures'] and not report['complete']
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 0
    assert list((tmp_path / 'agencies' / 'merx' / PORTAL['slug']).glob('*.bin'))


def test_retry_after_persists_across_runs(conn, tmp_path):
    handler = lambda req: httpx.Response(503, headers={'Retry-After': '120'})
    run(conn, tmp_path, handler, sections=['open'])
    report, calls, waits = run(conn, tmp_path, handler, sections=['open'])
    assert not calls and not waits and 'deadline' in report['failures'][0]


def test_bounded_server_retry_and_transport_backoff(conn, tmp_path):
    n = 0
    def handler(req):
        nonlocal n
        n += 1
        return httpx.Response(503 if n < 3 else 200, content=body('ttc.html'), headers={'Retry-After': '8'} if n < 3 else {})
    report, calls, waits = run(conn, tmp_path, handler, sections=['open'])
    assert report['complete'] and len(calls) == 3 and waits == [5.2, 8, 5.2, 8, 5.2]


@pytest.mark.parametrize('mutation', ['buyer', 'date', 'count', 'duplicate'])
def test_bad_listings_refuse_atomically(conn, tmp_path, mutation):
    t = html.fromstring(body('ttc.html'))
    if mutation == 'buyer':
        t.xpath('//*[contains(@class,"sol-buyer-name")]')[0].text = 'Other Buyer'
    elif mutation == 'date':
        t.xpath('//*[contains(@class,"date-value")]')[0].text = '2026/02/30'
    elif mutation == 'count':
        t.xpath('//a[contains(@class,"solicitation-link")]')[0].drop_tree()
    else:
        links = t.xpath('//a[contains(@class,"solicitation-link")]');links[1].set('href',links[0].get('href'))
    report, _, _ = run(conn, tmp_path, lambda req: httpx.Response(200, content=html.tostring(t)), sections=['open'])
    assert report['failures'] and not conn.execute('SELECT * FROM portal_listing').fetchall()


def test_legacy_gate_is_explicit_and_never_captured_as_notice():
    with pytest.raises(PermissionError):
        mx._tree('<html>An action is required before access to this content is granted</html>')


def test_real_detail_tabs_bind_to_notice_page_identity(conn, tmp_path):
    ids = seed_buyers(conn);buyer = ids[PORTAL['slug']]
    page = mx.parse_page(body('bidresults.html'), PORTAL, 'results', buyer)
    nid = '0000333265';listing = page['listings'][nid]
    from toronto_bids.store import db
    db.upsert_row(conn, listing, overwrite=True);conn.commit()
    def handler(req):
        return httpx.Response(200, content=body('bid-fragment.js') if req.url.path.endswith('/bid-results') else body('result-detail.html'))
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        session = mx.PublicSession(tmp_path,c,sleep=lambda n:None)
        assert mx.capture_details(conn,session,listing,page['links'][nid],nid) == 8
    mx.resolve_procurements(conn);conn.commit()
    ids = {r[0] for r in conn.execute('SELECT procurement_id FROM agency_bid UNION SELECT procurement_id FROM portal_listing')}
    assert len(ids) == 1 and None not in ids
    assert conn.execute('SELECT native_ref FROM portal_listing').fetchone()[0] == 'T06BI26617'
    assert not conn.execute('SELECT * FROM agency_award').fetchall()


def test_resume_completes_real_nine_page_history_and_rechecks_live_population(conn,tmp_path):
    def handler(req):
        q=parse_qs(req.url.query.decode());n=int(q.get('pageNumber',['1'])[0])
        return httpx.Response(200,content=body(f't11-{n}.html'))
    first,calls,_=run(conn,tmp_path,handler,sections=['closed'],backfill=True,max_requests=2)
    assert first['failures'] and len(calls)==2
    final,calls,_=run(conn,tmp_path,handler,sections=['closed'],backfill=True,resume=True,max_requests=8)
    assert final['complete'] and final['sections']['closed']['captured']==210
    assert len(calls)==8  # seven new pages plus live first-page revalidation
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0]==210
    assert conn.execute("SELECT COUNT(*) FROM merx_notice WHERE notice_id='879704954'").fetchone()[0]==1


def test_population_change_never_claims_complete(conn,tmp_path):
    count=0
    def handler(req):
        nonlocal count
        count+=1
        data=body('ttc.html')
        if count==2:
            data=data.replace(b'10 Open Solicitations',b'9 Open Solicitations')
        return httpx.Response(200,content=data)
    report,_,_=run(conn,tmp_path,handler,sections=['open'],backfill=True)
    assert not report['complete'] and report['failures']
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0]==10


def test_changed_title_and_section_route_keep_one_source_identity(conn,tmp_path):
    run(conn,tmp_path,lambda r:httpx.Response(200,content=body('ttc.html')),sections=['open'])
    before=[tuple(r) for r in conn.execute('SELECT id,procurement_id,portal_url FROM portal_listing ORDER BY id')]
    t=html.fromstring(body('ttc.html'))
    for a in t.xpath('//a[contains(@class,"solicitation-link")]'):
        a.set('href',a.get('href').replace('/open-bids/','/'))
    run(conn,tmp_path,lambda r:httpx.Response(200,content=html.tostring(t)),sections=['open'])
    assert [tuple(r) for r in conn.execute('SELECT id,procurement_id,portal_url FROM portal_listing ORDER BY id')]==before


def test_capped_parent_is_completed_by_overlapping_positive_queries(conn,tmp_path):
    # A 2,050-notice parent: reversed 1,000-row windows leave 50 in the middle.
    # A smaller positive query includes those 50 PLUS 25 overlapping rows.
    template=html.fromstring(body('closed.html'))
    anchors=template.xpath('//a[contains(@class,"solicitation-link")]')
    total_nodes=template.xpath('//*[not(*) and contains(text(),"2,938 Closed Solicitations")]')
    assert len(total_nodes)==1
    from copy import deepcopy
    def make(ids,page,total):
        t=deepcopy(template)
        nodes=t.xpath('//*[not(*) and contains(text(),"2,938 Closed Solicitations")]');nodes[0].text=f'{total:,} Closed Solicitations'
        options=t.xpath('//option[@data-page-number]')
        for o in options:
            o.attrib.pop('selected',None)
            if o.get('data-page-number')==str(page):o.set('selected','selected')
        for i,a in enumerate(t.xpath('//a[contains(@class,"solicitation-link")]')):
            if i>=len(ids):a.xpath('ancestor::tr[1]')[0].drop_tree();continue
            a.set('href',f'/ttc/ttcpurchasingandsales/solicitations/T11-PUBLIC-NOTICE/{ids[i]:010d}')
            a.text='T11PT00001 Public Notice'
        return html.tostring(t)
    def handler(req):
        q=parse_qs(req.url.query.decode());page=int(q['pageNumber'][0])
        assert req.url.path=='/ttc/solicitations/closed-bids'
        if q.get('keywords'):
            assert q['keywords']==['T11']
            dataset=list(range(975,1050));total=75
        else:
            dataset=list(range(2050));total=2050
            if q['sortDirection']==['DESC']:dataset.reverse()
        ids=dataset[(page-1)*25:page*25]
        return httpx.Response(200,content=make(ids,page,total))
    report,calls,_=run(conn,tmp_path,handler,sections=['closed'],backfill=True,max_requests=90)
    assert report['failures']==[]
    assert report['complete'] and report['sections']['closed']['captured']==2050, report
    assert len(calls)==84  # 40 newest +40 oldest +3 overlapping-query +1 recheck
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0]==2050
    assert any('keywords=T11' in str(r.url) for r in calls)


@pytest.mark.parametrize('profile,fixture,count,reference',[
    ('toronto-police-service','torontopolice',4,'10001619 – 26'),
    ('toronto-parking-authority','torontoparkingauthority',2,'2025-CON-CP11-D2020'),
    ('createto','buildtorontotplc',1,'RFP#2026-047'),
    ('toronto-pan-am-sports-centre','torontopanamsportscentre',0,None),
])
def test_other_verified_buyer_profiles(conn,tmp_path,profile,fixture,count,reference):
    portal=next(p for p in config.MERX_PORTALS if p['slug']==profile)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=body(fixture+'.html')))) as c:
        report=mx.capture_portal(conn,portal,sections=['open'],client=c,data_dir=tmp_path,sleep=lambda n:None)
    assert report['complete'] and report['listings']==count
    if reference:
        assert conn.execute('SELECT native_ref FROM portal_listing ORDER BY id').fetchone()[0]==reference
    assert not conn.execute('SELECT * FROM agency_award').fetchall()


def test_listing_denial_stops_details_too(conn,tmp_path):
    def handler(req):
        return httpx.Response(403) if 'closed-bids' in req.url.path else httpx.Response(200,content=body('ttc.html'))
    report,calls,_=run(conn,tmp_path,handler,sections=['open','closed'],details=True)
    assert len(calls)==2 and report['failures']
    assert report['listings']==10 and report['detail_facts']==0


def test_additive_stage_and_publication_migration_preserves_held_facts(conn):
    from toronto_bids.store import db
    seed_buyers(conn)
    buyer=conn.execute("SELECT id FROM buyer WHERE slug='toronto-transit-commission'").fetchone()[0]
    conn.execute("INSERT INTO agency_bid(buyer_id,native_ref,bidder_name_raw,source) VALUES(?, 'held', 'Bidder','test')",(buyer,))
    conn.execute('ALTER TABLE agency_bid DROP COLUMN result_stage')
    conn.execute('ALTER TABLE agency_award DROP COLUMN publication_date')
    conn.commit();db.init_db(conn);db.init_db(conn)
    assert conn.execute('SELECT bidder_name_raw,result_stage FROM agency_bid').fetchone()[:] == ('Bidder',None)
    assert 'publication_date' in {r[1] for r in conn.execute('PRAGMA table_info(agency_award)')}


def test_nightly_runner_captures_details_and_isolates_buyers(conn,monkeypatch):
    calls=[]
    def capture(conn,portal,**kwargs):
        calls.append((portal['slug'],kwargs))
        if portal['slug']==config.MERX_PORTALS[0]['slug']:
            raise RuntimeError('source unavailable')
        return dict(listings=1,failures=[],detail_gaps=[])
    monkeypatch.setattr(mx,'capture_portal',capture)
    results=mx.run_merx_capture(conn)
    assert len(results)==len([p for p in config.MERX_PORTALS if p['enabled']])
    assert results[config.MERX_PORTALS[0]['slug']]['failures']==['source unavailable']
    assert all(opts['details'] is True and not opts.get('backfill') for slug,opts in calls)
    assert results[config.MERX_PORTALS[1]['slug']]['listings']==1


GOOD_NOTICE = '0000333265'


def detail_handler(fail_after_good=None):
    """Results listing -> one real detail (+ bid fragment); any other notice detail -> `fail_after_good`."""
    def handler(req):
        path = req.url.path
        if path.endswith('/abstract/bid-results'):
            return httpx.Response(200, content=body('bid-fragment.js'))
        if path.endswith('/' + GOOD_NOTICE):
            return httpx.Response(200, content=body('result-detail.html'))
        if path.rsplit('/', 1)[-1].isdigit():
            return fail_after_good or httpx.Response(200, content=body('result-detail.html'))
        return httpx.Response(200, content=body('bidresults.html'))
    return handler


def resolve_spy(monkeypatch):
    calls = []
    real = mx.resolve_procurements
    def spy(conn):
        calls.append(1)
        return real(conn)
    monkeypatch.setattr(mx, 'resolve_procurements', spy)
    return calls


def merx_bid_ids(conn):
    return [r[0] for r in conn.execute("SELECT procurement_id FROM agency_bid WHERE source='merx'")]


def test_capture_resolves_identity_once_per_buyer_run(conn, tmp_path, monkeypatch):
    passes = resolve_spy(monkeypatch)
    report, calls, waits = run(conn, tmp_path, detail_handler(), sections=['results'], details=True)
    assert report['detail_facts'] == 8 and len(calls) > 3
    assert passes == [1]
    listing_id = conn.execute('SELECT procurement_id FROM portal_listing WHERE portal_url LIKE ?',
                              ('%' + GOOD_NOTICE,)).fetchone()[0]
    assert listing_id is not None and set(merx_bid_ids(conn)) == {listing_id}
    assert waits == [5.2] * len(calls)


def test_mid_run_failure_keeps_completed_notices_resolved_and_reported(conn, tmp_path, monkeypatch):
    passes = resolve_spy(monkeypatch)
    report, _, _ = run(conn, tmp_path, detail_handler(httpx.Response(403)), sections=['results'], details=True)
    assert report['detail_facts'] == 8
    assert any(f.startswith('detail:') for f in report['failures']) and not report['complete']
    ids = merx_bid_ids(conn)
    assert len(ids) == 8 and None not in ids
    assert passes == [1]
    held = conn.execute('SELECT complete, report_json FROM merx_capture').fetchone()
    assert held[0] == 0 and 'detail:' in held[1]


def test_failed_final_resolution_is_a_visible_failure_not_lost_facts(conn, tmp_path, monkeypatch):
    def broken(conn):
        raise RuntimeError('identity conflict')
    monkeypatch.setattr(mx, 'resolve_procurements', broken)
    report, _, _ = run(conn, tmp_path, detail_handler(), sections=['results'], details=True)
    assert any('procurement resolution' in f and 'identity conflict' in f for f in report['failures'])
    assert not report['complete']
    assert len(merx_bid_ids(conn)) == 8
    assert conn.execute('SELECT complete FROM merx_capture').fetchone()[0] == 0


def test_interrupted_final_resolution_still_writes_the_coverage_report(conn, tmp_path, monkeypatch):
    def interrupted(conn):
        raise KeyboardInterrupt
    monkeypatch.setattr(mx, 'resolve_procurements', interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(conn, tmp_path, detail_handler(), sections=['results'], details=True)
    held = conn.execute('SELECT complete, report_json FROM merx_capture').fetchone()
    assert held[0] == 0 and 'procurement resolution interrupted' in held[1]
    assert len(merx_bid_ids(conn)) == 8


def test_real_marked_empty_publication_has_no_fabricated_bidders():
    assert mx.parse_bids(body('bids-empty-publication.js'), 1, 'page', 'fragment') == []


@pytest.mark.parametrize('change', ['remove_table', 'hide_marker', 'add_file'])
def test_unmarked_or_nonempty_document_only_results_remain_refused(change):
    import ast
    import re
    import warnings

    raw = (F / 'bids-empty-publication.js').read_text()
    literal = re.search(r'\.html\((\'(?:\\.|[^\'\\])*\')\);', raw)[1]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        text = ast.literal_eval(literal).replace('\\/', '/')
    tree = html.fromstring(text)
    table = tree.xpath('//table[@id="tblBidResultDocument"]')[0]
    if change == 'remove_table':
        table.drop_tree()
    elif change == 'hide_marker':
        table.xpath('./tbody/tr')[0].set('class', 'mets-table-row-empty')
    else:
        table.xpath('./tbody')[0].append(
            html.fromstring('<tr class="mets-table-row"><td><a href="/actual-file">Results PDF</a></td></tr>')
        )
    modified = '$("#innerTabContent").html(' + repr(html.tostring(tree, encoding='unicode')) + ');'
    with pytest.raises(ValueError):
        mx.parse_bids(modified.encode(), 1, 'page', 'fragment')


def test_empty_publication_preserves_note_and_still_captures_advertised_award(conn):
    from toronto_bids.store import db

    manifest = json.loads((F / 'empty-results-manifest.json').read_text())
    buyer = seed_buyers(conn)[PORTAL['slug']]
    listing = mx.PortalListing(
        buyer_id=buyer, portal_url=manifest['detail_url'], title='Green Bus Technology', source='merx'
    )
    db.upsert_row(conn, listing, overwrite=True)
    conn.commit()
    detail = body('empty-result-detail.html').replace(
        b'</body>', b'<a data-ajax-url="/public/solicitations/526480708/abstract/award">Award</a></body>'
    )
    award = body('award-fragment.js').replace(b'0000331101', b'0000065665')
    calls, notes = [], []

    def fetch(url):
        calls.append(url)
        if url.endswith('/bid-results'):
            return body('bids-empty-publication.js')
        if url.endswith('/award'):
            return award
        return detail

    assert mx.capture_details(
        conn, None, listing, manifest['detail_url'], manifest['notice_id'], fetch=fetch, observations=notes
    ) == 1
    assert calls[-1].endswith('/award')
    assert not conn.execute('SELECT * FROM agency_bid').fetchall()
    assert conn.execute('SELECT count(*) FROM agency_award').fetchone()[0] == 1
    assert notes == [
        dict(
            notice_id=manifest['notice_id'],
            kind='no_bidder_disclosure',
            source_url=manifest['fragment_url'],
            reason='Public result tab contains no bidder entries and explicitly reports no result files.',
        )
    ]


@pytest.mark.parametrize('name,nid', [
    ('duplicate-location.html', '0000334741'),
    ('duplicate-events.html', '0000329486'),
    ('duplicate-pricing.html', '0000334413'),
])
def test_incidental_repeated_fields_do_not_refuse_notice_identity(name, nid):
    fields = mx._fields(mx._tree(body(name)), {'Reference Number', 'Solicitation Number'})
    assert fields['Reference Number'] == nid and fields['Solicitation Number']


@pytest.mark.parametrize('name,count,stage', [
    ('bids-na.js', 3, 'Unofficial Results'),
    ('bids-names-only.js', 4, 'Unofficial Results'),
    ('bids-generic-stage.js', 1, 'Bid Results'),
])
def test_named_bidders_survive_unpublished_prices(name, count, stage):
    rows = mx.parse_bids(body(name), 1, 'held-notice', 'public-result')
    assert len(rows) == count
    assert all(r.bid_price is None and r.status is None and r.result_stage == stage for r in rows)
    assert all(r.bidder_name_raw for r in rows)
    if name == 'bids-names-only.js':
        assert all(r.price_header is None for r in rows)


@pytest.mark.parametrize('name,nid', [
    ('no-award-ttc.js', '0000329037'),
    ('no-award-parking.js', '0000309923'),
])
def test_explicit_no_award_tabs_do_not_create_awards_or_identity_errors(name, nid):
    assert mx.parse_awards(body(name), 1, 'held-notice', 'public-award', nid) == []


def test_empty_202_is_not_successful_html_or_immediate_retry(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(202, content=b'', headers={'x-amzn-waf-action': 'challenge'})

    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        class UnavailableBrowser:
            def get(self, *a, **k):
                raise RuntimeError('HTTP 202: incomplete browser response')

            def close(self):
                pass

        session = mx.PublicSession(tmp_path / 'merx', c, sleep=lambda n: None, browser_factory=UnavailableBrowser)
        with pytest.raises(RuntimeError, match='202'):
            session.get('https://www.merx.com/ttc')
    assert len(calls) == 1


def test_202_fallback_is_bounded_paced_recorded_and_reuses_anonymous_state(tmp_path):
    waits, calls, browser_calls = [], [], []

    class Reader:
        def get(self, url, **kw):
            browser_calls.append(url)
            kw['before_request']()
            result = httpx.Response(200, content=body('ttc.html'), request=httpx.Request('GET', url))
            kw['record'](result)
            return result

        def cookies(self):
            return [dict(name='anonymous-state', value='test-value', domain='www.merx.com', path='/')]

        def close(self):
            pass

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                202, content=b'', headers={'x-amzn-waf-action': 'challenge', 'set-cookie': 'not-in-diagnostics'}
            )
        assert 'anonymous-state=test-value' in request.headers['cookie']
        return httpx.Response(200, content=b'held document')

    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        session = mx.PublicSession(tmp_path / 'merx', c, sleep=waits.append, browser_factory=Reader)
        assert session.get('https://www.merx.com/ttc').content
        assert session.get('https://www.merx.com/ttc').content == b'held document'
        assert session.requests == 3
    assert len(calls) == 2 and len(browser_calls) == 1 and waits == [5.2, 60, 5.2, 5.2]
    diagnostics = list((tmp_path / 'merx/diagnostics').glob('*.json'))
    assert diagnostics and all('set-cookie' not in p.read_text() and 'test-value' not in p.read_text() for p in diagnostics)


def test_long_202_deadline_defers_browser_and_next_buyer(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(202, headers={'Retry-After': '120'})

    def never():
        raise AssertionError('browser must not start before server deadline')

    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        one = mx.PublicSession(tmp_path / 'one', c, sleep=lambda n: None, browser_factory=never)
        two = mx.PublicSession(tmp_path / 'two', c, sleep=lambda n: None, browser_factory=never)
        with pytest.raises(RuntimeError, match='202'):
            one.get('https://www.merx.com/ttc')
        with pytest.raises(RuntimeError, match='deadline'):
            two.get('https://www.merx.com/torontopolice')
    assert len(calls) == 1


def test_required_identity_duplicates_are_still_refused():
    from copy import deepcopy

    tree = mx._tree(body('duplicate-location.html'))
    fields = tree.xpath(
        '//*[' + mx._class('mets-field') + '][./*[' + mx._class('mets-field-label') + ' and normalize-space(.)="Reference Number"]]'
    )
    fields[0].getparent().append(deepcopy(fields[0]))
    with pytest.raises(ValueError, match='Reference Number'):
        mx._fields(tree, {'Reference Number'})


def test_202_cannot_launch_browser_after_request_budget_is_spent(tmp_path):
    waits = []

    def never():
        raise AssertionError('request budget is exhausted')

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(202))) as c:
        session = mx.PublicSession(tmp_path / 'merx', c, sleep=waits.append, browser_factory=never, max_requests=1)
        with pytest.raises(RuntimeError, match='budget exhausted'):
            session.get('https://www.merx.com/ttc')
    assert waits == [5.2]


def test_browser_readiness_uses_navigation_status_not_buyer_title():
    from types import SimpleNamespace
    from toronto_bids.sources.merx_browser import BrowserReader

    class Response:
        url = 'https://www.merx.com/ttc/solicitations/title/0000329490'

        def __init__(self, status):
            self.status = status
            self.request = SimpleNamespace(is_navigation_request=lambda: True)
            self.frame = 'main'

        def body(self):
            return b'public notice' if self.status == 200 else b'ordinary JavaScript check'

        def all_headers(self):
            return {'x-amzn-waf-action': 'challenge'} if self.status == 202 else {}

    class Page:
        main_frame = 'main'

        def on(self, event, callback):
            self.callback = callback

        def remove_listener(self, *a):
            pass

        def goto(self, *a, **kw):
            response = Response(202)
            self.callback(response)
            return response

        def wait_for_function(self, *a, **kw):
            raise AssertionError('Solicitation titles do not start MERX :')

        def wait_for_event(self, event, **kw):
            response = Response(200)
            assert kw['predicate'](response)
            self.callback(response)
            return response

    reader = BrowserReader.__new__(BrowserReader)
    reader.page = Page()
    recorded = []
    result = reader.get(Response.url, prepare=None, before_request=lambda: None, record=recorded.append)
    assert result.content == b'public notice' and [r.status_code for r in recorded] == [202, 200]


def test_browser_fragment_uses_apiresponse_headers_property():
    from types import SimpleNamespace
    from toronto_bids.sources.merx_browser import BrowserReader

    class Page:
        main_frame = 'main'

        def on(self, *a):
            pass

        def remove_listener(self, *a):
            pass

        def goto(self, url, **kw):
            return SimpleNamespace(status=200, body=lambda: b'detail', url=url, all_headers=lambda: {})

    reader = BrowserReader.__new__(BrowserReader)
    reader.page = Page()
    reader.context = SimpleNamespace(
        request=SimpleNamespace(
            get=lambda url, **kw: SimpleNamespace(
                status=200, body=lambda: b'public fragment', url=url, headers={'content-type': 'text/javascript'}
            )
        )
    )
    held = []
    result = reader.get(
        'https://www.merx.com/public/solicitations/123/abstract/award',
        prepare='https://www.merx.com/ttc/solicitations/notice/0000329490',
        before_request=lambda: None,
        record=held.append,
    )
    assert result.content == b'public fragment' and result.headers['content-type'] == 'text/javascript'
    assert len(held) == 2
