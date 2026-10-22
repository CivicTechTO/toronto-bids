"""Stable archive identities, with only source-proven equivalences (#124).

Natural ingest keys remain on source tables. Repeated portal labels do not resolve
identity; buyer/page URLs do. Documents and council items can describe many procurements.
"""
import json
from collections import defaultdict
from contextlib import contextmanager

FACT_TABLES = ('solicitation', 'award', 'noncompetitive', 'ariba_posting', 'bid',
               'background_pdf', 'composite_award', 'ariba_attachment', 'capital_project',
               'agency_solicitation', 'agency_award', 'agency_bid', 'portal_listing', 'published_award')


@contextmanager
def _transaction(conn):
    conn.execute('SAVEPOINT procurement_resolution')
    try:
        yield
        conn.execute('RELEASE procurement_resolution')
    except BaseException:
        conn.execute('ROLLBACK TO procurement_resolution')
        conn.execute('RELEASE procurement_resolution')
        raise


class _Identities:
    def __init__(self, conn):
        self.conn = conn
        self.keys = {(r['namespace'], r['value']): r['procurement_id']
                     for r in conn.execute('SELECT * FROM procurement_key')}

    def get(self, namespace, value):
        key = (namespace, str(value))
        if key not in self.keys:
            pid = self.conn.execute('INSERT INTO procurement DEFAULT VALUES RETURNING id').fetchone()[0]
            self.conn.execute('INSERT INTO procurement_key VALUES (?,?,?)', (*key, pid))
            self.keys[key] = pid
        return self.keys[key]

    def join(self, left, right, evidence):
        if left == right:
            return left
        # Singular publisher identities may not be fused through a broad council item,
        # a changed source link, or a newly conflicting legacy reference.
        groups = defaultdict(set)
        for (namespace, value), pid in self.keys.items():
            if pid in (left, right) and (namespace in ('city_document', 'city_workspace', 'city_call')
                                         or namespace.startswith('agency_contract:')):
                groups[namespace].add(value)
        if any(len(values) > 1 for values in groups.values()):
            raise ValueError('Conflicting procurement anchors; refusing identity merge: ' + evidence)
        winner, loser = sorted((left, right))
        self.conn.execute('UPDATE procurement SET merged_into=?,merge_evidence=? WHERE id=?',
                          (winner, evidence, loser))
        # Flatten older redirects so every published alias points at a current identity.
        self.conn.execute('UPDATE procurement SET merged_into=? WHERE merged_into=?', (winner, loser))
        self.conn.execute('UPDATE procurement_key SET procurement_id=? WHERE procurement_id=?', (winner, loser))
        for key, pid in self.keys.items():
            if pid == loser:
                self.keys[key] = winner
        return winner


def resolve_procurements(conn):
    """Resolve all held source facts atomically; return the active procurement count.

    The caller owns commit. IDs/keys/redirects persist across refreshes; source facts
    retain their payload and history. Unsupported extraction identities are untouched.
    """
    with _transaction(conn):
        return _resolve(conn)


