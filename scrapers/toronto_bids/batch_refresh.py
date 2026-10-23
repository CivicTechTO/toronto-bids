"""Apply collected extraction using held evidence, then prepare publication."""

import json
import os
from pathlib import Path
import tempfile

from toronto_bids.extraction import CORPORA, backfill_from_extraction
from toronto_bids.linking.supplier import build_supplier_dimension


def refresh(queue, archive, *, export, manifest_path, log=print):
    result = queue.collect(archive)
    documents = queue.ready_documents()
    result['ready_documents'] = []
    result['ignored_documents'] = []
    if not documents:
        return result
    pdf_shas = {d['sha256'] for d in documents if d['source'] == 'pdf'}
    applied, failed, recognized = set(), set(), set()
    for corpus, where in CORPORA.items():
        matches = pdf_shas & {r[0] for r in archive.execute(f'SELECT sha256 FROM background_pdf WHERE {where}')}
        if not matches:
            continue
        recognized.update(matches)
        try:
            derived = backfill_from_extraction(archive, corpus, log=log)
            coverage = derived.get('coverage') or {}
            if coverage.get('contracts_refused', 0):
                result['errors'].append(f"coverage:{corpus}: {coverage['contracts_refused']} contract(s) refused or withheld; inspect tb agency-coverage")
            applied.update(matches)
        except Exception as exc:
            failed.update(matches)
            result['errors'].append(f'apply:{corpus}: {exc}')
    waterfront = {d['sha256'] for d in documents if d['source'] == 'waterfront'}
    if waterfront:
        try:
            from toronto_bids.sources.waterfront import replay_cached_awards
            replay = replay_cached_awards(archive, only_extractions=waterfront, log=log)
            applied.update(replay['processed'])
            recognized.update(replay['processed'])
            result['errors'].extend(f'{name}: {error}' for name, error in replay['failures'])
        except Exception as exc:
            failed.update(waterfront)
            result['errors'].append(f'apply:waterfront: {exc}')
    ready = [d['id'] for d in documents if d['sha256'] in applied - failed]
    ignored = [d['id'] for d in documents if d['sha256'] not in recognized | failed]
    if ignored:
        queue.acknowledge(ignored, status='superseded')
        result['ignored_documents'] = ignored
    if ready:
        build_supplier_dimension(archive)
        archive.commit()
        export_rc = export()
        if export_rc not in (None, 0):
            raise ValueError('Batch refresh export failed')
        # Write this last. The runner publishes before acknowledging exactly
        # these IDs; a failed publish leaves them cached for the next attempt.
        path = Path(manifest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', delete=False) as f:
            temporary = Path(f.name)
            try:
                json.dump({'documents': ready}, f)
                f.close()
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        result['ready_documents'] = ready
    return result
