"""Bounded anonymous recovery of reviewed public eSCRIBE document gaps (#259).

This opt-in pass captures bytes and provenance only. An alternative minutes package
never marks the original URL held or removes it from the normal download queue.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, UTC
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.trca_board import _pdftotext
from toronto_bids.store import db

HOST = 'pub-trca.escribemeetings.com'
MAX_BYTES = 64 * 1024 * 1024


def document_id(url):
    parsed = urlsplit(url)
    ids = parse_qs(parsed.query).get('DocumentId', [])
    if (parsed.scheme != 'https' or parsed.hostname != HOST or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.path.lower() != '/filestream.ashx'
            or len(ids) != 1 or not re.fullmatch('[1-9][0-9]*', ids[0])):
        raise ValueError('Not a public TRCA FileStream document URL')
    return int(ids[0])


def load_documents():
    documents = json.loads(resources.files('toronto_bids.data').joinpath('trca_document_gaps.json').read_text())['documents']
    seen = set()
    for item in documents:
        key = document_id(item['original_url'])
        if key != item['document_id'] or key in seen or not item['agenda_url'] or not item['agenda_item']:
            raise ValueError('Malformed/duplicate TRCA gap provenance')
        seen.add(key)
        for alternative in item['replacements']:
            document_id(alternative['url'])
            if (not re.fullmatch('[a-f0-9]{64}', alternative['sha256'])
                    or not alternative['meeting_id'] or not alternative['identity_excerpt']):
                raise ValueError('Replacement lacks reviewed byte/meeting provenance')
    return documents


@contextmanager
def browser_fetch():
    """One ordinary headed anonymous browser; no account, stealth or challenge bypass."""
    from playwright.sync_api import sync_playwright
    from pyvirtualdisplay import Display

    with Display(visible=False, size=(1440, 900)), sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            page.goto(f'https://{HOST}/MeetingsCalendarView.aspx', wait_until='domcontentloaded', timeout=45000)
            previous = time.monotonic()

            def fetch(url):
                nonlocal previous
                document_id(url)
                time.sleep(max(0, 1.0 - (time.monotonic() - previous)))
                previous = time.monotonic()
                result = page.evaluate('''async ({url, maximum}) => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), 60000);
                    try {
                        const r = await fetch(url, {signal: controller.signal});
                        if (Number(r.headers.get('content-length')) > maximum)
                            throw new Error('Document exceeds byte limit');
                        const bytes = new Uint8Array(await r.arrayBuffer());
                        if (bytes.length > maximum) throw new Error('Document exceeds byte limit');
                        let binary = '';
                        for (let i = 0; i < bytes.length; i += 32768)
                            binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
                        return {status: r.status, url: r.url,
                                content_type: r.headers.get('content-type'), body: btoa(binary)};
                    } finally { clearTimeout(timer); }
                }''', {'url': url, 'maximum': MAX_BYTES})
                result['body'] = base64.b64decode(result['body'], validate=True)
                return result

            yield fetch
        finally:
            browser.close()


def _validated(blob, directory, expected_sha=None):
    if not isinstance(blob, bytes) or not blob.startswith(b'%PDF-') or len(blob) > MAX_BYTES:
        raise ValueError('Response is not PDF bytes or exceeds the byte limit (viewer/verification HTML rejected)')
    sha = hashlib.sha256(blob).hexdigest()
    if expected_sha is not None and sha != expected_sha:
        raise ValueError('Replacement/held bytes differ from the reviewed SHA-256; new source review required')
    with tempfile.NamedTemporaryFile(dir=directory, suffix='.pdf', delete=False) as temp:
        path = Path(temp.name)
        temp.write(blob)
    try:
        info = subprocess.run(['pdfinfo', str(path)], capture_output=True, timeout=120)
        pages = re.search(rb'^Pages:\s+(\d+)\s*$', info.stdout, re.M)
        if info.returncode or not pages or int(pages[1]) < 1:
            raise ValueError('PDF structure could not be validated with pdfinfo')
        return {'sha256': sha, 'bytes': len(blob), 'pages': int(pages[1])}, path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _held(conn, url, directory, expected_sha=None):
    row = conn.execute('SELECT * FROM background_pdf WHERE url=?', (url,)).fetchone()
    if row is None or row['sha256'] is None:
        return None
    if not row['local_path']:
        raise ValueError('Held document has no local path; archive inconsistency')
    info, temp = _validated(Path(row['local_path']).read_bytes(), directory, expected_sha or row['sha256'])
    temp.unlink()
    if info['sha256'] != row['sha256']:
        raise ValueError('Held document does not match its indexed hash')
    return info | {'url': url, 'already_held': True}


def _archive(conn, url, blob, directory, expected_sha=None):
    info, temp = _validated(blob, directory, expected_sha)
    try:
        held = _held(conn, url, directory, expected_sha)
        if held is not None:
            return held
        path = directory / (info['sha256'] + '.pdf')
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != info['sha256']:
                raise ValueError('Existing content-addressed path contains different bytes')
        else:
            os.replace(temp, path)
        if conn.execute('SELECT 1 FROM background_pdf WHERE url=?', (url,)).fetchone() is None:
            db.upsert_row(conn, BackgroundPdf(url=url, kind='agency_board'), overwrite=False)
        updated = conn.execute('UPDATE background_pdf SET sha256=?,local_path=?,text=? WHERE url=? AND sha256 IS NULL',
                               (info['sha256'], str(path), _pdftotext(path), url)).rowcount
        conn.commit()
        if not updated:
            held = _held(conn, url, directory, expected_sha)
            if held is None:
                raise ValueError('Document changed during archival; held bytes could not be verified')
            return held
        return info | {'url': url, 'already_held': False}
    finally:
        temp.unlink(missing_ok=True)


def _fetch_pdf(conn, fetch, url, directory, expected_sha=None):
    document_id(url)
    held = _held(conn, url, directory, expected_sha)
    if held is not None:
        return held, None
    response = fetch(url)
    blob = response['body']
    title = re.search(rb'<title[^>]*>(.*?)</title>', blob[:10000], re.I | re.S)
    observation = {k: response[k] for k in ('status', 'url', 'content_type')}
    observation.update(bytes=len(blob), body_sha256=hashlib.sha256(blob).hexdigest(),
                       title=title[1].decode('utf-8', errors='replace')[:200] if title else None)
    try:
        same_document = document_id(response['url']) == document_id(url)
    except ValueError:
        same_document = False
    if not same_document:
        return None, observation | {'error': 'Response redirected outside the requested public document'}
    if response['status'] != 200:
        return None, observation | {'error': f"Browser HTTP {response['status']}; availability cause unverified"}
    try:
        return _archive(conn, url, blob, directory, expected_sha), observation
    except Exception as exc:
        return None, observation | {'error': str(exc)}


def _write_json(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as temp:
        temp.write(text)
        name = Path(temp.name)
    try:
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


def recover_documents(conn, fetch, *, documents=None, reports_dir, log=lambda _m: None):
    """Attempt each reviewed original and, only if needed, its published alternatives."""
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    report = dict(generated_at=datetime.now(UTC).isoformat(), documents=[], failures=[],
                  originals_held=0, replacement_coverage=0, unresolved_originals=0, without_verified_replacement=0)
    for item in load_documents() if documents is None else documents:
        url = item['original_url']
        document_id(url)
        if conn.execute('SELECT 1 FROM background_pdf WHERE url=?', (url,)).fetchone() is None:
            db.upsert_row(conn, BackgroundPdf(url=url, kind='agency_board'), overwrite=False)
            conn.commit()
        record = dict(provenance=item, original=None, observation=None, replacements=[])
        try:
            record['original'], record['observation'] = _fetch_pdf(conn, fetch, url, directory)
        except Exception as exc:
            record['observation'] = {'error': str(exc)}
        if record['original'] is not None:
            record['outcome'] = 'original_held'
            report['originals_held'] += 1
        else:
            report['unresolved_originals'] += 1
            for replacement in item['replacements']:
                try:
                    held, observation = _fetch_pdf(conn, fetch, replacement['url'], directory, replacement['sha256'])
                except Exception as exc:
                    held, observation = None, {'error': str(exc)}
                record['replacements'].append(dict(provenance=replacement, held=held, observation=observation))
            covered = any(r['held'] is not None for r in record['replacements'])
            record['outcome'] = 'replacement_held_original_unresolved' if covered else 'unresolved'
            report['replacement_coverage'] += int(covered)
            report['without_verified_replacement'] += int(not covered)
            report['failures'].append(f"{url}: {record['observation']['error']}; original left queued; "
                                      + ('verified alternative held separately' if covered else 'no verified alternative held'))
        report['documents'].append(record)
        log(f"  {item['document_id']}: {record['outcome']}")
    text = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    digest = hashlib.sha256(text.encode()).hexdigest()
    _write_json(directory / 'recovery_runs' / (digest + '.json'), text)
    _write_json(directory / 'document-recovery.json', text)
    return report
