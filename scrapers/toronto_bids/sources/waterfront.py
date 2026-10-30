"""Waterfront's public monthly award publication (#108).

HTML structure supplies source blocks and month precision; the shared extraction
client supplies supplier/value relationships. Evidence checks never infer those roles.
"""
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from lxml import html

from toronto_bids import config
from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION, ExtractionClient
from toronto_bids.store import db

URL = 'https://www.waterfrontoronto.ca/opportunities/procurement/awarded-contracts'
MONTHS = {name: i for i, name in enumerate(('January', 'February', 'March', 'April',
    'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'), 1)}
# Source qualifiers, not supplier/title parsing. Keep their exact text on the block.
ANNOTATION = re.compile(r'\([^()]*(?:competitive|copetitive|sole.?source|vendor of record|awarded by)[^()]*\)', re.I)
NUMBER = re.compile(r'\$?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?')


def _clean(text):
    return ' '.join(text.split())


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _events(element):
    buf = []

    def flush():
        text = _clean(''.join(buf))
        buf.clear()
        return [('text', text)] if text else []

    def walk(node):
        if not isinstance(node.tag, str):
            return
        tag = node.tag.lower()
        if tag in ('h3', 'h4'):
            yield from flush()
            text = _clean(node.text_content())
            if text:
                yield 'heading', text
            return
        if tag in ('p', 'li', 'ul', 'ol', 'div', 'br'):
            yield from flush()
        if node.text:
            buf.append(node.text)
        for child in node:
            yield from walk(child)
            if child.tail:
                buf.append(child.tail)
        if tag in ('p', 'li', 'ul', 'ol', 'div', 'br'):
            yield from flush()

    yield from walk(element)
    yield from flush()


def parse_publication(content):
    """Return every visible body block, or refuse an unfamiliar/lossy layout."""
    doc = html.fromstring(content)
    headings = [n for n in doc.xpath('//h2') if _clean(n.text_content()) == 'List of Awarded Contracts']
    if len(headings) != 1:
        raise ValueError('Expected one awarded-contracts heading')
    panels = headings[0].getparent().xpath('.//dl/div')
    if not panels:
        raise ValueError('No award year panels')
    rows, years, periods = [], set(), set()
    occurrences = Counter()
    for panel in panels:
        dt = panel.xpath('./dt')
        match = re.fullmatch(r'(\d{4}) Awarded Contracts', _clean(dt[0].text_content())) if len(dt) == 1 else None
        if not match or match[1] in years:
            raise ValueError('Unknown or repeated award year panel')
        year = match[1]
        years.add(year)
        bodies = panel.xpath('.//dd//div[contains(concat(" ",normalize-space(@class)," ")," field-content ")]')
        if len(bodies) != 1:
            raise ValueError('Expected one year body')
        events = list(_events(bodies[0]))
        without_space = lambda text: re.sub(r'\s+', '', text)
        if without_space(''.join(t for _, t in events)) != without_space(bodies[0].text_content()):
            raise ValueError('Award body text would be lost')
        month = None
        for kind, text in events:
            if kind == 'heading':
                m = re.fullmatch(r'([A-Za-z]+)(?: (\d{4}))?', text.replace('\u200b', '').strip())
                if not m or m[1] not in MONTHS or (m[2] and m[2] != year):
                    raise ValueError('Unrecognized publication month: ' + text)
                month = f'{year}-{MONTHS[m[1]]:02d}'
                if month in periods:
                    raise ValueError('Repeated publication month: ' + month)
                periods.add(month)
            else:
                if month is None:
                    raise ValueError('Award body precedes publication month')
                occurrences[month, text] += 1
                key = _sha(json.dumps([month, text, occurrences[month, text]], ensure_ascii=False))
                rows.append(dict(source_key=key, publication_month=month, raw_text=text,
                                 source_position=len(rows), labels=json.dumps(ANNOTATION.findall(text), ensure_ascii=False)))
    if not rows:
        raise ValueError('Award publication contains no text blocks')
    return rows


