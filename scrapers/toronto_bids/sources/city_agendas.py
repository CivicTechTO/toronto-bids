"""Resumable all-term City agenda/item/report inventory, independent of voting records.

The inventory is discovery evidence. Printed identifiers and candidate matches do
not create procurement identities or bidder facts.
"""
import csv
from datetime import date, datetime, timezone, timedelta
import fcntl
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import html

PACKAGE = '3bfab808-105b-45ab-8d92-cf1916594653'
BODIES = {
    'City Council': 'CC',
    'Executive Committee': 'EX',
    'Government Management Committee': 'GM',
    'General Government and Licensing Committee': 'GL',
    'General Government Committee': 'GG',
    'Public Works and Infrastructure Committee': 'PW',
    'Infrastructure and Environment Committee': 'IE',
}
_HEADING = re.compile(r'^([A-Z]{1,5}\d+\.\d+[a-z]?)\s*[-–]\s*(.+)$')
_PROCUREMENT = re.compile(r'\b(?:award|contract|procurement|tender|RF[PTQ]|proposal|quotation)\b', re.I)
_NONCOMP = re.compile(r'\b(?:non[- ]competitive|sole[- ]source)\b', re.I)


def clean(text):
    return ' '.join((text or '').split())


def schedule(csv_text, *, from_year=2010, through_year=2026, today=None):
    """Reconcile term-scoped meeting numbers and all their scheduled dates."""
    today = today or date.today()
    reader = csv.DictReader(io.StringIO(csv_text.lstrip('\ufeff')))
    columns = set(reader.fieldnames or [])
    number_column = 'MTG #' if 'MTG #' in columns else 'MTGNum'
    if not {'Term', 'Committee', 'Date', number_column} <= columns:
        raise ValueError('City meeting schedule schema changed')
    groups = {}
    for row in reader:
        body = row['Committee'].strip()
        if body not in BODIES:
            continue
        number = row[number_column].strip()
        if not number.isdigit() or int(number) < 1:
            raise ValueError('Invalid scheduled meeting number')
        day = date.fromisoformat(row['Date'].strip()[:10])
        if not from_year <= day.year <= through_year:
            continue
        key = (row['Term'].strip(), BODIES[body], int(number))
        meeting = groups.setdefault(key, dict(term=key[0], body=body, code=key[1], number=key[2], dates=[]))
        if day.isoformat() not in meeting['dates']:
            meeting['dates'].append(day.isoformat())
    for meeting in groups.values():
        meeting['dates'].sort()
        meeting['reference'] = f"{meeting['dates'][0][:4]}.{meeting['code']}{meeting['number']}"
        meeting['state'] = 'future' if meeting['dates'][0] > today.isoformat() else 'pending'
    return sorted(groups.values(), key=lambda m: (m['dates'][0], m['reference']))


