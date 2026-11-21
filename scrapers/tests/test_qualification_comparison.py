"""Controlled experimental changes and prompt provenance; no external requests."""
import copy
import json
from pathlib import Path

import httpx
import pytest

from toronto_bids.claude import DEFAULT_MODEL, _charge
from toronto_bids.extract import EXTRACTOR_VERSION, build_prompt
from toronto_bids.llm_budget import UsageBudget
from toronto_bids.qualification import digest, identity, qualification_report, request_prompt_prefix
from toronto_bids.qualification_comparison import (ARMS, CLARIFICATIONS, _unpriced_checks,
    comparison_request, comparison_summary, prepare_comparison)
from toronto_bids.qualification_trials import prepare_trials, run_sync


def test_each_arm_changes_one_factor_and_preserves_schema_and_source():
    text = 'A held source document.'
    original = comparison_request(text, ARMS[0])
    effort = comparison_request(text, ARMS[1])
    effort['output_config']['effort'] = original['output_config']['effort']
    assert identity(effort) == identity(original)
    model = comparison_request(text, ARMS[2])
    model['model'] = original['model']
    assert identity(model) == identity(original)
    prompt = comparison_request(text, ARMS[3])
    assert prompt['messages'][0]['content'][1]['text'] == text
    assert CLARIFICATIONS in prompt['messages'][0]['content'][0]['text']
    prompt['messages'][0]['content'][0]['text'] = original['messages'][0]['content'][0]['text']
    assert identity(prompt) == identity(original)
    assert DEFAULT_MODEL == 'claude-sonnet-5-5'
    assert original['messages'][0]['content'][0]['text'] == build_prompt('')


def test_opus_pricing_is_reviewed_opt_in():
    usage = dict(input_tokens=1_000_000, cache_creation_input_tokens=1_000_000,
                 cache_read_input_tokens=1_000_000, output_tokens=1_000_000)
    assert _charge('claude-opus-5-5', usage) == 29_200_000
    with pytest.raises(KeyError):
        _charge('unreviewed-model', usage)


def test_plan_is_balanced_and_cannot_overwrite_prior_evidence(tmp_path):
    cohort = Path(__file__).resolve().parents[2] / 'docs/ground-truth/extraction-qualification/cohort.json'
    manifest = prepare_comparison(cohort, tmp_path / 'comparison', campaign_id='test')
    assert manifest['requests'] == 80 and len(manifest['run_paths']) == 8
    assert manifest['mode'] == 'sync' and manifest['repeats'] == 2
    assert manifest['clarifications_sha256'] == digest(CLARIFICATIONS.encode())
    for relative in manifest['run_paths']:
        trial = json.loads((tmp_path / 'comparison' / relative).read_text())
        assert trial['mode'] == 'sync' and len(trial['attempts']) == 10
    with pytest.raises(FileExistsError):
        prepare_comparison(cohort, tmp_path / 'comparison', campaign_id='test')


def test_experimental_prompt_is_verifiable_but_never_production_qualification(tmp_path):
    text = 'No procurement contracts.'
    (tmp_path / 'source.txt').write_text(text)
    cohort = {'version': 1, 'thresholds': {'ep': {'precision': 1.0, 'recall': 1.0,
              'modes': ['sync', 'batch'], 'difficult_repeats': 2}}, 'documents': [
        {'id': 'negative', 'corpus': 'ep', 'difficult': True, 'text_path': 'source.txt',
         'text_sha256': digest(text.encode()), 'document_sha256': 'a' * 64,
         'review_status': 'reviewed', 'contracts': [], 'relations': [], 'review_notes': 'Negative control.'}]}
    cohort_path = tmp_path / 'cohort.json'
    cohort_path.write_text(json.dumps(cohort))
    paths = prepare_trials(cohort_path, tmp_path / 'runs', campaign_id='eval',
                           request_factory=lambda t: comparison_request(t, ARMS[3]))
    budget = UsageBudget(tmp_path / 'ledger.sqlite', '20')
    response = {'id': 'response-1', 'model': DEFAULT_MODEL, 'stop_reason': 'end_turn',
                'content': [{'type': 'text', 'text': '{"contracts": []}'}],
                'usage': {'input_tokens': 50, 'output_tokens': 10}}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response))) as http:
        result = run_sync(paths[0], budget=budget, api_key='test-key', http=http, timeout=300)
    sample = result['attempts']['negative']
    assert sample['provenance']['prompt_version'] != EXTRACTOR_VERSION
    assert request_prompt_prefix(sample['provenance']) != build_prompt('')
    clones = []
    for i, mode in enumerate(['sync', 'sync', 'batch', 'batch']):
        a = copy.deepcopy(sample)
        a['provenance']['mode'] = mode
        a['provenance']['response_id'] = f'response-{i}'
        a['raw_response']['id'] = f'response-{i}'
        if mode == 'batch': a['charged_microdollars'] //= 2
        path = tmp_path / f'clone-{i}.json'
        path.write_text(json.dumps({'id': f'clone-{i}', 'attempts': {'negative': a}}))
        clones.append(path)
    report = qualification_report(cohort_path, clones)
    assert all(not r['documents'][0]['provenance_issues'] for r in report['runs'])
    assert report['corpora']['ep']['comparisons'][0]['covered']
    assert not report['corpora']['ep']['qualified']
    p = copy.deepcopy(sample['provenance'])
    p['experimental_prompt']['prefix'] += 'changed'
    with pytest.raises(ValueError, match='identity changed'):
        request_prompt_prefix(p)


