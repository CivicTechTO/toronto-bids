"""Bounded TTC Board history: reconcile public discovery, then preserve every PDF link.

Capture is independent of paid extraction. Roles describe published links, not
approved awards; archived reports require separate source review before extraction.
"""
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import httpx
from lxml import html

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.merx import PublicSession
from toronto_bids.store import db

ARCHIVE_URL = 'https://www.ttc.ca/All-public-meetings'
HOSTS = ('www.ttc.ca', 'cdn.ttc.ca', 'pw.ttc.ca')
KINDS = {role: 'ttc_archive_' + role for role in ('report', 'decision', 'agenda', 'minutes', 'document')}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)


def _text(node):
    return ' '.join(node.text_content().split())


def _allowed(url):
    p = urlsplit(url)
    return p.scheme == 'https' and p.netloc in HOSTS and not p.username and not p.password


def _configuration(raw):
    tree = html.fromstring(raw)
    nodes = tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," search-results ")][@data-properties]')
    _require(len(nodes) == 1, 'TTC archive lacks a unique published search configuration')
    settings = json.loads(nodes[0].get('data-properties'))
    endpoint = urlsplit(settings['endpoint'])
    _require(_allowed(settings['endpoint']) and endpoint.netloc == 'www.ttc.ca'
             and endpoint.path.lstrip('/') == 'sxa/search/results/' and not endpoint.query and not endpoint.fragment,
             'Unexpected TTC search endpoint')
    size = settings['p']
    _require(type(size) is int and 1 <= size <= 100, 'Invalid TTC search page size')
    for key in ('s', 'v', 'itemid'):
        _require(isinstance(settings.get(key), str) and settings[key], 'Missing TTC search scope ' + key)
    return settings


def _search_url(settings, year, board, offset):
    params = {k: settings[k] for k in ('s', 'v', 'itemid')}
    params.update(sig=settings.get('sig', ''), autoFireSearch='true',
                  contentyearfacet=year, p=settings['p'], e=offset,
                  o=settings['defaultSortOrder'])
    if board:
        params['meetingtype'] = 'Board'
    return settings['endpoint'] + '?' + urlencode(params)


def _page(raw, year):
    data = json.loads(raw)
    _require(isinstance(data, dict) and type(data.get('Count')) is int and data['Count'] >= 0
             and isinstance(data.get('Results'), list), 'Malformed TTC search count/results')
    rows = []
    for result in data['Results']:
        _require(isinstance(result, dict) and isinstance(result.get('Id'), str) and result['Id']
                 and isinstance(result.get('Url'), str), 'Malformed TTC meeting identity')
        url = urljoin(ARCHIVE_URL, result['Url'])
        p = urlsplit(url)
        _require(_allowed(url) and p.netloc == 'www.ttc.ca' and not p.fragment
                 and p.path.casefold().startswith('/all-public-meetings/'),
                 'TTC search returned an unexpected origin')
        dates = []
        if result.get('Html'):
            rendered = html.fromstring(result['Html'])
            dates = rendered.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," field-contentdate ")]')
        source_date = _text(dates[0]) if len(dates) == 1 else None
        path_matches = f'/{year}/' in p.path
        _require(path_matches or (source_date and re.search(r'\b' + str(year) + r'\b', source_date)),
                 'TTC search returned an unexpected year')
        rows.append(dict(id=result['Id'], url=url, source_date=source_date,
                         year_path_matches=path_matches))
    _require(len({r['id'] for r in rows}) == len(rows) == len({r['url'] for r in rows}),
             'Repeated TTC meeting identity in page')
    return data['Count'], rows


def _scan(session, settings, year, board):
    total, first = _page(session.get(_search_url(settings, year, board, 0)).content, year)
    rows = list(first)
    _require(len(first) == min(settings['p'], total), 'Incomplete TTC first search page')
    for offset in range(settings['p'], total, settings['p']):
        count, more = _page(session.get(_search_url(settings, year, board, offset)).content, year)
        _require(count == total and len(more) == min(settings['p'], total - offset),
                 'TTC search changed or clamped during pagination')
        rows.extend(more)
    _require(len(rows) == len({r['id'] for r in rows}) == len({r['url'] for r in rows}) == total,
             'Incomplete/repeated TTC search pagination')
    return total, rows, first


