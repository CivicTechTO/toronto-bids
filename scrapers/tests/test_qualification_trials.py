"""Paid-boundary and restart tests for isolated qualification; no real requests."""
import copy
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from toronto_bids.claude import ClaudeExtractionClient, extraction_request
from toronto_bids.llm_budget import BudgetExceeded, UsageBudget
from toronto_bids.qualification import (QualificationEvidenceError, digest, identity, load_cohort,
                                       qualification_report, score, waterfront_relationships)
from toronto_bids.qualification_trials import prepare_trials, run_sync, submit_batch, collect_batch


@pytest.fixture
def prepared(tmp_path):
    text = 'A negative procurement control.'
    (tmp_path / 'source.txt').write_text(text)
    cohort = {'version': 1, 'thresholds': {'ep': {'precision': 1.0, 'recall': 1.0,
              'modes': ['sync', 'batch'], 'difficult_repeats': 2}}, 'documents': [
        {'id': 'negative', 'corpus': 'ep', 'difficult': True, 'text_path': 'source.txt',
         'text_sha256': digest(text.encode()), 'document_sha256': 'a' * 64,
         'review_status': 'reviewed', 'contracts': [], 'relations': [], 'review_notes': 'Negative source.'}]}
    path = tmp_path / 'cohort.json'
    path.write_text(json.dumps(cohort))
    paths = prepare_trials(path, tmp_path / 'runs', request_factory=extraction_request, campaign_id='test')
    return paths, UsageBudget(tmp_path / 'ledger.sqlite', '20')


def message(*, stop='end_turn', text='{"contracts": []}'):
    return {'id': 'message-1', 'model': 'claude-sonnet-5-5', 'stop_reason': stop,
            'content': [{'type': 'text', 'text': text}], 'usage': {'input_tokens': 50, 'output_tokens': 10}}


def ledger(budget):
    with sqlite3.connect(budget.path) as c:
        return c.execute('SELECT status,charged_microdollars FROM request').fetchall()


def test_offline_builder_has_exact_production_request_order(monkeypatch, tmp_path):
    monkeypatch.setattr('toronto_bids.config.DATA_DIR', tmp_path)
    monkeypatch.delenv('TB_CLAUDE_EFFORT', raising=False)
    client = ClaudeExtractionClient(api_key='test-key', models=['claude-sonnet-5-5'])
    assert identity(client.request_body('source')) == identity(extraction_request('source'))
    assert not client.budget.path.exists()


def test_sync_response_is_durable_billed_and_never_sent_twice(prepared):
    paths, budget = prepared
    calls = []
    def handler(request):
        calls.append(request)
        saved = json.loads(paths[0].read_text())['attempts']['negative']
        assert saved['dispatch'] == 'uncertain' and saved['reservation']
        return httpx.Response(200, json=message())
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = run_sync(paths[0], budget=budget, api_key='test-key', http=http)
        run_sync(paths[0], budget=budget, api_key='test-key', http=http)
    a = result['attempts']['negative']
    assert len(calls) == 1 and a['state'] == 'success'
    assert a['provenance']['response_id'] == 'message-1'
    assert a['raw_response'] == message() and ledger(budget)[0][0] == 'reported'


def test_truncated_extraction_keeps_raw_response_and_charge(prepared):
    paths, budget = prepared
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=message(stop='max_tokens')))) as http:
        result = run_sync(paths[0], budget=budget, api_key='test-key', http=http)
    a = result['attempts']['negative']
    assert a['state'] == 'failed' and a['charged_microdollars'] > 0
    assert a['raw_response']['stop_reason'] == 'max_tokens'
    assert 'extraction' not in a


def test_uncertain_sync_post_keeps_reservation_without_retry(prepared):
    paths, budget = prepared
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('lost response', request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.ReadTimeout):
            run_sync(paths[0], budget=budget, api_key='test-key', http=http)
        result = run_sync(paths[0], budget=budget, api_key='test-key', http=http)
    assert len(calls) == 1 and result['attempts']['negative']['state'] == 'pending'
    assert ledger(budget)[0][0] == 'reserved'


def test_request_tampering_is_refused_before_reservation(prepared):
    paths, budget = prepared
    data = json.loads(paths[0].read_text())
    data['attempts']['negative']['provenance']['request']['model'] = 'changed'
    paths[0].write_text(json.dumps(data))
    with pytest.raises(ValueError, match='changed'):
        run_sync(paths[0], budget=budget, api_key='test-key')
    assert not budget.path.exists()


def test_missing_key_does_not_reserve_an_allowance(prepared, monkeypatch):
    paths, budget = prepared
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    with pytest.raises(ValueError, match='API_KEY'):
        run_sync(paths[0], budget=budget)
    assert not budget.path.exists()


