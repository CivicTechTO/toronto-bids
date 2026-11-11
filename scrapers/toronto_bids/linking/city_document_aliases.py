"""Original City pages explicitly relating a native call and an Ariba document.

These are reviewed publisher assertions, never digit-derived call conversions.
The original award publication independently identifies the current City record.
"""
import hashlib
import json
from pathlib import Path
import re
from datetime import datetime
from urllib.parse import urlsplit

from lxml import html
from toronto_bids.linking import city_crosswalk as cw, city_archived_awards as archived

PATH = Path(__file__).resolve().parents[1]/'data/city_document_aliases.json'


def applied_targets(conn):
    """Read source-proven aliases independently of assigned canonical IDs."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='city_document_alias_evidence'").fetchone():
        return {}
    targets = {}
    for row in conn.execute('SELECT * FROM city_document_alias_evidence ORDER BY proof_sha256'):
        proof = json.loads(row['proof_json'])
        if (cw.digest(proof) != row['proof_sha256']
                or any(row[k] != proof[k] for k in ('document_number', 'alias_document_number', 'call_number'))):
            raise ValueError('applied City identifier evidence changed')
        alias, doc = row['alias_document_number'], row['document_number']
        if alias in targets and targets[alias] != doc:
            raise ValueError('competing applied City identifier evidence')
        targets[alias] = doc
    return targets


def _field(root, label):
    values = []
    for cell in root.xpath('//td'):
        if cw.clean(cell.text_content()).lower() == label.lower():
            following = cell.getnext()
            if following is not None and following.tag.lower() == 'td':
                values.append(cw.clean(following.text_content()))
    if len(values) != 1:
        raise ValueError('one original labelled alias field required: '+label)
    return values[0]


def _day(value):
    match = re.fullmatch(r'([A-Za-z]+ \d{1,2}, \d{4})(?: at .+)?', value)
    if not match:
        raise ValueError('literal original call date absent')
    return datetime.strptime(match[1], '%B %d, %Y').date().isoformat()


def propose(conn, *, evidence, document_number, archive_source_sha256,
            archive_row_ordinal, page, current=None):
    """Validate original same-page identifiers and independent current identity."""
    index = archived.current_index(conn) if current is None else current
    award, publisher, _ = archived.validate_current_award(
        conn, evidence, document_number, archive_source_sha256, archive_row_ordinal, index)
    body = page['html'].encode('utf-8')
    stamp = page['archive_timestamp']; original = urlsplit(page['source_url'])
    if (hashlib.sha256(body).hexdigest() != page['sha256'] or page['http_status'] != 200
            or original.scheme not in ('http', 'https') or original.hostname != 'wx.toronto.ca'
            or not re.fullmatch(r'/inter/pmmd/calls\.nsf/(?:0/)?[a-f0-9]{32}', original.path, re.I)
            or original.query.lower() != 'opendocument' or not re.fullmatch(r'\d{14}', stamp)
            or page['archive_url'] != 'https://web.archive.org/web/'+stamp+'id_/'+page['source_url']):
        raise ValueError('original identifier assertion provenance changed')
    root = html.fromstring(body)
    call_quote = _field(root, 'Call number:')
    if cw.find_call_numbers(call_quote) != {award['call_number']}:
        raise ValueError('original page names a different native call')
    description = _field(root, 'Description:')
    identifiers = re.findall(r'\bSee\s+ARIBA\s+Doc\s+(\d{10})\b', description, re.I)
    all_ids = set(re.findall(r'\bDoc\s*(\d{10})\b', description, re.I))
    if len(identifiers) != 1 or all_ids != set(identifiers) or identifiers[0] == document_number:
        raise ValueError('one explicit same-page alternate document assertion required')
    alias = identifiers[0]
    if conn.execute('SELECT 1 FROM solicitation WHERE document_number=?', (alias,)).fetchone():
        raise ValueError('alias competes with another current publisher record; further review required')
    issued = _field(root, 'Issue date:'); closed = _field(root, 'Closing date:')
    if (_day(issued) != publisher.get('Issue_Date') or _day(closed) != publisher.get('Closing_Date')
            or datetime.strptime(stamp, '%Y%m%d%H%M%S').date().isoformat() < _day(issued)):
        raise ValueError('original alias call dates disagree with current publisher')
    return dict(version=1, proof_class='published_city_ariba_alias', document_number=document_number,
                alias_document_number=alias, call_number=award['call_number'], page=page,
                call_number_quotation=call_quote, alias_quotation=re.search(r'\bSee\s+ARIBA\s+Doc\s+\d{10}\b', description, re.I)[0],
                issue_date_quotation=issued, closing_date_quotation=closed,
                publisher_record_sha256=cw.digest(publisher), snapshot_sha256=evidence.snapshot_sha256,
                archived_award=dict(source_sha256=archive_source_sha256, row_ordinal=archive_row_ordinal,
                                    row_sha256=award['row_sha256'], printed_award_date=award['published_award_date']))


def replay(conn):
    """Validate immutable approvals before persisting any alias evidence."""
    bundle = json.loads(PATH.read_text())
    if bundle.get('version') != 1:
        raise ValueError('unsupported City alias registry')
    proofs = bundle['reviews']; expected = {cw.digest(p) for p in proofs}
    prior = {r['proof_sha256']: dict(r) for r in conn.execute('SELECT * FROM city_document_alias_evidence')}
    for sha, row in prior.items():
        proof = json.loads(row['proof_json'])
        if (cw.digest(proof) != sha or sha not in expected
                or any(row[k] != proof[k] for k in ('document_number', 'alias_document_number', 'call_number'))):
            raise ValueError('applied City identifier assertion changed or removed')
    applicable = []
    for proof in proofs:
        if conn.execute('SELECT 1 FROM solicitation WHERE document_number=?', (proof['document_number'],)).fetchone():
            applicable.append(proof)
        elif cw.digest(proof) in prior:
            raise ValueError('applied City alias publisher context removed')
    if not applicable:
        return []
    config = json.loads(cw.PROOFS.read_text()); evidence = archived.load_evidence(config, cw.PROOFS)
    index = archived.current_index(conn); seen = set()
    for proof in applicable:
        identifiers = {proof['document_number'], proof['alias_document_number']}
        if identifiers & seen:
            raise ValueError('competing City identifier assertions require review')
        seen.update(identifiers); arc = proof['archived_award']
        actual = propose(conn, evidence=evidence, document_number=proof['document_number'],
                         archive_source_sha256=arc['source_sha256'], archive_row_ordinal=arc['row_ordinal'],
                         page=proof['page'], current=index)
        if actual != proof:
            raise ValueError('City identifier assertion source or context changed')
    for proof in applicable:
        conn.execute('INSERT OR IGNORE INTO city_document_alias_evidence '
                     '(proof_sha256,document_number,alias_document_number,call_number,proof_json) VALUES (?,?,?,?,?)',
                     (cw.digest(proof), proof['document_number'], proof['alias_document_number'], proof['call_number'], cw.canonical(proof)))
    return applicable
