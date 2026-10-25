"""Real production layouts refused by the first full MERX capture."""
from pathlib import Path
import httpx
import pytest
from toronto_bids.sources import merx as m

F=Path(__file__).parent/'fixtures/merx'


@pytest.mark.parametrize('name,nid',[('duplicate-location.html','0000334741'),('duplicate-events.html','0000329486'),('duplicate-pricing.html','0000334413')])
def test_incidental_repeated_fields_do_not_refuse_notice_identity(name,nid):
    fields=m._fields(m._tree((F/name).read_bytes()),{'Reference Number','Solicitation Number'})
    assert fields['Reference Number']==nid and fields['Solicitation Number']


@pytest.mark.parametrize('name,count,stage',[('bids-na.js',3,'Unofficial Results'),('bids-names-only.js',4,'Unofficial Results'),('bids-generic-stage.js',1,'Bid Results')])
def test_named_bidders_survive_unpublished_prices(name,count,stage):
    rows=m.parse_bids((F/name).read_bytes(),1,'held-notice','public-result')
    assert len(rows)==count
    assert all(r.bid_price is None and r.status is None and r.result_stage==stage for r in rows)
    assert all(r.bidder_name_raw for r in rows)
    if name=='bids-names-only.js':assert all(r.price_header is None for r in rows)


@pytest.mark.parametrize('name,nid',[('no-award-ttc.js','0000329037'),('no-award-parking.js','0000309923')])
def test_explicit_no_award_tabs_do_not_create_awards_or_identity_errors(name,nid):
    assert m.parse_awards((F/name).read_bytes(),1,'held-notice','public-award',nid)==[]


def test_empty_202_is_not_successful_html_or_immediate_retry(tmp_path):
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(202,content=b'',headers={'x-amzn-waf-action':'challenge'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        class UnavailableBrowser:
            def get(self,*a,**k):
                raise RuntimeError('HTTP 202: incomplete browser response')
            def close(self):pass
        session=m.PublicSession(tmp_path/'merx',c,sleep=lambda n:None,browser_factory=UnavailableBrowser)
        with pytest.raises(RuntimeError,match='202'):
            session.get('https://www.merx.com/ttc')
    assert len(calls)==1


def test_202_fallback_is_bounded_paced_recorded_and_reuses_anonymous_state(tmp_path):
    waits=[];calls=[];browser_calls=[]
    class Reader:
        def get(self,url,**kw):
            browser_calls.append(url);kw['before_request']()
            result=httpx.Response(200,content=(F/'ttc.html').read_bytes(),request=httpx.Request('GET',url))
            kw['record'](result)
            return result
        def cookies(self):return [dict(name='anonymous-state',value='test-value',domain='www.merx.com',path='/')]
        def close(self):pass
    def handle(request):
        calls.append(request)
        if len(calls)==1:return httpx.Response(202,content=b'',headers={'x-amzn-waf-action':'challenge','set-cookie':'not-in-diagnostics'})
        assert 'anonymous-state=test-value' in request.headers['cookie']
        return httpx.Response(200,content=b'held document')
    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        session=m.PublicSession(tmp_path/'merx',c,sleep=waits.append,browser_factory=Reader)
        assert session.get('https://www.merx.com/ttc').content
        assert session.get('https://www.merx.com/ttc').content==b'held document'
        assert session.requests==3
    assert len(calls)==2 and len(browser_calls)==1 and waits==[5.2,60,5.2,5.2]
    diagnostics=list((tmp_path/'merx/diagnostics').glob('*.json'))
    assert diagnostics and all('set-cookie' not in p.read_text() and 'test-value' not in p.read_text() for p in diagnostics)


def test_long_202_deadline_defers_browser_and_next_buyer(tmp_path):
    calls=[]
    def handle(request):
        calls.append(request);return httpx.Response(202,headers={'Retry-After':'120'})
    def never():raise AssertionError('browser must not start before server deadline')
    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        one=m.PublicSession(tmp_path/'one',c,sleep=lambda n:None,browser_factory=never)
        two=m.PublicSession(tmp_path/'two',c,sleep=lambda n:None,browser_factory=never)
        with pytest.raises(RuntimeError,match='202'):one.get('https://www.merx.com/ttc')
        with pytest.raises(RuntimeError,match='deadline'):two.get('https://www.merx.com/torontopolice')
    assert len(calls)==1


def test_required_identity_duplicates_are_still_refused():
    from lxml import html
    tree=m._tree((F/'duplicate-location.html').read_bytes())
    fields=tree.xpath('//*['+m._class('mets-field')+'][./*['+m._class('mets-field-label')+' and normalize-space(.)="Reference Number"]]')
    from copy import deepcopy
    fields[0].getparent().append(deepcopy(fields[0]))
    with pytest.raises(ValueError,match='Reference Number'):
        m._fields(tree,{'Reference Number'})


def test_202_cannot_launch_browser_after_request_budget_is_spent(tmp_path):
    waits=[]
    def never():raise AssertionError('request budget is exhausted')
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(202))) as c:
        session=m.PublicSession(tmp_path/'merx',c,sleep=waits.append,browser_factory=never,max_requests=1)
        with pytest.raises(RuntimeError,match='budget exhausted'):
            session.get('https://www.merx.com/ttc')
    assert waits==[5.2]
