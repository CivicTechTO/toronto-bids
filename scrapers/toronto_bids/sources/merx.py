"""Public buyer-scoped MERX capture, including bounded historical searches.

No login, private endpoints, bidding actions or guessed document links. Retain
responses before parsing; listing coverage and detail availability are independent.
"""
import ast
import fcntl
import hashlib
import json
import re
import time
import shutil
import warnings
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from lxml import html

from toronto_bids import config
from toronto_bids.buyers import seed_buyers
from toronto_bids.models import AgencyAward, AgencyBid, PortalListing
from toronto_bids.store import db
from toronto_bids.linking.procurement import _transaction, resolve_procurements
from toronto_bids.sources.bonfire import _record, _retry_after

ORIGIN = 'https://www.merx.com'
USER_AGENT = 'toronto-bids (+https://github.com/CivicTechTO/toronto-bids; public procurement archive)'
SECTIONS = {'open': ('open-bids', 'Open Solicitations'),
            'closed': ('closed-bids', 'Closed Solicitations'),
            'results': ('bidresults-bids', 'Bid Results'),
            'awarded': ('awarded-bids', 'Awarded Solicitations')}


def _class(name):
    return 'contains(concat(" ",normalize-space(@class)," ")," ' + name + ' ")'


def _text(element):
    return ' '.join(element.text_content().split())


def _tree(content):
    tree = html.fromstring(content)
    for e in tree.xpath('//script|//style'):
        e.drop_tree()
    text = _text(tree)
    if 'An action is required before access to this content is granted' in text:
        raise PermissionError('MERX detail requires account entitlement; listing retained')
    return tree


def _url(value):
    url = urljoin(ORIGIN, value)
    p = urlsplit(url)
    if p.scheme != 'https' or p.netloc != 'www.merx.com' or p.fragment:
        raise ValueError('MERX unexpected external/source URL')
    return url


def notice_identity(href):
    """Stable public notice ID, independent of slug, tab and tracking parameters."""
    path = urlsplit(_url(href)).path
    modern = re.fullmatch(r'/(?:(?:[^/]+/){0,2})solicitations/(?:[^/]+/)*?(\d{10})', path)
    legacy = re.fullmatch(r'/public/supplier/interception/view-notice/(\d+)', path)
    if not (modern or legacy):
        raise ValueError('MERX unfamiliar public notice route: ' + path)
    return (modern or legacy)[1]


def _date(raw):
    if not re.fullmatch(r'\d{4}/\d{2}/\d{2}', raw):
        raise ValueError('MERX invalid date-only value: ' + raw)
    return datetime.strptime(raw, '%Y/%m/%d').date().isoformat()


def parse_page(content, portal, section, buyer_id):
    """Parse only real listing anchors; hidden empty templates are not records."""
    tree = _tree(content)
    title = tree.xpath('//title/text()')
    if len(title) != 1 or portal['body'] not in title[0]:
        raise ValueError('MERX unexpected buyer page')
    label = SECTIONS[section][1]
    # The count is the visible heading, not the navigation links or empty template.
    text = _text(tree)
    matches = re.findall(r'(?<!\d)([\d,]+)\s+' + re.escape(label[:-1]) + r's?\b', text)
    if len(set(matches)) != 1:
        raise ValueError('MERX missing/ambiguous listing total for ' + section)
    total = int(matches[0].replace(',', ''))
    selected = tree.xpath('//option[@data-page-number and @selected]/@data-page-number')
    pages = tree.xpath('//option[@data-page-number]/@data-page-number')
    page = int(selected[0]) if len(selected) == 1 else 1 if not pages else None
    if page is None:
        raise ValueError('MERX missing selected page')
    listings, links = {}, {}
    for anchor in tree.xpath('//a[' + _class('solicitation-link') + ']'):
        nid = notice_identity(anchor.get('href', ''))
        if nid in listings:
            raise ValueError('MERX duplicate notice within page')
        row = anchor.xpath('ancestor::tr[1]')
        if len(row) != 1 or not _text(anchor):
            raise ValueError('MERX malformed listing row')
        buyer = row[0].xpath('.//*[' + _class('sol-buyer-name') + ']')
        if ((portal.get('listing_buyer_label', True) and len(buyer) != 1)
                or (buyer and (len(buyer) != 1 or not _text(buyer[0]).startswith(portal['body'])))):
            raise ValueError('MERX listing outside configured buyer scope')
        dates = []
        for name in ('sol-publication-date', 'sol-closing-date'):
            fields = row[0].xpath('.//*[' + _class(name) + ']//*[' + _class('date-value') + ']')
            if not fields and name == 'sol-closing-date' and section in ('results', 'awarded'):
                dates.append(None)
            elif len(fields) != 1:
                raise ValueError('MERX missing listing date')
            else:
                dates.append(_date(_text(fields[0])))
        # A result/award tab is evidence of tab membership, not an award fact.
        references = row[0].xpath('.//*[' + _class('sol-num') + ']')
        if len(references) > 1:
            raise ValueError('MERX ambiguous printed reference')
        native_ref = _text(references[0]) if references else None
        listings[nid] = PortalListing(buyer_id=buyer_id, portal_url=_url(anchor.get('href')).split('?')[0],
            title=_text(anchor), native_ref=native_ref, status={'open': 'Open', 'closed': 'Closed'}.get(section),
            posted_date=dates[0], closing_date=dates[1], source='merx')
        links[nid] = _url(anchor.get('href'))
    expected = min(25, max(0, total - (page - 1) * 25))
    if len(listings) != expected:
        raise ValueError(f'MERX page {page}: expected {expected} listings, got {len(listings)}')
    return dict(total=total, page=page, listings=listings, links=links)