def parse_agenda(source, meeting):
    """Validate a complete agenda and retain item-scoped text and every PDF link."""
    root = html.fromstring(source.decode('utf-8') if isinstance(source,bytes) else source)
    title = clean(' '.join(root.xpath('//title/text()')))
    visible = clean(' '.join(root.xpath('//body//text()[not(ancestor::script) and not(ancestor::style)]')))
    header = visible.split(' - ', 1)[0] if not root.xpath('//h3') else visible[:visible.find(clean(root.xpath('//h3')[0].text_content()))]
    published_date = re.match(r'(\d{4}-\d{2}-\d{2})\s+Agenda\b', title)
    if (not published_date or published_date[1] not in meeting['dates'] or
            meeting['body'].casefold() not in title.casefold() or
            not re.search(r'\bMeeting No\.?\s*:\s*'+str(meeting['number'])+r'\b', header)):
        raise ValueError('Agenda date/body/meeting number does not match the official schedule')
    if re.search(r'\b(?:meeting (?:is |has been )?cancelled|meeting canceled)\b', header, re.I):
        return dict(state='cancelled', date=published_date[1], items=[])
    items = []
    for heading in root.xpath('//h3'):
        match = _HEADING.match(clean(heading.text_content()))
        if not match:
            continue
        nodes = []
        for sibling in heading.itersiblings():
            if sibling.tag == 'h3':
                break
            if isinstance(sibling.tag, str):
                nodes.append(sibling)
        bare, item_title = match.groups()
        ref = published_date[1][:4]+'.'+bare
        attachments = {}
        for node in nodes:
            for anchor in node.xpath('.//a[@href]'):
                original = anchor.get('href')
                url = urljoin('https://www.toronto.ca', original)
                parts = urlsplit(url)
                if (parts.hostname not in ('www.toronto.ca','toronto.ca','secure.toronto.ca') or
                        not parts.path.startswith('/legdocs/mmis/') or not parts.path.lower().endswith('.pdf')):
                    continue
                url = urlunsplit((parts.scheme,parts.netloc,parts.path,parts.query,''))
                item = attachments.setdefault(url,dict(url=url,labels=[],original_hrefs=[]))
                label = clean(anchor.text_content())
                if label not in item['labels']:item['labels'].append(label)
                if original not in item['original_hrefs']:item['original_hrefs'].append(original)
        item_text = '\n'.join(clean(node.text_content()) for node in nodes if clean(node.text_content()))
        items.append(dict(ordinal=len(items),reference=ref,title=item_title,text=item_text,
            procurement_candidate=bool(_PROCUREMENT.search(item_title)) and not bool(_NONCOMP.search(item_title)),
            explicitly_noncompetitive=bool(_NONCOMP.search(item_title)),attachments=list(attachments.values())))
    if not items:
        raise ValueError('Validated agenda has no parsed item headings; completeness is unverified')
    return dict(state='held',date=published_date[1],items=items)


