"""Resumable capture of the known 2013–2016 Bid Committee report inventory.

python -m toronto_bids.sources.bd_reports --database PATH --directory PATH [--dry-run|--resume]
Capture retains evidence only. Extraction, identity review and publication are separate.
"""
import argparse
import fcntl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import urlparse

import httpx
from toronto_bids.http import HttpClient
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.council import download_pdf
from toronto_bids.store import db


def inventory(conn):
    return [dict(r) for r in conn.execute(
        "SELECT p.url,p.reference FROM background_pdf p WHERE p.kind='bgrd' "
        "AND substr(p.reference,1,4) BETWEEN '2013' AND '2016' "
        "AND p.reference LIKE '%.BD%' AND NOT EXISTS "
        "(SELECT 1 FROM bid b WHERE b.reference=p.reference) ORDER BY p.reference,p.url")]


def _valid_source(url, reference):
    parsed = urlparse(url)
    return (parsed.scheme in ('https','http') and parsed.hostname in ('www.toronto.ca','toronto.ca')
            and re.fullmatch(r'201[3-6]\.BD\d+\.\d+', reference) is not None
            and re.fullmatch(r'/legdocs/mmis/'+reference[:4]+r'/bd/bgrd/backgroundfile-\d+\.pdf', parsed.path) is not None
            and not parsed.query and not parsed.username)


def _save(path, manifest):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(manifest, indent=2)+'\n')
    temp.replace(path)


class CaptureDeferred(RuntimeError):
    pass


class PacedHttp:
    """One serial request lane; long Retry-After defers this item to a later run."""
    def __init__(self, http, sleep=time.sleep, clock=time.monotonic):
        self.http, self.sleep, self.clock = http, sleep, clock
        self.last = None

    def get_bytes(self, url):
        for attempt in range(3):
            if self.last is not None:
                self.sleep(max(0, 1.1-(self.clock()-self.last)))
            self.last = self.clock()
            try:
                return self.http.get_bytes(url)
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                delay = 2**attempt
                if isinstance(exc, httpx.HTTPStatusError):
                    if exc.response.status_code != 429 and exc.response.status_code < 500:
                        raise
                    header = exc.response.headers.get('retry-after')
                    if header:
                        try:
                            retry = float(header)
                        except ValueError:
                            try:
                                retry = (parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds()
                            except (ValueError, TypeError, OverflowError) as error:
                                raise CaptureDeferred('Unusable Retry-After; defer the capture') from error
                        delay = max(delay, retry)
                if delay > 60:
                    raise CaptureDeferred(f'Server requests at least {delay:g}s; resume later') from exc
                if attempt == 2:
                    raise CaptureDeferred('Server/transport failure persists; resume later') from exc
                self.sleep(delay)


def _validate_pdf(path):
    with path.open('rb') as stream:
        if stream.read(5) != b'%PDF-':
            raise ValueError('Response is not a PDF')
    if subprocess.run(['pdfinfo',str(path)],capture_output=True).returncode:
        raise ValueError('PDF structure could not be read')


def capture(conn, directory, *, resume=False, limit=None, http=None, log=lambda _m: None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'.capture.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another capture owns this inventory') from error
        return _capture(conn, directory, resume=resume, limit=limit, http=http, log=log)


def _capture(conn, directory, *, resume=False, limit=None, http=None, log=lambda _m: None):
    if limit is not None and limit < 1:
        raise ValueError('limit must be positive')
    directory = Path(directory)
    manifest_path = directory/'inventory.json'
    if resume:
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('version') != 1:
            raise ValueError('Unsupported inventory version')
    else:
        if manifest_path.exists():
            raise ValueError('Inventory already exists; use --resume')
        manifest = dict(version=1,scope='2013–2016 BD report URLs with no stored bid observations',
                        entries=[dict(**row,state='pending',sha256=None,error=None) for row in inventory(conn)])
    entries = manifest['entries']
    if len({r['url'] for r in entries}) != len(entries):
        raise ValueError('Repeated report URL in inventory')
    # Context validation precedes network or mutations, including manifest writes.
    for row in entries:
        held = conn.execute('SELECT * FROM background_pdf WHERE url=?',(row['url'],)).fetchone()
        if not _valid_source(row['url'],row['reference']) or not held or held['reference'] != row['reference'] or held['kind'] != 'bgrd':
            raise ValueError('Inventory source context changed: '+row['url'])
    directory.mkdir(parents=True,exist_ok=True)
    own = http is None
    raw = http or HttpClient(retries=0)
    lane = PacedHttp(raw)
    attempted = 0
    try:
        for row in entries:
            held = conn.execute('SELECT * FROM background_pdf WHERE url=?',(row['url'],)).fetchone()
            if held['sha256'] is None and limit is not None and attempted >= limit:
                continue
            try:
                if held['sha256']:
                    path = Path(held['local_path'] or '')
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != held['sha256']:
                        raise ValueError('Held report bytes are missing or changed')
                    _validate_pdf(path)
                    if row.get('sha256') not in (None,held['sha256']):
                        raise ValueError('Manifest hash disagrees with held bytes')
                    row.update(state='held',sha256=held['sha256'],text_available=bool((held['text'] or '').strip()),error=None)
                else:
                    if row.get('sha256'):
                        raise ValueError('Previously held source metadata was removed')
                    attempted += 1
                    with tempfile.TemporaryDirectory(prefix='.bd-',dir=directory) as stage:
                        info = download_pdf(lane,row['url'],stage,layout=True)
                        source = Path(info['local_path'])
                        _validate_pdf(source)
                        target = directory/(info['sha256']+'.pdf')
                        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != info['sha256']:
                            raise ValueError('Existing content-addressed file changed')
                        if not target.exists():
                            shutil.copy2(source,target)
                        db.upsert_row(conn,BackgroundPdf(row['url'],reference=row['reference'],kind='bgrd',
                            local_path=str(target.resolve()),sha256=info['sha256'],text=info['text']),overwrite=True)
                        conn.commit()
                    row.update(state='held',sha256=info['sha256'],text_available=bool((info['text'] or '').strip()),error=None)
                log(row['reference']+': held')
            except Exception as exc:
                conn.rollback()
                row.update(state='pending',error=str(exc))
                log(row['reference']+': pending — '+str(exc))
                if isinstance(exc, CaptureDeferred):
                    break
            _save(manifest_path,manifest)
    finally:
        if own:
            raw.close()
    _save(manifest_path,manifest)
    held = sum(r['state']=='held' for r in entries)
    return dict(total=len(entries),held=held,pending=len(entries)-held,attempted=attempted,
                held_without_text=sum(r['state']=='held' and not r.get('text_available') for r in entries),
                failures=[dict(url=r['url'],reference=r['reference'],error=r['error']) for r in entries if r.get('error')],
                inventory=str(manifest_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--limit',type=int)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('database does not exist')
    if args.dry_run:
        with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as conn:
            conn.row_factory=sqlite3.Row
            rows=inventory(conn)
        print(json.dumps(dict(total=len(rows),by_year={str(y):sum(r['reference'].startswith(str(y)) for r in rows) for y in range(2013,2017)}),indent=2))
        return 0
    with (args.database.resolve().parent/'.pipeline.lock').open('a') as pipeline:
        fcntl.flock(pipeline, fcntl.LOCK_EX)
        conn=db.connect(args.database)
        try:
            result=capture(conn,args.directory,resume=args.resume,limit=args.limit,log=print)
            print(json.dumps(result,indent=2))
            return int(result['pending']>0)
        finally:
            conn.close()



if __name__=='__main__':
    raise SystemExit(main())