class PublicSession:
    """Serial five-second access, bounded backoff and persistent server deadlines."""
    def __init__(self, root, client=None, *, sleep=time.sleep,
                 clock=lambda: datetime.now(timezone.utc), max_requests=300, allowed_hosts=("www.merx.com",)):
        self.allowed_hosts = allowed_hosts
        self.root, self.sleep, self.clock = Path(root), sleep, clock
        self.client = client or httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=False,
            headers={'User-Agent': USER_AGENT, 'X-Requested-With': 'XMLHttpRequest'})
        self.owned = client is None
        self.requests, self.max_requests = 0, max_requests
        self.root.mkdir(parents=True, exist_ok=True)

    def get(self, url):
        # All buyers/processes sharing the data directory serialize their MERX
        # traffic, including backfills alongside incremental commands.
        host_key = hashlib.sha256(','.join(self.allowed_hosts).encode()).hexdigest()[:16]
        with (self.root.parent / ('.public-' + host_key + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._get(url, host_key)

    def _get(self, url, host_key):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc not in self.allowed_hosts
                or parsed.fragment or parsed.username or parsed.password):
            raise ValueError('Unexpected public capture origin')
        cooldown = self.root.parent / ('.retry-' + host_key + '.txt')
        if cooldown.exists() and datetime.fromisoformat(cooldown.read_text().strip()) > self.clock():
            raise RuntimeError('MERX deferred by held Retry-After deadline')
        for attempt in range(4):
            if self.requests >= self.max_requests:
                raise RuntimeError('MERX request budget exhausted; capture is partial')
            self.sleep(5.2)
            self.requests += 1
            try:
                response = self.client.get(url, headers={'User-Agent': USER_AGENT})
            except httpx.TransportError:
                if attempt == 3:
                    raise
                self.sleep(2 ** (attempt + 1))
                continue
            _record(response, self.root, self.clock())
            if response.status_code == 429 or 500 <= response.status_code < 600:
                delay = max(2 ** (attempt + 1), _retry_after(response.headers.get('Retry-After'), self.clock()))
                cooldown.write_text((self.clock() + timedelta(seconds=delay)).isoformat())
                if delay > 60:
                    raise RuntimeError('MERX retry deferred by server deadline')
                if attempt == 3:
                    response.raise_for_status()
                self.sleep(delay)
                continue
            response.raise_for_status()  # redirects/denials are not empty datasets
            return response
        raise AssertionError('Unreachable retry state')

    def close(self):
        if self.owned:
            self.client.close()


def _fields(tree):
    values = {}
    for element in tree.xpath('.//*[' + _class('mets-field') + ']'):
        label = element.xpath('./*[' + _class('mets-field-label') + ']')
        body = element.xpath('./*[' + _class('mets-field-body') + ']')
        if len(label) == len(body) == 1:
            name, value = _text(label[0]), _text(body[0])
            if not name:
                continue  # unlabeled contact/address fields are not named notice metadata
            if name in values:
                raise ValueError('MERX repeated field: ' + name)
            values[name] = value
    return values


def fragment(content, tab):
    """Decode the UI's literal HTML payload; never execute source JavaScript."""
    matches = re.findall(r'\$\("#innerTabContent"\)\.html\((\'(?:\\.|[^\'\\])*\')\);', content.decode())
    if len(matches) != 1:
        raise ValueError('MERX unfamiliar public fragment envelope')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        body = ast.literal_eval(matches[0]).replace('\\/', '/')
    tree = _tree(body)
    if tree.xpath('//input[@id="currentTabId"]/@value') != [tab]:
        raise ValueError('MERX fragment does not identify requested tab')
    return tree


def parse_bids(content, buyer_id, page_url, source_url):
    tree = fragment(content, 'bid-results')
    stage_fields = tree.xpath('//*[' + _class('mets-field') + '][./*[' + _class('mets-field-label') + ' and normalize-space(.)="Publication Type"]]')
    if len(stage_fields) != 1:
        raise ValueError('MERX missing bid publication type')
    stage = _fields(stage_fields[0].getparent()).get('Publication Type')
    if stage not in ('Unofficial Results', 'Official Results'):
        raise ValueError('MERX unfamiliar bid publication type')
    rows = []
    for item in tree.xpath('//*[' + _class('bidResultItems') + ']'):
        names = item.xpath('./h3')
        fields = _fields(item)
        if len(names) != 1 or not _text(names[0]) or not fields.get('Bid Amount'):
            raise ValueError('MERX malformed bidder/amount binding')
        price = fields['Bid Amount']
        if price != 'No Bid' and not re.fullmatch(r'\$[\d,]+\.\d{2}', price):
            raise ValueError('MERX unfamiliar bid amount; retained for review')
        rows.append(AgencyBid(buyer_id=buyer_id, native_ref=page_url,
            bidder_name_raw=_text(names[0]), bid_price=None if price == 'No Bid' else price,
            status='No Bid' if price == 'No Bid' else None, result_stage=stage,
            price_header='Bid Amount', report_url=source_url, source='merx'))
    if not rows or len({r.bidder_name_raw for r in rows}) != len(rows):
        raise ValueError('MERX empty/repeated bidder collection')
    return rows


def parse_awards(content, buyer_id, page_url, source_url, notice_id):
    tree = fragment(content, 'award')
    refs = tree.xpath('//*[' + _class('mets-field') + '][./*[' + _class('mets-field-label') + ' and normalize-space(.)="Reference Number"]]')
    if len(refs) != 1 or _fields(refs[0].getparent()).get('Reference Number') != notice_id:
        raise ValueError('MERX award reference differs from listing')
    publication = tree.xpath('//*[' + _class('mets-field') + '][./*[' + _class('mets-field-label') + ' and normalize-space(.)="Publication Date"]]')
    if len(publication) != 1:
        raise ValueError('MERX missing award publication date')
    published = _date(_fields(publication[0].getparent())['Publication Date'])
    rows = []
    for item in tree.xpath('//*[' + _class('awardeeItem') + ']'):
        fields = _fields(item)
        if not all(fields.get(k) for k in ('Supplier Awarded', 'Awarded Value', 'Award Date')):
            raise ValueError('MERX missing explicit award fields')
        date = fields['Award Date']
        # Preserve only day precision; do not invent a UTC time from EST/EDT text.
        if not re.fullmatch(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} [AP]M E[DS]T', date):
            raise ValueError('MERX unfamiliar award date')
        rows.append(AgencyAward(buyer_id=buyer_id, native_ref=page_url,
            supplier_name_raw=fields['Supplier Awarded'], award_amount=fields['Awarded Value'],
            award_date=_date(date[:10]), publication_date=published, report_url=source_url, source='merx'))
    if not rows:
        raise ValueError('MERX no explicit awardee collection')
    return rows


def capture_details(conn, session, listing, detail_url, notice_id, *, fetch=None):
    """Only tabs advertised by this notice's public detail, never guessed tab IDs."""
    # Legacy published links lead to an account gate; preserve the known gap without
    # repeatedly requesting it. Modern public listing routes are fetched directly.
    if '/public/supplier/interception/' in urlsplit(detail_url).path:
        raise PermissionError('legacy detail entitlement unverified; public listing retained')
    fetch = fetch or (lambda url: session.get(url).content)
    tree = _tree(fetch(detail_url))
    fields = _fields(tree)
    if fields.get('Reference Number') != notice_id:
        raise ValueError('MERX detail reference differs from listing')
    native_ref = fields.get('Solicitation Number')
    rows = []
    for tab in ('bid-results', 'award'):
        paths = set(tree.xpath('//*[@data-ajax-url]/@data-ajax-url'))
        paths = {p for p in paths if re.fullmatch(r'/public/solicitations/\d+/abstract/' + tab, p)}
        if len(paths) > 1:
            raise ValueError('MERX ambiguous public tab route')
        if paths:
            url = _url(paths.pop())
            raw = fetch(url)
            parser = parse_bids if tab == 'bid-results' else parse_awards
            args = (raw, listing.buyer_id, listing.portal_url, url)
            rows.extend(parser(*args) if tab == 'bid-results' else parser(*args, notice_id))
    with _transaction(conn):
        if native_ref:
            db.upsert_row(conn, replace(listing, native_ref=native_ref), overwrite=True)
        for row in rows:
            db.upsert_row(conn, row, overwrite=True)
        resolve_procurements(conn)
    conn.commit()
    return len(rows)


def capture_portal(conn, portal, *, backfill=False, resume=False, details=False, sections=None,
                   queries=(), keyword=None, max_requests=300, client=None, data_dir=None,
                   sleep=time.sleep, clock=lambda: datetime.now(timezone.utc), log=lambda m: None):
    """Capture useful public records even when full historical coverage is unproven.

    Full-mode reconciliation uses this run's union only, never accumulated DB counts.
    Query matches may overlap; neither prefix syntax nor disjointness is assumed.
    """
    if not portal.get('enabled') or not portal.get('permission'):
        raise PermissionError('MERX capture requires recorded permission and enabled buyer')
    if not re.fullmatch(r'https://www\.merx\.com/[a-z0-9]+', portal['portal_url']):
        raise ValueError('MERX config must name a buyer profile')
    buyer_id = seed_buyers(conn)[portal['slug']]
    root = Path(data_dir or config.DATA_DIR) / 'agencies' / 'merx' / portal['slug']
    session = PublicSession(root, client, sleep=sleep, clock=clock, max_requests=max_requests)
    cache = root / 'backfill-pages'
    if backfill and not resume and cache.exists():
        shutil.rmtree(cache)  # pointers only; immutable held responses remain archived
    cache.mkdir(exist_ok=True)
    def fetch_page(url):
        pointer = cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        if backfill and resume and pointer.exists():
            held = json.loads(pointer.read_text())
            if held['url'] != url or not re.fullmatch('[a-f0-9]{64}', held['sha256']):
                raise ValueError('MERX invalid resume pointer')
            data = (root / (held['sha256'] + '.bin')).read_bytes()
            if hashlib.sha256(data).hexdigest() != held['sha256']:
                raise ValueError('MERX held page failed hash verification')
            return data
        response = session.get(url)
        if backfill:
            pointer.write_text(json.dumps(dict(url=url, sha256=hashlib.sha256(response.content).hexdigest())))
        return response.content
    report = dict(buyer=portal['slug'], mode='backfill' if backfill else 'incremental',
                  started_at=clock().isoformat(), keyword=keyword, details_requested=details, sections={}, failures=[], detail_gaps=[], detail_facts=0)
    all_listings, all_links = {}, {}
    def query(section, keyword='', direction='DESC', *, window=False):
        base = portal['portal_url'] + '/solicitations/' + SECTIONS[section][0]
        rows, links, total = {}, {}, None
        for number in range(1, 41):
            params = dict(selectedContent='BUYER', sortBy='publicationDate', sortDirection=direction, pageNumber=number)
            if keyword:
                params['keywords'] = keyword
            content = fetch_page(base + '?' + urlencode(params))
            page = parse_page(content, portal, section, buyer_id)
            if page['page'] != number or rows.keys() & page['listings'].keys():
                raise ValueError('MERX repeated/clamped page; incomplete query')
            if total is not None and page['total'] != total:
                raise ValueError('MERX changing query total; incomplete query')
            total = page['total']
            for nid, listing in page['listings'].items():
                held = conn.execute('SELECT portal_url FROM merx_notice WHERE buyer_id=? AND notice_id=?',
                                    (buyer_id, nid)).fetchone()
                if held:
                    page['listings'][nid] = replace(listing, portal_url=held['portal_url'])
            rows.update(page['listings']); links.update(page['links'])
            if len(rows) >= total or (not backfill and section != 'open'):
                break
        # Every validated query/window may contribute useful records, even if parent
        # coverage is incomplete. Nothing is deleted for absence from a search.
        with _transaction(conn):
            for nid, row in rows.items():
                conn.execute('INSERT OR IGNORE INTO merx_notice VALUES(?,?,?)',
                             (buyer_id, nid, row.portal_url))
                db.upsert_row(conn, row, overwrite=True)
            resolve_procurements(conn)
        conn.commit()
        all_listings.update(rows); all_links.update(links)
        if not window and backfill and len(rows) != total:
            report['failures'].append(f'{section}: query {keyword!r} exceeds accessible result window ({len(rows)}/{total})')
        return total, rows
    network_stopped = False
    try:
        for section in sections or SECTIONS:
            try:
                total, rows = query(section, keyword or '', window=True)
                union = set(rows)
                if backfill and len(union) < total:
                    _, older = query(section, keyword or '', direction='ASC', window=True)
                    union.update(older)
                    # These are positive keyword searches within the SAME buyer profile.
                    # Prefixes are observed labels; they are not presumed exhaustive.
                    codes = set(queries) if not keyword else set()
                    for row in [*rows.values(), *older.values()]:
                        if not keyword:
                            codes.update(re.findall(r'\b[A-Z]\d{2}(?=[A-Z])', row.title or ''))
                    for term in sorted(codes):
                        if len(union) == total:
                            break
                        _, part = query(section, term)
                        union.update(part)
                        log(f'{section}: query {term}: {len(part)}; {len(union)}/{total} unique')
                complete = len(union) == total
                if backfill:
                    base = portal['portal_url'] + '/solicitations/' + SECTIONS[section][0]
                    params = dict(selectedContent='BUYER', sortBy='publicationDate', sortDirection='DESC', pageNumber=1)
                    if keyword:
                        params['keywords'] = keyword
                    raw = session.get(base + '?' + urlencode(params))
                    final = parse_page(raw.content, portal, section, buyer_id)
                    final_total = final['total']
                    if list(final['listings']) != list(rows)[:25]:
                        complete = False
                    if final_total != total:
                        complete = False
                    if not complete:
                        report['failures'].append(f'{section}: incomplete history: {len(union)} unique / {total} initial advertised; final total {final_total}')
                report['sections'][section] = dict(advertised=total, captured=len(union),
                    complete=complete, scope=('query history' if keyword else 'full history') if backfill else 'recent window')
                log(f'{section}: {len(union)}/{total} unique ({report["sections"][section]["scope"]})')
            except Exception as exc:
                report['failures'].append(f'{section}: {exc}')
                # Stop the entire buyer on server/access problems or request budget.
                if not isinstance(exc, ValueError):
                    network_stopped = True
                    break
        if details and not network_stopped:
            for nid, listing in all_listings.items():
                try:
                    report['detail_facts'] += capture_details(conn, session, listing, all_links[nid], nid,
                        fetch=fetch_page if backfill else None)
                except PermissionError as exc:
                    report['detail_gaps'].append(dict(notice_id=nid, reason=str(exc)))
                except Exception as exc:
                    report['failures'].append(f'detail:{nid}: {exc}')
                    if not isinstance(exc, ValueError):
                        break
    finally:
        report['requests'] = session.requests
        report['finished_at'] = clock().isoformat()
        report['listings'] = len(all_listings)
        report['complete'] = not report['failures'] and not report['detail_gaps'] and all(
            s['complete'] for s in report['sections'].values())
        encoded = json.dumps(report, sort_keys=True, indent=2) + '\n'
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        (root / ('capture-' + digest + '.json')).write_text(encoded)
        (root / 'capture.json').write_text(encoded)
        conn.execute('INSERT INTO merx_capture(buyer_id,captured_at,mode,complete,report_json) VALUES(?,?,?,?,?)',
            (buyer_id, report['finished_at'], report['mode'], int(report['complete']), encoded))
        conn.commit()
        session.close()
    return report


def run_merx_capture(conn, *, log=lambda m: None):
    """Nightly incremental capture; isolate buyers and return every coverage failure.

    Historical backfills remain separate. Routine capture includes the public
    detail tabs so bid/award evidence survives after notices close.
    """
    results = {}
    for portal in config.MERX_PORTALS:
        if not portal['enabled']:
            continue
        try:
            results[portal['slug']] = capture_portal(conn, portal, details=True, log=log)
        except Exception as exc:
            results[portal['slug']] = dict(listings=0, detail_facts=0, detail_gaps=[],
                failures=[str(exc)])
            log(f"FAILED merx:{portal['slug']}: {exc}")
    return results
