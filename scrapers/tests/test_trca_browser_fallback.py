"""The ordinary browser is a bounded fallback for observed eSCRIBE verification HTML."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from toronto_bids.sources import trca_board as trca

BASE='https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId='
PDF=(Path(__file__).parent/'fixtures/agencies/trca_20812.pdf').read_bytes()
CHALLENGE=b'<html>Verifying your browser - eScribe</html>'


def setup(conn,monkeypatch,responses):
    visits=[]
    sessions=[]
    closed=[]
    @contextmanager
    def browser():
        sessions.append(True)
        def fetch(url):
            visits.append(url)
            return responses[url]
        try:
            yield fetch
        finally:
            closed.append(True)
    monkeypatch.setattr(trca,'_browser_fetch',browser,raising=False)
    for url in responses:
        conn.execute("INSERT INTO background_pdf(url,kind) VALUES (?,'agency_board')",(url,))
    return visits,sessions,closed


@pytest.mark.parametrize('status',[200,403])
def test_verification_uses_one_browser_session_and_archives_exact_pdf(conn,monkeypatch,tmp_path,status):
    replies={BASE+str(n):{'status':200,'url':BASE+str(n),'body':PDF} for n in [21060,21063]}
    visits,sessions,closed=setup(conn,monkeypatch,replies)
    def direct(url):
        if status==200:
            return CHALLENGE
        req=httpx.Request('GET',url)
        raise httpx.HTTPStatusError('403',request=req,response=httpx.Response(403,content=CHALLENGE,request=req))
    failures=[]
    n=trca._store_pending_pdfs(conn,SimpleNamespace(get_bytes=direct),tmp_path,'%escribemeetings%',lambda m:None,'trca',failures=failures)
    assert n==2 and not failures
    assert visits==list(replies) and sessions==[True] and closed==[True]
    rows=conn.execute('SELECT * FROM background_pdf').fetchall()
    assert all(Path(r['local_path']).read_bytes()==PDF and r['text'] for r in rows)


@pytest.mark.parametrize('reply',[
    {'status':403,'body':CHALLENGE},
    {'status':200,'body':CHALLENGE},
    {'status':200,'body':b'%PDF-1.7\nnot a valid document'},
    {'status':200,'body':PDF,'url':'https://other.example/file.pdf'},
])
def test_browser_failures_stay_queued_and_reported(conn,monkeypatch,tmp_path,reply):
    url=BASE+'21060'
    visits,_,closed=setup(conn,monkeypatch,{url:{'url':url,**reply}})
    failures=[]
    n=trca._store_pending_pdfs(conn,SimpleNamespace(get_bytes=lambda u:CHALLENGE),tmp_path,'%escribemeetings%',lambda m:None,'trca',failures=failures)
    assert n==0 and len(failures)==1 and visits==[url] and closed==[True]
    assert conn.execute('SELECT sha256 FROM background_pdf').fetchone()[0] is None
    assert not list(tmp_path.glob('*.pdf'))


def test_browser_rate_limit_defers_rest_of_pass(conn,monkeypatch,tmp_path):
    replies={BASE+str(n):{'status':429,'url':BASE+str(n),'body':b'limited','retry_after':'120'} for n in [1,2]}
    visits,_,closed=setup(conn,monkeypatch,replies)
    failures=[]
    n=trca._store_pending_pdfs(conn,SimpleNamespace(get_bytes=lambda u:CHALLENGE),tmp_path,'%escribemeetings%',lambda m:None,'trca',failures=failures)
    assert n==0 and visits==[BASE+'1'] and closed==[True]
    assert len(failures)==1 and 'deferred' in failures[0][1]


def test_plain_denial_does_not_start_browser(conn,monkeypatch,tmp_path):
    url=BASE+'1'
    visits,sessions,closed=setup(conn,monkeypatch,{url:{}})
    req=httpx.Request('GET',url)
    def denied(u):
        raise httpx.HTTPStatusError('403',request=req,response=httpx.Response(403,content=b'Forbidden',request=req))
    failures=[]
    assert trca._store_pending_pdfs(conn,SimpleNamespace(get_bytes=denied),tmp_path,'%escribemeetings%',lambda m:None,'trca',failures=failures)==0
    assert not visits and not sessions and not closed and len(failures)==1


def test_pending_agenda_context_survives_calendar_passage(conn,monkeypatch,tmp_path):
    import json
    from toronto_bids import config
    monkeypatch.setattr(config,'TRCA_ESCRIBE_YEARS',[2026])
    monkeypatch.setattr(config,'TRCA_REPORTS_DIR',tmp_path)
    monkeypatch.setattr(trca,'_store_pending_pdfs',lambda *a,**kw:0)
    meeting={'ID':'meeting-id','HasAgenda':True,'MeetingPassed':False,'MeetingDate':'2026-11-06'}
    http=SimpleNamespace(post_json=lambda *a,**kw:{'d':[meeting]},get_text=lambda u:f'<a href="{BASE}21060">Report</a>')
    trca.download_reports(conn,http)
    before=conn.execute('SELECT award_review_context_json FROM background_pdf').fetchone()[0]
    assert json.loads(before)['meeting']['MeetingPassed'] is False
    meeting['MeetingPassed']=True
    trca.download_reports(conn,http)
    assert conn.execute('SELECT award_review_context_json FROM background_pdf').fetchone()[0]==before


def test_future_agenda_award_is_withheld_but_supported_bids_survive(conn):
    import json
    from tests.test_agency_extraction_fields import cache,contract
    from toronto_bids.extraction import backfill_from_extraction
    url=cache(conn,'trca',[contract('10044924')])
    context=json.dumps({'meeting':{'ID':'meeting-id','MeetingPassed':False}})
    conn.execute('UPDATE background_pdf SET award_review_context_json=? WHERE url=?',(context,url))
    # A duplicate source URL for the same bytes must not circumvent the review hold.
    conn.execute("INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,'agency_board','one','held text')",(url+'&duplicate',))
    result=backfill_from_extraction(conn,'trca')
    assert conn.execute('SELECT count(*) FROM agency_bid').fetchone()[0]==1
    assert conn.execute('SELECT count(*) FROM agency_award').fetchone()[0]==0
    assert conn.execute('SELECT status FROM agency_solicitation').fetchone()[0] is None
    rows=conn.execute('SELECT * FROM agency_derivation').fetchall()
    assert all('decision verification' in r['reason'] for r in rows)
    assert result['coverage']['withheld_award_facts']==1


def test_direct_rate_limit_defers_without_browser(conn,monkeypatch,tmp_path):
    replies={BASE+str(n):{} for n in [1,2]}
    visits,sessions,_=setup(conn,monkeypatch,replies)
    direct_visits=[]
    def limited(url):
        direct_visits.append(url)
        req=httpx.Request('GET',url)
        raise httpx.HTTPStatusError('429',request=req,response=httpx.Response(429,headers={'Retry-After':'120'},request=req))
    failures=[]
    assert trca._store_pending_pdfs(conn,SimpleNamespace(get_bytes=limited),tmp_path,'%escribemeetings%',lambda m:None,'trca',failures=failures)==0
    assert direct_visits==[BASE+'1'] and not visits and not sessions
    assert len(failures)==1 and 'deferred' in failures[0][1]


def test_browser_launch_failure_does_not_launch_once_per_document(conn,monkeypatch,tmp_path):
    attempts=[]
    for n in [1,2]:
        conn.execute("INSERT INTO background_pdf(url,kind) VALUES (?,'agency_board')",(BASE+str(n),))
    @contextmanager
    def unavailable():
        attempts.append(True)
        raise RuntimeError('browser unavailable')
        yield
    monkeypatch.setattr(trca,'_browser_fetch',unavailable)
    failures=[]
    assert trca._store_pending_pdfs(conn,SimpleNamespace(get_bytes=lambda u:CHALLENGE),tmp_path,'%escribemeetings%',lambda m:None,'trca',failures=failures)==0
    assert attempts==[True] and len(failures)==2