def _topic(heading):
    if re.search(r'award acknowledgement|recognition|long service|employee awards', heading, re.I):
        return 'recognition'
    if re.search(r'amendment|settlement|renewal|contract extension|lease extension|extension option', heading, re.I):
        return 'procurement_change'
    if re.search(r'procurement|contract|tender|purchase|supply of|lease', heading, re.I):
        return 'procurement_candidate'
    return 'other'


def _meeting(raw, year):
    tree = html.fromstring(raw)
    main = tree.xpath('//main')
    _require(len(main) == 1, 'Missing TTC meeting main content')
    title = main[0].xpath('.//h1')
    _require(len(title) == 1, 'Unexpected TTC meeting year/title')
    heading = _text(title[0])
    if re.search(r'\b' + str(year) + r'\b', heading):
        return main[0], heading, None
    # The archive includes cancelled/rescheduled placeholders whose h1 is just
    # "Board Meeting". Only an explicit source date and disposition establish
    # their scope; neither their URL nor absence of PDFs proves a cancellation.
    _require(heading == 'Board Meeting', 'Unexpected TTC meeting year/title')
    fields = {}
    for label in main[0].xpath('.//strong'):
        name = _text(label)
        if name in ('Date:', 'Start Time:'):
            _require(name not in fields, 'Ambiguous TTC meeting notice fields')
            fields[name] = (label.tail or '').strip()
    try:
        date = datetime.strptime(fields.get('Date:', ''), '%A, %B %d, %Y')
    except ValueError:
        raise ValueError('Unexpected TTC meeting notice date') from None
    status = fields.get('Start Time:', '')
    match = re.fullmatch(r'(Cancelled|Re-?scheduled)(?: to .+)?', status, re.I)
    _require(date.year == year and match, 'Unexpected TTC meeting year/title')
    disposition = 'cancelled' if match[1].casefold() == 'cancelled' else 'rescheduled'
    return main[0], heading, dict(published_date=fields['Date:'],
        published_start_time=status, disposition=disposition)


def _links(raw, meeting, year):
    main, title, _ = _meeting(raw, year)
    records = []
    for anchor in main.xpath('.//a[@href]'):
        href = urljoin(meeting['url'], anchor.get('href'))
        p = urlsplit(href)
        if not p.path.lower().endswith('.pdf'):
            continue
        url = urlunsplit((p.scheme, p.netloc, p.path, p.query, ''))
        headers = anchor.xpath('ancestor::*[self::h2 or self::h3 or self::h4 or self::h5][1]')
        if not headers:
            headers = anchor.xpath('preceding::*[self::h2 or self::h3 or self::h4 or self::h5][1]')
        heading = _text(headers[0]) if headers else ''
        label = _text(anchor)
        low = label.casefold()
        role = ('decision' if 'decision' in low else 'report' if 'staff report' in low else
                'minutes' if 'minutes' in low else 'agenda' if 'agenda' in low else 'document')
        records.append(dict(meeting_id=meeting['id'], meeting_url=meeting['url'],
            meeting_title=title, heading=heading, label=label, href=href,
            url=url, role=role, topic=_topic(heading), allowed_origin=_allowed(url)))
    return records


def _inventory(observations):
    grouped = {}
    for row in observations:
        item = grouped.setdefault(row['url'], dict(url=row['url'], roles=[], topics=[], observations=[],
                                                   allowed_origin=row['allowed_origin']))
        item['observations'].append(row)
        for target, key in (('roles', 'role'), ('topics', 'topic')):
            if row[key] not in item[target]:
                item[target].append(row[key])
    return list(grouped.values())