def extraction_text(month, rows):
    return ('Waterfront Toronto — published awarded contracts.\n'
            'Publication month only (no exact award day published): ' + month + '\n\n'
            + '\n'.join(r['raw_text'] for r in rows))


def _amount_extent(text, amount):
    """Require literal model evidence and retain the surrounding source qualifier."""
    if not amount:
        # A missing price is valid only when the source has none (or says TBD).
        return None if '$' not in text and not re.search(r'\(\s*\d[\d,]+', text) else False
    positions = [m.start() for m in re.finditer(re.escape(amount), text)]
    if len(positions) != 1:
        return False
    start = positions[0]
    for match in re.finditer(r'\([^()]*\)', text):
        # The source determines the extent, including qualifiers, whether the
        # model copied the display parentheses or just their contents.
        if match.start() <= start and start + len(amount) <= match.end():
            return match.group()[1:-1].strip()
    return amount


def verified_claims(rows, result):
    """Map model roles to unambiguous literal source evidence; retain refusals."""
    from toronto_bids.linking.publication_evidence import reviewed_recipients
    month=rows[0].get('publication_month') if rows else None
    reviewed=reviewed_recipients(rows,result,
        extraction_sha256=_sha(extraction_text(month,rows)) if month else None,
        extractor_version=EXTRACTOR_VERSION)
    claims, errors, seen = [], [], set()
    mapped = defaultdict(list)
    nonmonetary = defaultdict(list)
    for contract_index,contract in enumerate(result['contracts']):
        for award_index,award in enumerate(contract.get('awards', [])):
            proof=reviewed.get((contract_index,award_index))
            name = award.get('supplier_name')
            amount = award.get('amount_raw')
            if not isinstance(name, str) or not name.strip() or (amount is not None and not isinstance(amount, str)):
                errors.append('Invalid supplier or amount type')
                continue
            name, amount = _clean(name), _clean(amount) if amount else None
            if proof is not None:name=proof['recipient']
            matches = []
            for row in rows:
                body = ANNOTATION.sub('', row['raw_text'])
                if proof is not None:
                    if row['source_key']!=proof['publication_key']:continue
                elif name not in body:
                    continue
                extent = _amount_extent(body, amount)
                if extent is not False:
                    matches.append((row, extent))
            title = contract.get('title')
            if len(matches) > 1 and isinstance(title, str) and title.strip():
                # The model's literal title may distinguish two published observations
                # with the same supplier/value; it never establishes equivalence.
                titled = [(row, extent) for row, extent in matches if _clean(title) in row['raw_text']]
                if len(titled) == 1:
                    matches = titled
            if len(matches) != 1:
                errors.append(f'Unresolved literal supplier/value evidence: {name!r}, {amount!r} ({len(matches)} blocks)')
                continue
            row, extent = matches[0]
            key = _sha(json.dumps([row['source_key'], name, extent], ensure_ascii=False))
            if key in seen:
                errors.append('Repeated extracted award: ' + name)
                continue
            seen.add(key)
            nonmonetary[row['source_key']].append(name)
            if isinstance(title, str) and title.strip() and _clean(title) in row['raw_text']:
                nonmonetary[row['source_key']].append(_clean(title))
            if extent:
                mapped[row['source_key']].append(extent)
            numeric = float(extent.replace('$', '').replace(',', '').strip()) if extent and NUMBER.fullmatch(extent) else None
            claims.append(dict(source_key=key, publication_key=row['source_key'],
                               supplier_name_raw=name, award_amount=extent,
                               evidence_json=json.dumps(proof,sort_keys=True,ensure_ascii=False) if proof else None,
                               award_amount_numeric=numeric, title_extracted=contract.get('title')))
    # Every visible money token must be accounted for, including a second award
    # in the same block. Title/context blocks remain visible without invented facts.
    for row in rows:
        remainder = ANNOTATION.sub('', row['raw_text'])
        for extent in mapped[row['source_key']]:
            remainder = remainder.replace(extent, '', 1)
        for literal in nonmonetary[row['source_key']]:
            remainder = remainder.replace(literal, '', 1)
        if re.search(r'\$\s*\d|\(\s*\d[\d,]+(?:\.\d+)?\s*\)', remainder):
            errors.append('Unmapped monetary evidence: ' + row['source_key'])
    if not claims:
        errors.append('No supported award claims extracted')
    return claims, errors