def test_published_comparison_reproduces_with_frozen_labels():
    root = Path(__file__).resolve().parents[2] / 'docs/ground-truth/extraction-qualification'
    comparison = root / 'comparisons/2026-11-13'
    summary = comparison_summary(root / 'cohort-comparison-2026-11-13.json', comparison)
    assert summary == json.loads((comparison / 'summary.json').read_text())
    assert summary['requests_complete'] == summary['requests_expected'] == 80
    assert summary['charged_microdollars'] == 2_058_287
    assert not summary['production_qualification']
    results = {a['arm']['id']: a for a in summary['arms']}
    assert [results[id]['exact_relations']['correct'] for id in
            ['sonnet-high', 'sonnet-xhigh', 'opus-high', 'sonnet-clarified']] == [121, 139, 138, 164]
    assert all(a['exact_relations']['expected'] == 166 for a in summary['arms'])
    assert all(a['negative_controls_passed'] == a['positive_controls_passed'] == 2 for a in summary['arms'])
    assert all(not a['provenance_issues'] and not a['unknown_charges'] for a in summary['arms'])
    assert results['sonnet-clarified']['protected_unpriced']['correctly_unpriced'] == 10
    assert all(results[id]['protected_unpriced']['unsupported_priced_observations'] == 10
               for id in ['sonnet-xhigh', 'opus-high'])


def test_unpublished_supplier_alias_cannot_be_used_for_component_credit(tmp_path):
    cohort = Path(__file__).resolve().parents[2] / 'docs/ground-truth/extraction-qualification/cohort-comparison-2026-11-13.json'
    manifest = {'id': 'invalid-review', 'source_cohort_sha256': digest(cohort.read_bytes()),
                'arms': [], 'documents': [], 'run_paths': [], 'scope': 'test', 'requests': 0}
    (tmp_path / 'comparison.manifest').write_text(json.dumps(manifest))
    (tmp_path / 'component-reviews.json').write_text(json.dumps({'version': 1, 'supplier_aliases': [
        {'document': 'zoo-fence', 'alias': 'Unpublished Vendor', 'canonical': 'Imperial Fence Inc.',
         'evidence': ['Imperial Fence, Inc.']}]}))
    with pytest.raises(ValueError, match='not source bound'):
        comparison_summary(cohort, tmp_path)


def test_returning_a_name_with_an_award_price_does_not_repair_unpriced_participation():
    root = Path(__file__).resolve().parents[2] / 'docs/ground-truth/extraction-qualification'
    doc = next(d for d in json.loads((root/'cohort-comparison-2026-11-13.json').read_text())['documents']
               if d['id'] == 'composite')
    attempt = {'extraction': {'contracts': [
        {'reference': r['contract'], 'bids': [{'supplier_name': r['supplier'], 'amount_raw': '$100'}]}
        for r in doc['relations'] if r['role'] == 'bid']}}
    checks = _unpriced_checks(doc, attempt, [])
    assert len(checks) == 4 and all(c['named_participation_present'] for c in checks)
    assert not any(c['correctly_unpriced'] for c in checks)
    assert sum(c['unsupported_priced_observations'] for c in checks) == 4
