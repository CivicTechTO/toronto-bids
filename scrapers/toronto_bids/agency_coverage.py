"""Agency identity decisions and evidence-linked coverage; no identity matching rules."""
import hashlib
import json
from collections import Counter

from toronto_bids.extract import EXTRACTOR_VERSION


COUNTING = (
    'Contracts and extracted facts are counted once per (corpus, document SHA-256, '
    'extraction version, result digest, contract index), across indexed URLs. '
    'source_observations counts URL/contract pairs. Accepted and refused sets may '
    'overlap if document contexts produce different decisions. Fact counts include '
    'named suppliers only and describe extraction evidence, not distinct stored rows. '
    'Alias counts describe stored rows normalized/merged in this rebuild, across '
    'solicitations, bids and awards; they are not lost facts.'
)


def decision(row, corpus, buyer_id, index, contract, native_ref, reason, contract_count):
    return dict(corpus=corpus, buyer_id=buyer_id, sha256=row['sha256'],
                extractor_version=EXTRACTOR_VERSION,
                result_sha256=hashlib.sha256(row['result_json'].encode()).hexdigest(),
                decision_sha256=hashlib.sha256(json.dumps([native_ref, reason, row['reference']]).encode()).hexdigest(),
                contract_index=index, contract_count=contract_count,
                report_url=row['url'], report_reference=row['reference'],
                native_ref=native_ref, reason=reason,
                contract_json=json.dumps(contract, sort_keys=True, ensure_ascii=False))


def persist(conn, corpus, decisions, aliases, docs_processed):
    """Called inside the derived-row swap transaction; never commits caller work."""
    conn.execute('UPDATE agency_derivation SET active=0 WHERE corpus=?', (corpus,))
    for item in decisions:
        cols = list(item)
        conn.execute(
            f"INSERT INTO agency_derivation ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) "
            "ON CONFLICT(corpus,sha256,extractor_version,result_sha256,contract_index,report_url,decision_sha256) "
            "DO UPDATE SET active=1, native_ref=excluded.native_ref, reason=excluded.reason, "
            "report_reference=excluded.report_reference, last_seen=datetime('now')",
            tuple(item.values()))
    summary = summarize(decisions) | {
        'docs_processed': docs_processed,
        'aliases_normalized': aliases['normalized'], 'aliases_merged': aliases['merged'],
    }
    conn.execute("INSERT INTO agency_coverage(corpus,summary_json) VALUES (?,?) "
                 "ON CONFLICT(corpus) DO UPDATE SET summary_json=excluded.summary_json, rebuilt_at=datetime('now')",
                 (corpus, json.dumps(summary, sort_keys=True)))
    return summary


def summarize(decisions):
    groups = {'accepted': {}, 'refused': {}}
    for item in decisions:
        key = tuple(item[k] for k in ('sha256', 'extractor_version', 'result_sha256', 'contract_index'))
        groups['refused' if item['reason'] else 'accepted'][key] = json.loads(item['contract_json'])
    result = {'source_observations': len(decisions),
              'unique_documents_with_facts': len({d['sha256'] for d in decisions}), 'counting': COUNTING}
    for status, contracts in groups.items():
        result[f'contracts_{status}'] = len(contracts)
        for kind in ('bid', 'award'):
            result[f'{status}_{kind}_facts'] = sum(
                bool(f.get('supplier_name')) for c in contracts.values() for f in c.get(kind + 's', []))
    return result


def report(conn, *, only=None, baseline=None):
    """Read-only report of latest successful coverage, current and historical decisions."""
    corpora = [only] if only else ['trca', 'zoo', 'ep']
    agencies = {}
    for corpus in corpora:
        saved = conn.execute('SELECT * FROM agency_coverage WHERE corpus=?', (corpus,)).fetchone()
        rows = conn.execute('SELECT * FROM agency_derivation WHERE corpus=? ORDER BY id', (corpus,)).fetchall()
        agencies[corpus] = {
            'rebuilt_at': saved['rebuilt_at'] if saved else None,
            'summary': json.loads(saved['summary_json']) if saved else None,
            'decisions': [dict(r) | {'contract': json.loads(r['contract_json'])} for r in rows],
        }
        for d in agencies[corpus]['decisions']:
            del d['contract_json']
    result = {'counting': COUNTING, 'agencies': agencies,
              'incomplete': any(a['summary'] is None or a['summary']['contracts_refused'] for a in agencies.values())}
    if baseline is not None:
        result['reconciliation'] = reconcile(conn, baseline, agencies)
        result['incomplete'] = result['incomplete'] or any(
            r['unmatched'] for r in result['reconciliation'].values())
    return result