def connect(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(directory/'inventory.sqlite',timeout=30);conn.row_factory=sqlite3.Row
    conn.executescript('''
      CREATE TABLE IF NOT EXISTS meeting (
        reference TEXT PRIMARY KEY,term TEXT NOT NULL,body TEXT NOT NULL,number INTEGER NOT NULL,
        dates_json TEXT NOT NULL,state TEXT NOT NULL,source_sha256 TEXT,local_path TEXT,error TEXT,
        updated_at TEXT DEFAULT (datetime('now')));
      CREATE TABLE IF NOT EXISTS item (
        meeting TEXT NOT NULL,source_sha256 TEXT NOT NULL,ordinal INTEGER NOT NULL,
        reference TEXT NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,candidate INTEGER NOT NULL,
        noncompetitive INTEGER NOT NULL,PRIMARY KEY(meeting,source_sha256,ordinal));
      CREATE TABLE IF NOT EXISTS attachment (
        meeting TEXT NOT NULL,source_sha256 TEXT NOT NULL,ordinal INTEGER NOT NULL,url TEXT NOT NULL,
        labels_json TEXT NOT NULL,original_hrefs_json TEXT NOT NULL,
        PRIMARY KEY(meeting,source_sha256,ordinal,url));
      CREATE TABLE IF NOT EXISTS report (
        url TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT 'pending',sha256 TEXT,local_path TEXT,text TEXT,
        error TEXT,updated_at TEXT DEFAULT (datetime('now')));
    ''')
    return conn


def seed(conn, meetings):
    for meeting in meetings:
        old=conn.execute('SELECT * FROM meeting WHERE reference=?',(meeting['reference'],)).fetchone()
        if old and (old['term']!=meeting['term'] or old['body']!=meeting['body'] or old['number']!=meeting['number']):
            raise ValueError('Schedule reference conflicts across terms/bodies')
        conn.execute('INSERT INTO meeting (reference,term,body,number,dates_json,state) VALUES (?,?,?,?,?,?) '
            "ON CONFLICT(reference) DO UPDATE SET dates_json=excluded.dates_json,state=CASE WHEN meeting.state='future' THEN excluded.state ELSE meeting.state END",
            (meeting['reference'],meeting['term'],meeting['body'],meeting['number'],json.dumps(meeting['dates']),meeting['state']))
    conn.commit()


class CaptureDeferred(ValueError):
    def __init__(self,message,retry_after=120):
        super().__init__(message);self.retry_after=retry_after


class InventoryBusy(ValueError):
    """Another process owns this inventory's request lane."""


def maintenance_page(source):
    """Recognize the platform's dedicated unavailable page, not agenda prose."""
    root=html.fromstring(source)
    crumbs=[clean(node.text_content()).casefold() for node in root.xpath('//*[@id="breadcrumbs"]//li')]
    notice=clean(' '.join(root.xpath('//*[@id="main"]//text()'))).casefold()
    return 'system maintenance' in crumbs and 'currently performing scheduled maintenance' in notice


class Browser:
    """One ordinary anonymous headed browser; no persisted cookies or auth headers."""
    def __enter__(self):
        from pyvirtualdisplay import Display
        from playwright.sync_api import sync_playwright
        self.display=Display(visible=False,size=(1440,900));self.display.start()
        self.pw=sync_playwright().start();self.browser=self.pw.chromium.launch(headless=False,args=['--disable-blink-features=AutomationControlled'])
        self.page=self.browser.new_context().new_page();self.last=None
        return self

    def fetch(self, reference):
        if self.last is not None:time.sleep(max(0,1.1-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        try:
            response=self.page.goto('https://secure.toronto.ca/council/report.do?meeting='+reference+'&type=agenda',wait_until='domcontentloaded',timeout=45000)
        except Exception as exc:raise CaptureDeferred('Agenda transport failed; defer this capture') from exc
        self.page.wait_for_timeout(900)
        if response is None or response.status in (403,429) or response.status>=500:
            retry=120
            if response:
                value=response.headers.get('retry-after')
                if value:
                    from email.utils import parsedate_to_datetime
                    try:retry=max(retry,int(value))
                    except ValueError:
                        try:retry=max(retry,int((parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()))
                        except (ValueError,TypeError,OverflowError):pass
            raise CaptureDeferred('Source blocked/deferred HTTP '+str(response.status if response else 'unknown'),retry)
        if response.status>=400:raise ValueError('Agenda HTTP '+str(response.status))
        source=self.page.content()
        if maintenance_page(source):raise CaptureDeferred('TMMIS reports scheduled maintenance; resume later',1800)
        if re.search(r'access denied|just a moment|pardon our interruption|request rejected',self.page.title(),re.I):
            raise CaptureDeferred('Source verification page; defer this capture')
        return source

    def __exit__(self,*args):
        self.browser.close();self.pw.stop();self.display.stop()


def capture(directory, *, browser, limit=None, log=lambda m:None):
    """Resume every scheduled meeting, checkpointing one validated source at a time."""
    directory=Path(directory);conn=connect(directory);count=0
    lock=(directory/'.capture.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        conn.close();lock.close();raise InventoryBusy('Another capture owns this inventory')
    try:
        deferred=directory/'deferred-until.json'
        backing_off=deferred.exists() and datetime.fromisoformat(json.loads(deferred.read_text())['until'])>datetime.now(timezone.utc)
        for row in conn.execute("SELECT * FROM meeting WHERE state IN ('held','cancelled')").fetchall():
            path=Path(row['local_path'])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['source_sha256']:
                with conn:conn.execute("UPDATE meeting SET state='failed',error='Held source missing or changed' WHERE reference=?",(row['reference'],))
        rows=conn.execute("SELECT * FROM meeting WHERE state IN ('pending','failed') ORDER BY local_path IS NULL,substr(reference,1,4) DESC,reference").fetchall()
        rows=sorted(rows,key=lambda row:not (directory/'agendas'/(row['reference']+'.html')).exists())
        for row in rows:
            if limit is not None and count>=limit:break
            if backing_off and not (directory/'agendas'/(row['reference']+'.html')).exists():
                log('Agenda source backoff remains active; held work is preserved')
                break
            meeting=dict(reference=row['reference'],term=row['term'],body=row['body'],number=row['number'],dates=json.loads(row['dates_json']))
            try:
                path=directory/'agendas'/(row['reference']+'.html')
                source=path.read_bytes() if path.exists() else browser.fetch(row['reference']).encode()
                sha=hashlib.sha256(source).hexdigest()
                try:parsed=parse_agenda(source,meeting)
                except ValueError:
                    rejected=directory/'rejected';rejected.mkdir(exist_ok=True)
                    (rejected/(row['reference']+'.'+sha+'.html')).write_bytes(source)
                    raise
                if row['source_sha256'] and row['source_sha256']!=sha:
                    raise ValueError('Previously held agenda bytes changed; explicit refresh required')
                path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():path.write_bytes(source)
                with conn:
                    for item in parsed['items']:
                        conn.execute('INSERT OR IGNORE INTO item VALUES (?,?,?,?,?,?,?,?)',(row['reference'],sha,item['ordinal'],item['reference'],item['title'],item['text'],int(item['procurement_candidate']),int(item['explicitly_noncompetitive'])))
                        for attachment in item['attachments']:
                            conn.execute('INSERT OR IGNORE INTO attachment VALUES (?,?,?,?,?,?)',(row['reference'],sha,item['ordinal'],attachment['url'],json.dumps(attachment['labels']),json.dumps(attachment['original_hrefs'])))
                            if item['procurement_candidate']:conn.execute('INSERT OR IGNORE INTO report (url) VALUES (?)',(attachment['url'],))
                    conn.execute("UPDATE meeting SET state=?,source_sha256=?,local_path=?,error=NULL,updated_at=datetime('now') WHERE reference=?",(parsed['state'],sha,str(path.resolve()),row['reference']))
                log(row['reference']+': '+parsed['state']+'; '+str(len(parsed['items']))+' items')
            except Exception as exc:
                conn.rollback()
                with conn:conn.execute("UPDATE meeting SET state='failed',error=?,updated_at=datetime('now') WHERE reference=?",(str(exc),row['reference']))
                log(row['reference']+': failed — '+str(exc))
                if isinstance(exc,CaptureDeferred):
                    deferred.write_text(json.dumps(dict(until=(datetime.now(timezone.utc)+timedelta(seconds=exc.retry_after)).isoformat(),reason=str(exc)))+'\n')
                    break
            count+=1
        result=status(conn)
        if deferred.exists():
            until=json.loads(deferred.read_text())['until']
            if datetime.fromisoformat(until)>datetime.now(timezone.utc):result['deferred_until']=until
        return result
    finally:conn.close();lock.close()


def status(conn):
    return dict(meetings=dict(conn.execute('SELECT state,COUNT(*) FROM meeting GROUP BY state').fetchall()),
        items=conn.execute('SELECT COUNT(*) FROM item').fetchone()[0],
        procurement_items=conn.execute('SELECT COUNT(*) FROM item WHERE candidate=1').fetchone()[0],
        candidate_reports=conn.execute('SELECT COUNT(*) FROM report').fetchone()[0],
        reports=dict(conn.execute('SELECT state,COUNT(*) FROM report GROUP BY state').fetchall()))


def seed_schedules(directory, *, http, from_year=2010, through_year=None):
    """Resolve official CSV resources through package metadata; pin enumeration inputs."""
    directory=Path(directory);conn=connect(directory)
    snapshots=directory/'schedules';snapshots.mkdir(exist_ok=True)
    manifest=snapshots/'manifest.json'
    try:
        if manifest.exists():
            resources=json.loads(manifest.read_text())
        else:
            package=http.get_json('https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show',params={'id':PACKAGE})
            if not package.get('success'):raise ValueError('Meeting schedule package failed')
            resources=[]
            for resource in package['result']['resources']:
                if str(resource.get('format','')).casefold()!='csv':continue
                url=resource.get('url','')
                parts=urlsplit(url)
                if parts.scheme!='https' or parts.hostname!='ckan0.cf.opendata.inter.prod-toronto.ca':
                    raise ValueError('Unrecognized official schedule resource')
                data=http.get_bytes(url)
                sha=hashlib.sha256(data).hexdigest();path=snapshots/(sha+'.csv')
                path.write_bytes(data)
                # A resource must parse before it can enter a durable manifest.
                schedule(data.decode('utf-8-sig'),from_year=from_year,through_year=through_year or date.today().year)
                resources.append(dict(url=url,path=str(path.resolve()),sha256=sha))
                time.sleep(1.1)
            if not resources:raise ValueError('No CSV meeting schedules advertised')
            manifest.write_text(json.dumps(resources,indent=2)+'\n')
        for resource in resources:
            data=Path(resource['path']).read_bytes()
            if hashlib.sha256(data).hexdigest()!=resource['sha256']:raise ValueError('Schedule snapshot changed')
            seed(conn,schedule(data.decode('utf-8-sig'),from_year=from_year,through_year=through_year or date.today().year))
        return status(conn)
    finally:conn.close()


def capture_reports(directory, *, http, limit=None, log=lambda m:None):
    """Archive candidate attachments once, retaining immutable bytes and native text.

    Attachment candidacy is deliberately broad. Neither the filename nor a
    procurement title qualifies a report for model extraction or fact admission.
    """
    from toronto_bids.sources.bd_reports import PacedHttp, CaptureDeferred as PdfDeferred, _validate_pdf
    directory=Path(directory);conn=connect(directory);count=0
    lane=PacedHttp(http)
    with (directory/'.reports.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            conn.close();raise InventoryBusy('Another report capture owns this inventory')
        try:
            deferred=directory/'reports-deferred-until.json'
            if deferred.exists() and datetime.fromisoformat(json.loads(deferred.read_text())['until'])>datetime.now(timezone.utc):
                raise CaptureDeferred('Report source backoff is still active',3600)
            for row in conn.execute("SELECT * FROM report WHERE state='held'").fetchall():
                path=Path(row['local_path'])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
                    with conn:conn.execute("UPDATE report SET state='failed',error='Held PDF missing or changed' WHERE url=?",(row['url'],))
            for row in conn.execute("SELECT * FROM report WHERE state IN ('pending','failed') ORDER BY url DESC").fetchall():
                if limit is not None and count>=limit:break
                try:
                    if row['sha256']:raise ValueError('Previously held PDF missing or changed; explicit repair required')
                    data=lane.get_bytes(row['url']);sha=hashlib.sha256(data).hexdigest()
                    path=directory/'reports'/(sha+'.pdf');path.parent.mkdir(exist_ok=True)
                    if path.exists() and path.read_bytes()!=data:raise ValueError('Content-addressed PDF changed')
                    if not path.exists():path.write_bytes(data)
                    _validate_pdf(path)
                    proc=subprocess.run(['pdftotext','-q',str(path),'-'],capture_output=True,text=True)
                    if proc.returncode:raise ValueError('PDF native text extraction failed')
                    with conn:conn.execute("UPDATE report SET state='held',sha256=?,local_path=?,text=?,error=NULL,updated_at=datetime('now') WHERE url=?",(sha,str(path.resolve()),proc.stdout.strip() or None,row['url']))
                    log('held '+row['url'])
                except Exception as exc:
                    with conn:conn.execute("UPDATE report SET state='failed',error=?,updated_at=datetime('now') WHERE url=?",(str(exc),row['url']))
                    log('failed '+row['url']+': '+str(exc))
                    if isinstance(exc,PdfDeferred):
                        # PacedHttp has already honored short retries; defer the lane
                        # instead of running the same failing service for every PDF.
                        deferred.write_text(json.dumps(dict(until=(datetime.now(timezone.utc)+timedelta(seconds=max(120,exc.retry_after))).isoformat(),reason=str(exc)))+'\n')
                        break
                count+=1
            return status(conn)
        finally:conn.close()


def main():
    import argparse
    from toronto_bids.http import HttpClient
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step',choices=['seed','capture','reports','status'])
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--from-year',type=int,default=2010)
    parser.add_argument('--through-year',type=int,default=date.today().year)
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    if args.limit is not None and args.limit<1:parser.error('--limit must be positive')
    if args.step=='capture':
        with Browser() as browser:result=capture(args.directory,browser=browser,limit=args.limit,log=print)
    elif args.step in ('seed','reports'):
        http=HttpClient(retries=0)
        try:
            result=(seed_schedules(args.directory,http=http,from_year=args.from_year,through_year=args.through_year)
                    if args.step=='seed' else capture_reports(args.directory,http=http,limit=args.limit,log=print))
        finally:http.close()
    else:
        conn=connect(args.directory)
        try:result=status(conn)
        finally:conn.close()
    print(json.dumps(result,indent=2))
    if args.step=='capture' and result['meetings'].get('failed'):raise SystemExit(1)
    if args.step=='reports' and result['reports'].get('failed'):raise SystemExit(1)


if __name__=='__main__':main()