def test_batch_receipts_match_every_custom_id_and_collection_is_idempotent(prepared):
    paths, budget = prepared
    path, sent = paths[2], []
    def handler(request):
        if request.method == 'POST':
            sent.extend(json.loads(request.content)['requests'])
            return httpx.Response(200, json={'id': 'msgbatch_test'})
        if request.url.path.endswith('/results'):
            return httpx.Response(200, text='\n'.join(json.dumps({'custom_id': r['custom_id'],
                'result': {'type': 'succeeded', 'message': message()}}) for r in reversed(sent)))
        return httpx.Response(200, json={'id': 'msgbatch_test', 'processing_status': 'ended'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        submit_batch(path, budget=budget, api_key='test-key', http=http)
        result = collect_batch(path, budget=budget, api_key='test-key', http=http)
        before = ledger(budget)
        collect_batch(path, budget=budget, api_key='test-key', http=http)
        assert ledger(budget) == before
        with pytest.raises(ValueError, match='submission'):
            submit_batch(path, budget=budget, api_key='test-key', http=http)
    assert result['submission']['state'] == 'ended'
    assert result['attempts']['negative']['state'] == 'success'


def test_foreign_batch_receipt_is_preserved_without_settlement(prepared):
    paths, budget = prepared
    def handler(request):
        if request.method == 'POST': return httpx.Response(200, json={'id': 'msgbatch_test'})
        if request.url.path.endswith('/results'):
            return httpx.Response(200, text=json.dumps({'custom_id': 'foreign', 'result': {'type': 'succeeded', 'message': message()}}))
        return httpx.Response(200, json={'id': 'msgbatch_test', 'processing_status': 'ended'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        submit_batch(paths[2], budget=budget, api_key='test-key', http=http)
        with pytest.raises(ValueError, match='membership'):
            collect_batch(paths[2], budget=budget, api_key='test-key', http=http)
    assert ledger(budget)[0][0] == 'reserved'
    assert 'foreign' in json.loads(paths[2].read_text())['submission']['raw_results']


def test_unsent_batch_releases_partial_reservations_on_budget_failure(prepared):
    paths, budget = prepared
    budget.limit = 0
    with pytest.raises(BudgetExceeded):
        submit_batch(paths[2], budget=budget, api_key='test-key')
    assert json.loads(paths[2].read_text())['submission']['state'] == 'not_submitted'
    assert ledger(budget) == []


def test_waterfront_qualification_retains_duplicate_and_fabricated_awards():
    root = Path(__file__).resolve().parents[2]
    base = root / 'docs/ground-truth/extraction-qualification'
    cohort = load_cohort(base / 'cohort.json')
    doc = next(d for d in cohort['documents'] if d['id'] == 'waterfront-2017-09')
    value = json.loads((root / 'scrapers/tests/fixtures/waterfront/2017-09.json').read_text())['result']
    original, errors = waterfront_relationships(doc, value, base)
    assert len(original) == 8 and not errors
    value['contracts'][0]['awards'].append(copy.deepcopy(value['contracts'][0]['awards'][0]))
    value['contracts'][0]['awards'].append({'supplier_name': 'Fabricated Co.', 'amount_raw': '$10'})
    observed, errors = waterfront_relationships(doc, value, base)
    metrics = score(doc['relations'], observed)
    assert len(observed) == 10 and len(metrics['duplicates']) == 1 and len(metrics['fabrications']) == 1
    assert len(errors) == 2


def test_changed_publication_review_is_distinct_from_malformed_model_output(monkeypatch):
    root = Path(__file__).resolve().parents[2] / 'docs/ground-truth/extraction-qualification'
    def changed(*args, **kwargs):
        raise ValueError('reviewed source context changed')
    monkeypatch.setattr('toronto_bids.sources.waterfront.verified_claims', changed)
    report = qualification_report(root / 'cohort.json', [root / 'runs/2026-10-23-high-full.json'])
    row = next(r for r in report['runs'][0]['documents'] if r['document'] == 'waterfront-2017-09')
    assert row['state'] == 'source_review_required'
    assert row['reason'] == 'reviewed source context changed'


def test_published_paired_campaign_is_complete_and_reproducible():
    root = Path(__file__).resolve().parents[2] / 'docs/ground-truth/extraction-qualification'
    campaign = root / 'campaigns/2026-11-13'
    paths = sorted((campaign / 'runs').glob('*.json'))
    report = qualification_report(root / 'cohort.json', paths)
    assert report == json.loads((campaign / 'report.json').read_text())
    rows = [d for run in report['runs'] for d in run['documents'] if d['state'] != 'not_selected']
    assert len(rows) == 48 and len({d['provenance']['response_id'] for d in rows}) == 48
    assert all(not d['provenance_issues'] for d in rows)
    assert all(d['covered'] for c in report['corpora'].values() for d in c['comparisons'])
    assert sum(d['state'] == 'source_review_required' for d in rows) == 4
    assert report['usage']['known_charged_microdollars'] == 670469
    assert report['usage']['attempts_without_charge'] == 0
    assert not report['qualified']
    frozen = qualification_report(root / 'cohort-2026-11-13-pretrial.json', paths)
    assert frozen == json.loads((campaign / 'report-pre-review.json').read_text())
