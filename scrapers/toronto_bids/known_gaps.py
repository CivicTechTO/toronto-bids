"""Reviewed source gaps the nightly reports as known rather than failed (#283).

A failure is known only when it matches reviewed evidence exactly; anything else stays a
failure. Standalone commands keep their strict exit codes — only the nightly verdict reads
this, so a step that fails for the same reviewed reasons every night still turns red the
first night it fails for a new one.

- `download:trca` — an original document reviewed in `trca_document_gaps.json` (#259).
- `coverage:<corpus>` — award claims withheld by reviewed identity evidence (#258).
- `identity:<corpus>` — every currently refused contract is in the recorded baseline
  `known_agency_refusals.json` (#282). The baseline is keyed on the extraction cache, so a
  prompt change re-flags every refusal for review, as it should.
- `waterfront:<month>` — every verifier error for that month is recorded verbatim in
  `known_gaps.json`.
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
    # No baseline recorded yet means no refusal is known.
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
    """Active identity refusals, one per (document, version, result, contract) — the same
    dedup `agency_coverage.summarize` counts by. Reviewed withholdings are not refusals."""
    out = {}
    for row in conn.execute(
            'SELECT * FROM agency_derivation WHERE corpus=? AND active=1 AND reason IS NOT NULL '
            'ORDER BY id', (corpus,)):
        proof = json.loads(row['identity_evidence_json'] or 'null')
        if proof and proof.get('withheld_awards_reason'):
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
    """Write the current refusals as the reviewed baseline. Run after a #282 review pass,
    then commit the file: the nightly treats exactly these contracts as known. Corpora
    not named keep their recorded baseline."""
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