def _valid(held):
    if not held:
        return False
    path = Path(held['path'])
    return path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == held['sha256']


def _hold(root, content, suffix):
    digest = hashlib.sha256(content).hexdigest()
    path = root / (digest + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return dict(sha256=digest, path=str(path))


def _kind(item):
    roles = set(item['roles']) - {'document'}
    return KINDS[next(iter(roles))] if len(roles) == 1 else KINDS['document']


def _year(conn, year, root, session, settings, report, *, resume, download, clock, log):
    directory = root / str(year)
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = directory / 'checkpoint.json'
    state = json.loads(checkpoint.read_text()) if resume and checkpoint.exists() else dict(version=1, year=year, meetings={}, documents={})
    _require(state.get('version') == 1 and state.get('year') == year, 'Unexpected TTC checkpoint scope/version')
    save = lambda: _write(checkpoint, state)
    total, all_rows, all_first = _scan(session, settings, year, False)
    advertised, board, board_first = _scan(session, settings, year, True)
    prefix = '/all-public-meetings/board/'
    subset = [r for r in all_rows if urlsplit(r['url']).path.casefold().startswith(prefix)]
    _require(all(urlsplit(r['url']).path.casefold().startswith(prefix) for r in board)
             and {r['id'] for r in subset} == {r['id'] for r in board}, 'TTC year and Board filters disagree')
    for board_filter, count, first in ((False, total, all_first), (True, advertised, board_first)):
        fresh_count, fresh = _page(session.get(_search_url(settings, year, board_filter, 0)).content, year)
        _require(fresh_count == count and fresh == first, 'TTC live count/first-page anchor changed')
    report['discovery'].update(advertised=advertised, captured=len(board), all_types_captured=total,
        source_discrepancies=[r for r in all_rows if not r['year_path_matches']],
        fresh_population_verified_at=clock().isoformat(), snapshot_caveat='No transactional publisher snapshot')
    _write(directory / 'census.json', dict(report['discovery'], meetings=board))
    observations = []
    report['discovery']['meeting_notices'] = []
    for meeting in board:
        url = meeting['url']
        try:
            held = state['meetings'].get(url) if resume else None
            if _valid(held):
                raw = Path(held['path']).read_bytes()
            else:
                raw = session.get(url).content
                held = _hold(root / 'pages', raw, '.html')
                held['captured_at'] = clock().isoformat()
            _, _, notice = _meeting(raw, year)
            links = _links(raw, meeting, year)
            if notice:
                report['discovery']['meeting_notices'].append(dict(url=url, **notice))
            state['meetings'][url] = held
            observations.extend(links)
            save()
            log(f'{year}: meeting {len(state["meetings"])}, {len(links)} published PDF links: {url}')
        except (ValueError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code != 404:
                raise
            report['failures'].append(dict(phase='meeting', url=url, error=str(exc)))
    report['discovery']['complete'] = not report['failures']
    inventory = _inventory(observations)
    _write(directory / 'inventory.json', inventory)
    docs = report['documents']
    docs.update(observations=len(observations), discovered=len(inventory))
    if not download:
        return
    for item in inventory:
        url = item['url']
        if not item['allowed_origin']:
            report['failures'].append(dict(phase='document', url=url, error='Unapproved PDF origin; link retained'))
            continue
        existing = conn.execute('SELECT kind,sha256 FROM background_pdf WHERE url=?', (url,)).fetchone()
        kind = existing['kind'] if existing else _kind(item)
        db.upsert_row(conn, BackgroundPdf(url=url, kind=kind), overwrite=False)
        conn.commit()
        try:
            held = state['documents'].get(url) if resume else None
            if not _valid(held):
                content = session.get(url).content
                _require(content.startswith(b'%PDF-') and b'%%EOF' in content[-4096:], 'TTC response is not a complete PDF')
                held = _hold(root / 'documents', content, '.pdf')
                held['captured_at'] = clock().isoformat()
            proc = subprocess.run(['pdftotext', '-layout', held['path'], '-'], capture_output=True,
                                  text=True, timeout=60, check=True)
            text = proc.stdout.strip()
            held['text_chars'] = len(text)
            state['documents'][url] = held
            save()
            if existing and existing['kind'] not in KINDS.values():
                if existing['sha256'] != held['sha256']:
                    docs['reviewed_content_changes'].append(url)
            else:
                db.upsert_row(conn, BackgroundPdf(url=url, kind=kind, local_path=held['path'],
                              sha256=held['sha256'], text=text or None), overwrite=True)
                # Archive-owned text belongs to these exact bytes. The shared
                # COALESCE upsert cannot clear an older version's text on its own.
                conn.execute('UPDATE background_pdf SET text=? WHERE url=?', (text or None, url))
                conn.commit()
            docs['held'] += 1
            if not text:
                docs['textless'].append(url)
            log(f'{year}: held PDF {docs["held"]}/{len(inventory)}: {url}')
        except (ValueError, subprocess.SubprocessError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code != 404:
                raise
            report['failures'].append(dict(phase='document', url=url, error=str(exc)))
    docs['pending'] = len(inventory) - docs['held']


def capture_history(conn, years, *, resume=False, download=True, max_requests=1000,
                    client=None, data_dir=None, sleep=time.sleep,
                    clock=lambda: datetime.now(timezone.utc), log=lambda _: None):
    """Archive explicit years; resume hash-checks held bytes and rechecks live discovery.

    Returns scope and failures. Nothing here queues model work or asserts award facts.
    Capture all linked PDFs, including decisions, amendments and recognition context;
    no title classifier is allowed to erase the discovery inventory.
    """
    _require(years and all(type(y) is int and 2000 <= y <= 2024 for y in years),
             'TTC history years must be within the published 2000–2024 archive')
    _require(type(max_requests) is int and max_requests > 0, 'max_requests must be positive')
    years = sorted(set(years))
    root = Path(data_dir or config.DATA_DIR) / 'agencies' / 'ttc_history'
    # Match other TTC readers' parent directory so the shared host lock and
    # Retry-After deadline serialize traffic across TTC capture commands.
    session = PublicSession(root, client, sleep=sleep, clock=clock,
                            max_requests=max_requests, allowed_hosts=HOSTS)
    result = dict(scope='meeting and PDF capture' if download else 'meeting and link discovery',
        started_at=clock().isoformat(), years={}, failures=[], complete=False,
        extraction_requested=False, resume=resume, max_requests=max_requests)
    try:
        settings = _configuration(session.get(ARCHIVE_URL).content)
        for year in years:
            report = dict(discovery=dict(complete=False), documents=dict(requested=download,
                held=0, pending=0, textless=[], reviewed_content_changes=[]), failures=[])
            result['years'][str(year)] = report
            try:
                _year(conn, year, root, session, settings, report, resume=resume, download=download, clock=clock, log=log)
            except Exception as exc:
                report['failures'].append(dict(phase='capture', error=str(exc)))
                if not isinstance(exc, ValueError):
                    raise
            finally:
                if download:
                    report['documents']['pending'] = report['documents'].get('discovered', 0) - report['documents']['held']
                report['complete'] = report['discovery']['complete'] and not report['failures']
                _write(root / str(year) / 'capture.json', report)
            result['failures'].extend(dict(year=year, **f) for f in report['failures'])
        result['complete'] = not result['failures'] and all(r['complete'] for r in result['years'].values())
    except Exception as exc:
        result['failures'].append(dict(phase='capture', error=str(exc)))
    finally:
        result.update(requests=session.requests, finished_at=clock().isoformat(),
                      unattempted_years=[y for y in years if str(y) not in result['years']])
        encoded = json.dumps(result, sort_keys=True).encode()
        _write(root / ('capture-' + hashlib.sha256(encoded).hexdigest() + '.json'), result)
        _write(root / 'capture.json', result)
        session.close()
    return result
