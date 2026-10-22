"""Anonymous Bonfire listing metadata (#110, #134).

Follow the public portal's Open/Past JSON requests; retain replies before parsing,
validate the complete capture, and archive by buyer/page rather than printed ref.
"""
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from lxml import html

from toronto_bids import config
from toronto_bids.buyers import seed_buyers
from toronto_bids.models import PortalListing
from toronto_bids.store import db

USER_AGENT = 'toronto-bids (+https://github.com/CivicTechTO/toronto-bids)'
SECTIONS = {'open': '/PublicPortal/getOpenPublicOpportunitiesSectionData',
            'past': '/PublicPortal/getPastPublicOpportunitiesSectionData'}


def _record(response, root, now):
    """Store original bytes and request provenance, including empty/error replies."""
    root.mkdir(parents=True, exist_ok=True)
    content = response.content
    digest = hashlib.sha256(content).hexdigest()
    path = root / (digest + '.bin')
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Bonfire held response failed hash verification')
    else:
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as held:
            temporary = Path(held.name)
            try:
                held.write(content)
                held.close()
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    manifest = dict(url=str(response.request.url), status=response.status_code,
                    content_type=response.headers.get('Content-Type'),
                    retry_after=response.headers.get('Retry-After'), sha256=digest,
                    captured_at=now.isoformat())
    encoded = json.dumps(manifest, sort_keys=True, indent=2)
    key = hashlib.sha256(encoded.encode()).hexdigest()
    (root / (key + '.json')).write_text(encoded + '\n')


def _retry_after(value, now):
    if not value:
        return 0
    if value.strip().isdigit():
        return int(value.strip())
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, (date - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0


def _get(client, url, root, *, sleep, clock, retries=3):
    cooldown = root / 'retry-not-before.txt'
    if cooldown.exists():
        until = datetime.fromisoformat(cooldown.read_text().strip())
        if until.tzinfo is None:
            raise ValueError('Bonfire invalid held retry deadline')
        if until > clock():
            raise RuntimeError('Bonfire retry deferred until ' + until.isoformat())
        cooldown.unlink()
    for attempt in range(retries + 1):
        sleep(1.0)  # Serial requests, including every retry, at least one second apart.
        try:
            response = client.get(url)
        except httpx.TransportError:
            if attempt == retries:
                raise
            sleep(2 ** (attempt + 1))
            continue
        _record(response, root, clock())
        if response.status_code == 429 or 500 <= response.status_code < 600:
            server_wait = _retry_after(response.headers.get('Retry-After'), clock())
            # Preserve server deadlines across command/nightly runs as well as
            # retries inside this process. Never retry a deferred request early.
            if server_wait > 0 and (server_wait > 60 or attempt == retries):
                cooldown.write_text((clock() + timedelta(seconds=server_wait)).isoformat() + '\n')
            if attempt == retries:
                response.raise_for_status()
            pause = max(2 ** (attempt + 1), server_wait)
            if pause > 60:
                raise RuntimeError(f'Bonfire {response.status_code}: retry deferred; Retry-After requires {pause:g}s')
            sleep(pause)
            continue
        # Redirects/login responses are failures, not empty datasets. No account access.
        response.raise_for_status()
        return response
    raise AssertionError('Unreachable retry state')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Bonfire duplicate JSON key: ' + key)
        result[key] = value
    return result


def _section(response):
    data = json.loads(response.content, object_pairs_hook=_unique_object)
    success = data.get('success') if isinstance(data, dict) else None
    if not (success is True or (type(success) is int and success == 1)):
        raise ValueError('Bonfire listing request did not report success=true/1')
    if set(data) - {'success', 'message', 'payload'}:
        raise ValueError('Bonfire unfamiliar response fields; check pagination/schema')
    payload = data.get('payload')
    if not isinstance(payload, dict) or 'projects' not in payload:
        raise ValueError('Bonfire missing projects collection')
    allowed = {'projects', 'departments', 'proposalsForVendorForOrganization',
               'pastScheduledProjectOpenPeriodsWhereNotIsReopen'}
    if set(payload) - allowed:
        raise ValueError('Bonfire unfamiliar collection fields; check pagination/schema')
    projects = payload['projects']
    if isinstance(projects, dict):
        for key, project in projects.items():
            if not isinstance(project, dict) or project.get('ProjectID') != key:
                raise ValueError('Bonfire project map key does not match ProjectID')
        projects = list(projects.values())
    elif not isinstance(projects, list):
        raise ValueError('Bonfire invalid projects collection')
    if any(not isinstance(project, dict) for project in projects):
        raise ValueError('Bonfire invalid project record')
    # This is an anonymous capture; account-specific proposals are outside its scope.
    if payload.get('proposalsForVendorForOrganization'):
        raise ValueError('Bonfire unexpected account-specific proposals')
    return projects, payload


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', value):
        raise ValueError(f'Bonfire invalid UTC close date: {value!r}')
    # The portal passes these fields to BFUtil.createMomentFromUtcDateString.
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)