def _resolve(conn):
    ids = _Identities(conn)
    # Derive all approved links before assigning FKs, so a late join cannot leave a fact
    # pointing at an alias. A conflicting reference stays unjoined to document identities.
    ref_docs = defaultdict(set)
    for row in conn.execute('SELECT DISTINCT reference,document_number FROM bid '
                            'WHERE reference IS NOT NULL AND document_number IS NOT NULL'):
        ref_docs[row['reference']].add(row['document_number'])
    for row in conn.execute('SELECT * FROM solicitation_link'):
        ref_docs[row['reference']].add(row['document_number'])
    for ref, docs in sorted(ref_docs.items()):
        if len(docs) > 1:
            existing = ids.keys.get(('city_council_bid', ref))
            if existing is not None and any(pid == existing and namespace == 'city_document'
                                            for (namespace, _), pid in ids.keys.items()):
                raise ValueError('Previously joined, now ambiguous council reference: ' + ref)
        if len(docs) == 1:
            doc = next(iter(docs))
            ids.join(ids.get('city_council_bid', ref), ids.get('city_document', doc),
                     json.dumps(dict(rule='held_bid_or_solicitation_link', reference=ref, document_number=doc)))
    for row in conn.execute('SELECT rfx_id,document_number FROM ariba_posting WHERE document_number IS NOT NULL'):
        ids.join(ids.get('ariba_event', row['rfx_id']), ids.get('city_document', row['document_number']),
                 json.dumps(dict(rule='published_ariba_document_number', rfx_id=row['rfx_id'], document_number=row['document_number'])))

    # Resolving new keys below never merges IDs, so the in-memory assignment is stable.
    ref_ids = defaultdict(set)
    document_ids = defaultdict(set)
    for table in FACT_TABLES:
        if table == 'background_pdf':
            continue  # many-to-many associations, collected after all contract facts
        updates = []
        for row in conn.execute(f'SELECT rowid AS _rowid,* FROM {table} ORDER BY rowid'):
            pid = None
            if table in ('solicitation', 'award', 'ariba_attachment'):
                pid = ids.get('city_document', row['document_number'])
            elif table == 'noncompetitive':
                pid = ids.get('city_workspace', row['workspace_number'])
            elif table == 'composite_award':
                pid = ids.get('city_call', row['call_number'])
            elif table == 'ariba_posting':
                pid = ids.get('ariba_event', row['rfx_id'])
            elif table == 'capital_project':
                pid = ids.get('city_plan', row['name'])
            elif table == 'bid':
                if row['document_number']:
                    pid = ids.get('city_document', row['document_number'])
                elif row['reference']:
                    pid = ids.get('city_council_bid', row['reference'])
                else:
                    # A stored observation with no publisher ID still has archive identity.
                    pid = row['procurement_id'] or ids.get('unidentified_bid', row['id'])
            elif table == 'published_award':
                pid = ids.get('published_award:' + str(row['buyer_id']), row['source_key'])
            elif table == 'portal_listing' or (table == 'agency_solicitation'
                                               and row['source'] == 'bids_tenders' and row['portal_url']):
                pid = ids.get('agency_portal:' + str(row['buyer_id']), row['portal_url'])
            else:
                pid = ids.get('agency_contract:' + str(row['buyer_id']), row['native_ref'])
            updates.append((pid, row['_rowid']))
            if 'reference' in row.keys() and row['reference']:
                ref_ids[row['reference']].add(pid)
            if 'report_url' in row.keys() and row['report_url']:
                document_ids[row['report_url']].add(pid)
        conn.executemany(f'UPDATE {table} SET procurement_id=? WHERE rowid=? AND procurement_id IS NOT ?',
                         [(pid, rowid, pid) for pid, rowid in updates])

    conn.execute('DELETE FROM procurement_document')
    for row in conn.execute('SELECT id,url,reference,document_number FROM background_pdf ORDER BY id'):
        candidates = set(document_ids[row['url']])
        if row['document_number']:
            candidates.add(ids.get('city_document', row['document_number']))
        if row['reference']:
            candidates.update(ref_ids[row['reference']])
        conn.executemany('INSERT INTO procurement_document VALUES (?,?)',
                         [(pid, row['url']) for pid in sorted(candidates)])
        pid = next(iter(candidates)) if len(candidates) == 1 else None
        conn.execute('UPDATE background_pdf SET procurement_id=? WHERE id=? AND procurement_id IS NOT ?',
                     (pid, row['id'], pid))
    conn.execute('DELETE FROM procurement_council_item')
    for row in conn.execute('SELECT reference FROM council_item ORDER BY reference'):
        conn.executemany('INSERT INTO procurement_council_item VALUES (?,?)',
                         [(pid, row['reference']) for pid in sorted(ref_ids[row['reference']])])
    violations = conn.execute('PRAGMA foreign_key_check').fetchall()
    if violations:
        raise ValueError('Procurement resolution found foreign-key violations')
    return conn.execute('SELECT COUNT(*) FROM procurement WHERE merged_into IS NULL').fetchone()[0]
