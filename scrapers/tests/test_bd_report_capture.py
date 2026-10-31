import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
from toronto_bids.models import BackgroundPdf,Bid
from toronto_bids.store import db
from toronto_bids.sources import bd_reports as b


def seed(conn):
 for year in (2013,2016):
  db.upsert_row(conn,BackgroundPdf(f'https://www.toronto.ca/legdocs/mmis/{year}/bd/bgrd/backgroundfile-{year}.pdf',reference=f'{year}.BD1.1',kind='bgrd'),overwrite=True)
 conn.commit()


@pytest.fixture
def downloads(monkeypatch):
 calls=[]
 def fetch(http,url,directory,layout=False):
  calls.append(url);p=Path(directory)/'report.pdf';p.write_bytes(b'%PDF-1.4\nfixture')
  return dict(local_path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),text='Named bidders')
 monkeypatch.setattr(b,'download_pdf',fetch)
 monkeypatch.setattr(b.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
 return calls


def test_frozen_inventory_resumes_after_a_source_gains_bid_rows(conn,tmp_path,downloads):
 seed(conn);result=b.capture(conn,tmp_path,limit=1,http=object());assert result['held']==1 and result['pending']==1
 db.upsert_row(conn,Bid('New extraction',reference='2013.BD1.1',source='bd_report'),overwrite=True)
 assert len(b.inventory(conn))==1
 result=b.capture(conn,tmp_path,resume=True,http=object())
 assert result['total']==result['held']==2 and result['pending']==0 and len(downloads)==2
 before=[tuple(r) for r in conn.execute('SELECT * FROM background_pdf ORDER BY id')]
 assert b.capture(conn,tmp_path,resume=True,http=object())['attempted']==0
 assert [tuple(r) for r in conn.execute('SELECT * FROM background_pdf ORDER BY id')]==before


def test_corrupt_held_bytes_are_visible_and_not_silently_replaced(conn,tmp_path,downloads):
 seed(conn);b.capture(conn,tmp_path,http=object());row=conn.execute('SELECT local_path FROM background_pdf ORDER BY id').fetchone();Path(row[0]).write_bytes(b'changed')
 result=b.capture(conn,tmp_path,resume=True,http=object())
 assert result['pending']==2 and all('changed' in e['error'] for e in result['failures'])
 assert len(downloads)==2


def test_changed_inventory_context_rejects_before_download(conn,tmp_path,downloads):
 seed(conn);b.capture(conn,tmp_path,limit=1,http=object());conn.execute("UPDATE background_pdf SET reference='2016.BD9.9' WHERE reference='2016.BD1.1'")
 with pytest.raises(ValueError,match='context changed'):b.capture(conn,tmp_path,resume=True,http=object())
 assert len(downloads)==1


def test_non_pdf_response_does_not_mark_source_held(conn,tmp_path,downloads,monkeypatch):
 seed(conn)
 def html(http,url,directory,layout=False):
  p=Path(directory)/'x';p.write_text('<html>blocked</html>');return dict(local_path=str(p),sha256='x',text='blocked')
 monkeypatch.setattr(b,'download_pdf',html)
 result=b.capture(conn,tmp_path,limit=1,http=object());assert result['pending']==2 and result['held']==0
 assert conn.execute('SELECT COUNT(sha256) FROM background_pdf').fetchone()[0]==0


def test_long_retry_after_stops_batch_instead_of_retrying_another_url(conn,tmp_path,monkeypatch):
 seed(conn);calls=[]
 class Http:
  def get_bytes(self,url):
   calls.append(url);r=httpx.Response(429,headers={'Retry-After':'120'},request=httpx.Request('GET',url));raise httpx.HTTPStatusError('rate limited',request=r.request,response=r)
 def fetch(http,url,directory,layout=False):http.get_bytes(url)
 monkeypatch.setattr(b,'download_pdf',fetch)
 result=b.capture(conn,tmp_path,http=Http());assert len(calls)==1 and result['pending']==2
 assert 'resume later' in result['failures'][0]['error']


def test_paced_lane_waits_between_attempts_and_honours_short_retry_after():
 sleeps=[];clock=[0];calls=[]
 def sleep(n):sleeps.append(n);clock[0]+=n
 class Http:
  def get_bytes(self,url):
   calls.append(clock[0])
   if len(calls)==1:
    r=httpx.Response(503,headers={'Retry-After':'4'},request=httpx.Request('GET',url));raise httpx.HTTPStatusError('retry',request=r.request,response=r)
   return b'ok'
 lane=b.PacedHttp(Http(),sleep=sleep,clock=lambda:clock[0]);assert lane.get_bytes('https://example.org')==b'ok';lane.get_bytes('https://example.org/next')
 assert calls==[0,4,5.1]


def test_same_inventory_cannot_run_two_request_lanes(conn,tmp_path,downloads):
 import fcntl
 seed(conn)
 with (tmp_path/'.capture.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  with pytest.raises(ValueError,match='Another capture'):b.capture(conn,tmp_path,http=object())
 assert downloads==[]


def test_cli_dry_run_creates_no_output_or_lock_files(conn,tmp_path,monkeypatch):
 import sqlite3,sys
 seed(conn);path=tmp_path/'bids.sqlite'
 with sqlite3.connect(path) as target:conn.backup(target)
 directory=tmp_path/'untouched'
 monkeypatch.setattr(sys,'argv',['bd_reports','--database',str(path),'--directory',str(directory),'--dry-run'])
 assert b.main()==0
 assert not directory.exists() and not (tmp_path/'.pipeline.lock').exists()


def test_valid_scanned_pdf_is_held_with_visible_text_gap(conn,tmp_path,downloads,monkeypatch):
 seed(conn);original=b.download_pdf
 def scanned(*args,**kwargs):
  result=original(*args,**kwargs);result['text']=None;return result
 monkeypatch.setattr(b,'download_pdf',scanned)
 result=b.capture(conn,tmp_path,limit=1,http=object())
 assert result['held']==1 and result['held_without_text']==1 and result['pending']==1
 assert conn.execute('SELECT COUNT(sha256) FROM background_pdf').fetchone()[0]==1