def _listing(record, payload, base, buyer_id, now):
    pid = record.get('ProjectID')
    if not isinstance(pid, str) or not re.fullmatch(r'[1-9]\d*', pid):
        raise ValueError('Bonfire invalid/missing ProjectID')
    if record.get('ProjectVisibilityID') != '1':
        raise ValueError(f'Bonfire {pid}: refusing non-public project visibility')
    title = record.get('ProjectName')
    reference = record.get('ReferenceID')
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f'Bonfire {pid}: missing project title')
    if reference is not None and not isinstance(reference, str):
        raise ValueError(f'Bonfire {pid}: invalid printed reference')
    state, substate = record.get('ProjectStatusID'), record.get('ProjectSubStatusID')
    if state not in ('1', '2', '3', '4', '5', '6', '7') or substate not in ('1', '2', '3'):
        raise ValueError(f'Bonfire {pid}: unfamiliar project status {state!r}/{substate!r}')
    public_award = record.get('IsPublicAward', False)
    if type(public_award) is not bool:
        raise ValueError(f'Bonfire {pid}: invalid public-award indicator')
    close = _date(record.get('DateClose'))
    if state == '6':
        # Match the UI's anonymous view of a reopened event: use its original close.
        originals = payload.get('pastScheduledProjectOpenPeriodsWhereNotIsReopen')
        if not isinstance(originals, list):
            raise ValueError(f'Bonfire {pid}: missing original close for reopened project')
        matches = [r for r in originals if isinstance(r, dict) and r.get('ProjectID') == pid]
        if len(matches) != 1:
            raise ValueError(f'Bonfire {pid}: ambiguous original close for reopened project')
        close = _date(matches[0].get('DateClose'))
    # Labels follow the captured public UI, not the internal workflow status name.
    status = 'Open' if state == '2' and close > now else 'Closed'
    if status == 'Closed' and public_award and state in ('4', '5'):
        status = 'Awarded'
    if substate == '2':
        status = 'Cancelled'
    return PortalListing(buyer_id=buyer_id, portal_url=f'{base}/opportunities/{pid}',
                         native_ref=(reference.strip() or None) if reference is not None else None,
                         title=title, status=status, posted_date=None,
                         closing_date=close.isoformat(), source='bonfire')


def capture_portal(conn, portal, *, client=None, data_dir=None, sleep=time.sleep,
                   clock=lambda: datetime.now(timezone.utc)):
    """Capture one enabled public portal atomically; retained evidence survives failure.

    Three ordinary GETs on success: landing page and complete Open/Past collections.
    No per-project requests, authentication, documents or inferred award/bid facts.
    """
    if not portal.get('enabled') or not portal.get('permission'):
        raise PermissionError('Bonfire capture requires an enabled portal with recorded permission')
    base = portal['portal_url'].rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port
            or parsed.path or parsed.query or parsed.fragment
            or not re.fullmatch(r'[a-z0-9-]+\.bonfirehub\.(ca|com)', parsed.netloc)):
        raise ValueError('Bonfire config must name an HTTPS portal origin')
    root = Path(data_dir or config.DATA_DIR) / 'agencies' / 'bonfire' / portal['slug']
    owned = client is None
    client = client or httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=False,
                                   headers={'User-Agent': USER_AGENT, 'X-Requested-With': 'XMLHttpRequest'})
    try:
        landing = _get(client, base + '/portal/?tab=openOpportunities', root, sleep=sleep, clock=clock)
        doc = html.fromstring(landing.content)
        titles = doc.xpath('//title/text()')
        if len(titles) != 1 or not titles[0].strip().endswith(' - ' + portal['body']):
            raise ValueError('Bonfire unexpected portal/buyer page')
        for section, endpoint in SECTIONS.items():
            if endpoint not in landing.text:
                raise ValueError('Bonfire public listing endpoint missing from page: ' + section)
        # Hold and validate both responses before writing any normalized listings.
        batches = []
        for section, endpoint in SECTIONS.items():
            response = _get(client, base + endpoint, root, sleep=sleep, clock=clock)
            projects, payload = _section(response)
            batches.append((projects, payload))
        buyer_ids = seed_buyers(conn)
        buyer_id = buyer_ids[portal['slug']]
        observed_at = clock()
        by_url = {}
        for projects, payload in batches:
            seen = set()
            for record in projects:
                listing = _listing(record, payload, base, buyer_id, observed_at)
                if listing.portal_url in seen:
                    raise ValueError('Bonfire repeated project within one collection')
                seen.add(listing.portal_url)
                if listing.portal_url in by_url and by_url[listing.portal_url] != listing:
                    raise ValueError('Bonfire project changed between Open and Past collections')
                by_url[listing.portal_url] = listing
        from toronto_bids.linking.procurement import _transaction, resolve_procurements
        with _transaction(conn):
            for listing in by_url.values():
                db.upsert_row(conn, listing, overwrite=True)
            resolve_procurements(conn)
        conn.commit()
        return len(by_url)
    finally:
        if owned:
            client.close()


def run_bonfire_capture(conn, *, only=None, log=lambda _m: None):
    """Per-buyer isolation for standalone/nightly; failures must reach their caller."""
    results = {}
    for portal in config.BONFIRE_PORTALS:
        if not portal['enabled'] or (only and portal['slug'] not in only):
            continue
        try:
            results[portal['slug']] = capture_portal(conn, portal)
            log(f"  {portal['slug']}: {results[portal['slug']]} Bonfire listings stored")
        except Exception as exc:
            results[portal['slug']] = f'FAILED: {exc}'
            log(f"  FAILED {portal['slug']}: {exc}")
    return results