def reconcile(conn, baseline, agencies):
    """Compare held facts, not procurement identity: evidence links are not permission to merge."""
    from toronto_bids.linking.agency_reference import normalize_agency_reference

    sources = {c + '_board' for c in agencies}
    buyer_slugs = {r['id']: r['slug'] for r in baseline.execute('SELECT id,slug FROM buyer')}
    # Buyer IDs are database-local. Compare by slug even when the baseline's IDs differ.
    live_slugs = {r['id']: r['slug'] for r in conn.execute('SELECT id,slug FROM buyer')}
    evidence = [d for a in agencies.values() for d in a['decisions'] if d['active']]
    by_url = {}
    for d in evidence:
        by_url.setdefault((d['corpus'], d['report_url']), []).append(d)
    result = {}
    for table, name, amount, kind in (
        ('agency_award', 'supplier_name_raw', 'award_amount', 'awards'),
        ('agency_bid', 'bidder_name_raw', 'bid_price', 'bids'),
    ):
        current = [dict(r) for r in conn.execute(f'SELECT * FROM {table}') if r['source'] in sources]
        def signature(r, slugs):
            return (slugs[r['buyer_id']], r['source'], r['report_url'], r[name], r[amount])
        live_facts = {}
        identities = set()
        bid_keys = {}
        for r in current:
            live_facts.setdefault(signature(r, live_slugs), []).append(r)
            identities.add((live_slugs[r['buyer_id']], r['source'], r['native_ref'], r[name], r[amount]))
            if kind == 'bids':
                bid_keys[(live_slugs[r['buyer_id']], r['source'], r['native_ref'], r[name])] = r
        counts = Counter()
        unmatched = []
        changed_amounts = []
        for row in baseline.execute(f'SELECT * FROM {table} ORDER BY id'):
            row = dict(row)
            if row['source'] not in sources:
                continue
            counts['baseline_rows'] += 1
            def identity(ref):
                return (buyer_slugs[row['buyer_id']], row['source'], ref, row[name], row[amount])
            if identity(row['native_ref']) in identities:
                counts['same_identity'] += 1
                continue
            try:
                canonical = normalize_agency_reference(buyer_slugs[row['buyer_id']], row['native_ref'])
            except ValueError:
                canonical = None
            if canonical and identity(canonical) in identities:
                counts['canonical_alias'] += 1
                continue
            changed = (bid_keys.get(identity(row['native_ref'])[:-1]) or
                       (bid_keys.get(identity(canonical)[:-1]) if canonical else None))
            if changed:
                counts['same_bid_key_changed_amount'] += 1
                changed_amounts.append({'prior_row': row, 'current_row': changed})
                continue
            if live_facts.get(signature(row, buyer_slugs)):
                counts['same_facts_other_reference'] += 1
                continue
            corpus = row['source'].removesuffix('_board')
            matches = [d for d in by_url.get((corpus, row['report_url']), [])
                       if any(f.get('supplier_name') == row[name] and f.get('amount_raw') == row[amount]
                              for f in d['contract'].get(kind, []))]
            disposition = ('refused_identity' if any(d['reason'] for d in matches)
                           else 'accepted_evidence_not_stored' if matches else 'no_matching_current_evidence')
            counts[disposition] += 1
            unmatched.append({'prior_row': row, 'disposition': disposition, 'evidence': matches})
        result[table] = {'counts': dict(counts), 'current_rows': len(current), 'unmatched': unmatched,
                         'changed_bid_amounts': changed_amounts,
                         'counting': 'Each baseline row receives one disposition. Same-fact representation is not an identity equivalence.'}
    return result
