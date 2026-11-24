"""Assign stable procurement IDs and merge only source-proven identity links."""
import json
from collections import defaultdict
from contextlib import contextmanager

AGENCY_CONTRACT = 'agency_contract:'

FACT_TABLES = ('solicitation', 'award', 'noncompetitive', 'ariba_posting', 'bid',
               'background_pdf', 'composite_award', 'ariba_attachment', 'capital_project', 'bid_scope',
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
    def __init__(self, conn, city_aliases=()):
        self.conn = conn
        self.city_aliases = {frozenset((p['document_number'], p['alias_document_number'])) for p in city_aliases}
        self.keys = {(r['namespace'], r['value']): r['procurement_id']
                     for r in conn.execute('SELECT * FROM procurement_key')}

    def get(self, namespace, value):
        key = (namespace, str(value))
        if key not in self.keys:
            pid = self.conn.execute('INSERT INTO procurement DEFAULT VALUES RETURNING id').fetchone()[0]
            self.conn.execute('INSERT INTO procurement_key VALUES (?,?,?)', (*key, pid))
            self.keys[key] = pid
        return self.keys[key]

    def join(self, left, right, evidence, *, prefer=None):
        if left == right:
            return left
        # Refuse merges that combine distinct primary anchors, except reviewed city_document aliases.
        groups = defaultdict(set)
        for (namespace, value), pid in self.keys.items():
            if pid in (left, right) and (namespace in ('city_document', 'city_workspace', 'city_call')
                                         or namespace.startswith(AGENCY_CONTRACT)):
                groups[namespace].add(value)
        if any(len(values) > 1 and not (namespace == 'city_document' and frozenset(values) in self.city_aliases)
               for namespace, values in groups.items()):
            raise ValueError('Conflicting procurement anchors; refusing identity merge: ' + evidence)
        winner, loser = sorted((left, right)) if prefer is None else (prefer, right if prefer == left else left)
        if prefer is not None and prefer not in (left, right):
            raise ValueError('preferred canonical identity must be an existing merge participant')
        self.conn.execute('UPDATE procurement SET merged_into=?,merge_evidence=? WHERE id=?',
                          (winner, evidence, loser))
        # Flatten transitive redirects to the new winner.
        self.conn.execute('UPDATE procurement SET merged_into=? WHERE merged_into=?', (winner, loser))
        self.conn.execute('UPDATE procurement_key SET procurement_id=? WHERE procurement_id=?', (winner, loser))
        for key, pid in self.keys.items():
            if pid == loser:
                self.keys[key] = winner
        return winner


def register_agency_contract_keys(conn, buyer_id, references):
    """Allocate standard agency keys for reviewed references within the caller's transaction."""
    ids = _Identities(conn)
    namespace = AGENCY_CONTRACT + str(buyer_id)
    return {reference: ids.get(namespace, reference) for reference in sorted(references)}


def resolve_procurements(conn):
    """Resolve procurement IDs across all fact tables inside a savepoint (caller owns commit)."""
    with _transaction(conn):
        return _resolve(conn)


def _resolve(conn):
    from toronto_bids.linking.city_identity_repairs import repair_city_identities
    repair_city_identities(conn)
    from toronto_bids.linking.city_cached_bids import replay_reviewed_bids
    replay_reviewed_bids(conn)
    from toronto_bids.linking.city_scoped_bids import replay_scoped_bids
    replay_scoped_bids(conn)
    from toronto_bids.linking.city_crosswalk import reviewed_links
    crosswalks = reviewed_links(conn)
    from toronto_bids.linking.city_native_participation import replay as replay_native_participation
    native_participation = replay_native_participation(conn)
    from toronto_bids.linking.city_document_aliases import reviewed_receipt_aliases, replay as replay_city_aliases
    from toronto_bids.linking.city_crosswalk import digest as proof_digest
    city_aliases = [*replay_city_aliases(conn), *reviewed_receipt_aliases(conn)]
    alias_targets = {p['alias_document_number']: p['document_number'] for p in city_aliases}
    ids = _Identities(conn, city_aliases)
    for proof in city_aliases:
        target = ids.get('city_document', proof['document_number'])
        rule = 'reviewed_city_receipt_alias' if 'receipt_review' in proof else 'published_city_ariba_alias'
        evidence = json.dumps(dict(rule=rule, proof_sha256=proof_digest(proof)))
        if call := proof.get('call_number'):
            ids.join(target, ids.get('city_call', call), evidence, prefer=target)
        ids.join(target, ids.get('city_document', proof['alias_document_number']),
                 evidence, prefer=target)
    for proof in native_participation:
        ids.join(ids.get('city_call', proof['call_number']), ids.get('city_document', proof['document_number']),
                 json.dumps(dict(rule='reviewed_native_call_participation', call_number=proof['call_number'],
                                 document_number=proof['document_number'], reference=proof['reference'])))
    # Resolve all joins before assigning FKs so no fact points at a merged alias.
    ref_docs = defaultdict(set)
    for row in conn.execute('SELECT DISTINCT reference,document_number FROM bid '
                            'WHERE reference IS NOT NULL AND document_number IS NOT NULL'):
        ref_docs[row['reference']].add(alias_targets.get(row['document_number'], row['document_number']))
    for row in conn.execute('SELECT * FROM solicitation_link'):
        ref_docs[row['reference']].add(alias_targets.get(row['document_number'], row['document_number']))
    for ref, docs in sorted(ref_docs.items()):
        if len(docs) > 1:
            existing = ids.keys.get(('city_council_bid', ref))
            if existing is not None and any(pid == existing and namespace == 'city_document'
                                            for (namespace, _), pid in ids.keys.items()):
                raise ValueError('Previously joined, now ambiguous council reference: ' + ref)
        if len(docs) == 1:
            doc = next(iter(docs))
            ids.join(ids.get('city_council_bid', ref), ids.get('city_document', doc),
                     json.dumps(crosswalks.get(ref) or dict(rule='held_bid_or_solicitation_link', reference=ref, document_number=doc)))
    for row in conn.execute('SELECT rfx_id,document_number FROM ariba_posting WHERE document_number IS NOT NULL'):
        ids.join(ids.get('ariba_event', row['rfx_id']), ids.get('city_document', row['document_number']),
                 json.dumps(dict(rule='published_ariba_document_number', rfx_id=row['rfx_id'], document_number=row['document_number'])))

    # Only new keys are created below; existing IDs do not merge.
    ref_ids = defaultdict(set)
    for (namespace, value), pid in ids.keys.items():
        if namespace == 'city_council_bid':
            ref_ids[value].add(pid)
    document_ids = defaultdict(set)
    for table in FACT_TABLES:
        if table == 'background_pdf':
            continue  # Handled below after collecting document links.
        updates = []
        for row in conn.execute(f'SELECT rowid AS _rowid,* FROM {table} ORDER BY rowid'):
            pid = None
            if table in ('solicitation', 'award', 'ariba_attachment', 'bid_scope'):
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
                    pid = row['procurement_id'] or ids.get('unidentified_bid', row['id'])
            elif table == 'published_award':
                pid = ids.get('published_award:' + str(row['buyer_id']), row['source_key'])
            elif table in ('agency_bid', 'agency_award') and row['source'] == 'merx':
                # MERX keys on the notice page URL, not reusable printed labels.
                pid = ids.get('agency_portal:' + str(row['buyer_id']), row['native_ref'])
            elif table == 'portal_listing' or (table == 'agency_solicitation'
                                               and row['source'] == 'bids_tenders' and row['portal_url']):
                pid = ids.get('agency_portal:' + str(row['buyer_id']), row['portal_url'])
            else:
                pid = ids.get(AGENCY_CONTRACT + str(row['buyer_id']), row['native_ref'])
            updates.append((pid, row['_rowid']))
            if 'reference' in row.keys() and row['reference']:
                ref_ids[row['reference']].add(pid)
            if 'report_url' in row.keys() and row['report_url']:
                document_ids[row['report_url']].add(pid)
        conn.executemany(f'UPDATE {table} SET procurement_id=? WHERE rowid=? AND procurement_id IS NOT ?',
                         [(pid, rowid, pid) for pid, rowid in updates])

    for proof in native_participation:
        pid = ids.get('city_document', proof['document_number'])
        ref_ids[proof['reference']].add(pid)
        document_ids[proof['report_url']].add(pid)
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
