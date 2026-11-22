"""Classify nightly failures against reviewed gap baselines.

Only exact matches to recorded evidence (TRCA document gaps, agency coverage/refusal baselines,
Waterfront verifier errors) are downgraded from failure to known gap. Standalone commands ignore this.
"""
import json
import re
from datetime import UTC, datetime
from pathlib import Path

DATA = Path(__file__).parent / 'data'
GAPS_PATH = DATA / 'known_gaps.json'
REFUSALS_PATH = DATA / 'known_agency_refusals.json'

_TRCA_DOWNLOAD = re.compile(r'^skip (https://\S+): |left queued: (https://\S+)$')


def _load(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else {}


def load_registry():
    from toronto_bids.sources.trca_document_recovery import load_documents
    gaps = _load(GAPS_PATH)
    refusals = _load(REFUSALS_PATH)
    return {
        'trca_documents': {d['document_id'] for d in load_documents()},
        'waterfront': gaps.get('waterfront', {}),
        'refusals': {corpus: {_key(r) for r in rows}
                     for corpus, rows in refusals.get('corpora', {}).items()},
    }


def _key(row):
    return (row['sha256'], row['extractor_version'], row['result_sha256'], int(row['contract_index']))


def refused_contracts(conn, corpus):
    """Active identity refusals deduplicated by (sha256, version, result, contract_index), excluding reviewed withholdings."""
    out = {}
    for row in conn.execute(
            'SELECT * FROM agency_derivation WHERE corpus=? AND active=1 AND reason IS NOT NULL '
            'ORDER BY id', (corpus,)):
        proof = json.loads(row['identity_evidence_json'] or 'null')
        # Verified native identity with individual award admissions is an award gap, not an identity refusal.
        if proof and (proof.get('withheld_awards_reason') or
                      (row['native_ref'] and proof.get('award_admission'))):
            continue
        out.setdefault(_key(row), row)
    return out


def _known_reason(conn, name, error, registry):
    if name == 'download:trca':
        from toronto_bids.sources.trca_document_recovery import document_id
        match = _TRCA_DOWNLOAD.search(error)
        if not match:
            return None
        try:
            doc = document_id(match[1] or match[2])
        except ValueError:
            return None
        if doc in registry['trca_documents']:
            return 'reviewed unavailable TRCA original (#259); stays queued'
        return None
    kind, _, scope = name.partition(':')
    if kind == 'coverage' and error.startswith('incomplete coverage: ') and 'withheld after source review' in error:
        return 'award claims withheld by reviewed identity evidence (#258)'
    if kind == 'identity':
        current = set(refused_contracts(conn, scope))
        baseline = registry['refusals'].get(scope, set())
        if current and current <= baseline:
            return 'refused identities in the reviewed baseline (#282)'
        return None
    if kind == 'waterfront':
        entry = registry['waterfront'].get(scope)
        if entry and set(error.split('; ')) <= set(entry['errors']):
            return entry['reason']
    return None


def classify(conn, failures, registry=None):
    """Split failures into (unknown, known); known entries carry their reviewed reason."""
    registry = load_registry() if registry is None else registry
    unknown, known = [], []
    for name, error in failures:
        reason = _known_reason(conn, name, error, registry)
        if reason is None:
            unknown.append((name, error))
        else:
            known.append({'name': name, 'error': error, 'reason': reason})
    return unknown, known


def record_refusals(conn, corpora, path=REFUSALS_PATH):
    """Write current refusals for corpora to the reviewed baseline JSON, preserving unlisted corpora."""
    path = Path(path)
    held = json.loads(path.read_text()) if path.is_file() else {}
    payload = {
        'recorded_at': datetime.now(UTC).strftime('%Y-%m-%d'),
        'issue': 282,
        'corpora': held.get('corpora', {}),
    }
    for corpus in corpora:
        payload['corpora'][corpus] = [
            dict(sha256=r['sha256'], extractor_version=r['extractor_version'],
                 result_sha256=r['result_sha256'], contract_index=r['contract_index'],
                 report_url=r['report_url'], reason=r['reason'])
            for r in refused_contracts(conn, corpus).values()]
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + '\n')
    return {c: len(payload['corpora'][c]) for c in corpora}