def capture_awards(conn, http, *, data_dir=None, client=None, limit=None, log=print,
                   cached_only=False, only_extractions=None):
    """One HTTP page, immutable snapshots, cached month extraction, honest coverage.

    `limit` bounds new model requests only; every source block is always archived.
    Failed/ambiguous extraction retains raw evidence and previously held facts.
    """
    if limit is not None and limit < 0:
        raise ValueError('Extraction limit must be nonnegative')
    root = Path(data_dir or config.DATA_DIR) / 'waterfront'
    root.mkdir(parents=True, exist_ok=True)
    content = http.get_bytes(URL)
    sha = hashlib.sha256(content).hexdigest()
    path = root / (sha + '.html')
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError('Held Waterfront snapshot failed hash verification')
    else:
        # Publish a complete snapshot atomically; interrupted writes cannot truncate
        # an earlier archive file bearing the same content address.
        with tempfile.NamedTemporaryFile(dir=root, suffix='.tmp', delete=False) as held:
            temporary = Path(held.name)
            try:
                held.write(content)
                held.close()
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    # Snapshot precedes parsing; even a changed layout retains its original bytes.
    rows = parse_publication(content)
    buyer_id = seed_buyers(conn)['waterfront-toronto']
    by_month = defaultdict(list)
    for row in rows:
        conn.execute('INSERT INTO award_publication (source_key,buyer_id,publication_month,raw_text,source_position,labels,source_url,snapshot_sha256) '
                     'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET '
                     "source_position=excluded.source_position,snapshot_sha256=excluded.snapshot_sha256,last_seen=datetime('now')",
                     (row['source_key'], buyer_id, row['publication_month'], row['raw_text'], row['source_position'], row['labels'], URL, sha))
        by_month[row['publication_month']].append(row)
    conn.commit()
    from toronto_bids.batch_extraction import ExtractionPending
    # Record the last successfully parsed snapshot for offline batch completion.
    with tempfile.NamedTemporaryFile(dir=root, mode='w', delete=False) as marker:
        temporary = Path(marker.name)
        try:
            marker.write(sha)
            marker.close()
            os.replace(temporary, root / 'latest')
        finally:
            temporary.unlink(missing_ok=True)
    requests, failures, accepted, pending = 0, [], 0, 0
    processed = []
    for month, blocks in by_month.items():
        text = extraction_text(month, blocks)
        digest = _sha(text)
        if only_extractions is not None and digest not in only_extractions:
            continue
        errors = []
        cached = db.get_extraction(conn, digest, EXTRACTOR_VERSION)
        try:
            if cached is None:
                if cached_only:
                    continue
                if limit is not None and requests >= limit:
                    raise ValueError('Extraction pending (new-request limit reached)')
                if client is None:
                    client = ExtractionClient()
                requests += 1
                if hasattr(client, 'extract_document'):
                    result = client.extract_document(digest, [text], source='waterfront')
                else:
                    result = client.extract(text)
                db.mark_extracted(conn, digest, EXTRACTOR_VERSION, json.dumps(result, ensure_ascii=False))
            else:
                result = json.loads(cached)
            processed.append(digest)
            claims, errors = verified_claims(blocks, result)
            # Supported claims survive a partial month. Only a complete verification
            # may retire older derived claims; source observations are never deleted.
            conn.execute('SAVEPOINT waterfront_month')
            live = {c['source_key'] for c in claims}
            if not errors:
                for block in blocks:
                    held_keys = conn.execute('SELECT source_key FROM published_award WHERE publication_key=?',
                                             (block['source_key'],)).fetchall()
                    for held in held_keys:
                        if held['source_key'] not in live:
                            conn.execute('DELETE FROM published_award WHERE source_key=?', (held['source_key'],))
            for claim in claims:
                conn.execute('INSERT INTO published_award (source_key,publication_key,buyer_id,supplier_name_raw,award_amount,award_amount_numeric,title_extracted,extraction_sha256,extractor_version,evidence_json) '
                             'VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET '
                             "title_extracted=excluded.title_extracted,extraction_sha256=excluded.extraction_sha256,extractor_version=excluded.extractor_version,evidence_json=excluded.evidence_json,last_seen=datetime('now')",
                             (claim['source_key'], claim['publication_key'], buyer_id, claim['supplier_name_raw'], claim['award_amount'],
                              claim['award_amount_numeric'], claim['title_extracted'], digest, EXTRACTOR_VERSION,claim['evidence_json']))
            conn.execute('RELEASE waterfront_month')
            accepted += len(claims)
        except ExtractionPending as exc:
            pending += 1
            conn.execute('INSERT INTO publication_extraction (buyer_id,publication_month,extraction_sha256,extractor_version,status,errors) '
                         'VALUES (?,?,?,?,?,?) ON CONFLICT(buyer_id,publication_month) DO UPDATE SET '
                         "extraction_sha256=excluded.extraction_sha256,extractor_version=excluded.extractor_version,status=excluded.status,errors=excluded.errors,last_seen=datetime('now')",
                         (buyer_id, month, digest, EXTRACTOR_VERSION, 'pending', json.dumps([str(exc)])))
            conn.commit()
            log(f'Waterfront {month}: extraction pending; prior supported claims retained')
            continue
        except Exception as exc:
            # Roll back any derived-fact refresh as a unit; source capture is committed.
            if conn.in_transaction:
                conn.rollback()
            errors = [str(exc)]
        conn.execute('INSERT INTO publication_extraction (buyer_id,publication_month,extraction_sha256,extractor_version,status,errors) '
                     'VALUES (?,?,?,?,?,?) ON CONFLICT(buyer_id,publication_month) DO UPDATE SET '
                     "extraction_sha256=excluded.extraction_sha256,extractor_version=excluded.extractor_version,status=excluded.status,errors=excluded.errors,last_seen=datetime('now')",
                     (buyer_id, month, digest, EXTRACTOR_VERSION, 'incomplete' if errors else 'ok', json.dumps(errors)))
        conn.commit()
        if errors:
            failures.append((f'waterfront:{month}', '; '.join(errors)))
        log(f'Waterfront {month}: {len(blocks)} source blocks; {"incomplete" if errors else "verified"}')
    return dict(blocks=len(rows), months=len(by_month), awards=accepted, requests=requests, failures=failures, pending=pending, processed=processed)



def replay_cached_awards(conn, *, data_dir=None, only_extractions=None, log=print):
    """Apply completed batches against the last validated snapshot without fetching."""
    root = Path(data_dir or config.DATA_DIR) / 'waterfront'
    sha = (root / 'latest').read_text().strip()
    if not re.fullmatch(r'[a-f0-9]{64}', sha):
        raise ValueError('Invalid held Waterfront snapshot pointer')
    content = (root / (sha + '.html')).read_bytes()
    if hashlib.sha256(content).hexdigest() != sha:
        raise ValueError('Held Waterfront snapshot failed hash verification')
    class HeldPage:
        def get_bytes(self, url):
            return content
    return capture_awards(conn, HeldPage(), data_dir=data_dir, cached_only=True,
                          only_extractions=only_extractions, log=log)
