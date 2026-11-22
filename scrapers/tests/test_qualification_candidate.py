"""Offline tests for the separately versioned candidate and evidence-backed validation (#302)."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from toronto_bids.claude import EXTRACTION_SCHEMA
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.qualification import digest, identity, load_cohort
from toronto_bids.qualification_candidate import (
    CANDIDATE_SCHEMA,
    candidate_request,
    prepare_candidate_campaign,
    summarize_candidate_campaign,
    validate_candidate_extraction,
)


REPO = Path(__file__).resolve().parents[2]
QUAL_ROOT = REPO / 'docs/ground-truth/extraction-qualification'
CAMPAIGN_22 = QUAL_ROOT / 'followups/2026-11-22'


def _load_summarize_22():
    spec = importlib.util.spec_from_file_location('summarize_2026_11_22', CAMPAIGN_22 / 'summarize.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_schema_and_request_isolated_from_production():
    assert CANDIDATE_SCHEMA != EXTRACTION_SCHEMA
    bid_props = CANDIDATE_SCHEMA['properties']['contracts']['items']['properties']['bids']['items']['properties']
    assert 'amount_evidence' in bid_props
    prod_bid_props = EXTRACTION_SCHEMA['properties']['contracts']['items']['properties']['bids']['items']['properties']
    assert 'amount_evidence' not in prod_bid_props

    req = candidate_request('Sample text')
    assert req['output_config']['format']['schema'] == CANDIDATE_SCHEMA
    assert req['model'] == 'claude-sonnet-5-5'
    assert req['output_config']['effort'] == 'high'
    prefix = req['messages'][0]['content'][0]['text']
    assert 'amount_evidence' in prefix
    assert 'strictly local' in prefix
    assert EXTRACTOR_VERSION.startswith('v1-')


def test_validate_submitted_price_evidence_rules():
    source = (
        '1. Acme Corp. $125,000.00 (plus HST)\n'
        'Authorize agreement with Imperial Fence, Inc. for up to $1,639,615.00 (excluding HST), '
        'the lowest bid among all four received.'
    )
    raw = {
        'contracts': [{
            'reference': 'RFT-1',
            'stage': 'single_stage',
            'prequalified': [],
            'invited_to_tender': [],
            'bids': [
                # Grounded bid price -> kept
                {
                    'supplier_name': 'Acme Corp.',
                    'amount_raw': '$125,000.00',
                    'amount_basis': 'plus_HST',
                    'status': 'compliant',
                    'rank': 1,
                    'amount_evidence': '1. Acme Corp. $125,000.00 (plus HST)',
                },
                # Missing evidence -> nulled
                {
                    'supplier_name': 'Missing Evidence Co.',
                    'amount_raw': '$125,000.00',
                    'amount_basis': 'plus_HST',
                    'status': 'not_stated',
                    'rank': None,
                    'amount_evidence': None,
                },
                # Non-verbatim evidence -> nulled
                {
                    'supplier_name': 'Fabricated Quote Co.',
                    'amount_raw': '$999,999.00',
                    'amount_basis': 'plus_HST',
                    'status': 'not_stated',
                    'rank': None,
                    'amount_evidence': 'Fabricated Quote Co. bid $999,999.00',
                },
                # Real Zoo fence failure pattern: award ceiling copied into bid price -> nulled
                {
                    'supplier_name': 'Imperial Fence, Inc.',
                    'amount_raw': '$1,639,615.00',
                    'amount_basis': 'plus_HST',
                    'status': 'compliant',
                    'rank': 1,
                    'amount_evidence': 'for up to $1,639,615.00 (excluding HST), the lowest bid among all four received.',
                },
            ],
            'awards': [{
                'supplier_name': 'Imperial Fence, Inc.',
                'amount_raw': '$1,639,615.00',
                'amount_basis': 'plus_HST',
                'contingency_raw': None,
                'value_confidential': False,
            }],
        }]
    }
    raw_copy = copy.deepcopy(raw)
    validated, interventions = validate_candidate_extraction(raw, source)
    assert raw == raw_copy  # never mutates input

    bids = {b['supplier_name']: b for b in validated['contracts'][0]['bids']}
    assert bids['Acme Corp.']['amount_raw'] == '$125,000.00'
    assert bids['Acme Corp.']['amount_basis'] == 'plus_HST'
    for nulled in ('Missing Evidence Co.', 'Fabricated Quote Co.', 'Imperial Fence, Inc.'):
        assert bids[nulled]['amount_raw'] is None
        assert bids[nulled]['amount_basis'] is None
        assert bids[nulled]['amount_evidence'] is None
    assert len(interventions) == 3
    assert all(i['rule'] == 'submitted_price_evidence' for i in interventions)


def test_validate_prequalified_only_membership_and_empty_stub():
    # Reproduces both TRCA D01 batch-repeat-2 overlap and Gardiner empty unnumbered stub.
    raw = {
        'contracts': [
            {
                'reference': 'RFT No. 10040951',
                'stage': 'prequalification_then_tender',
                'prequalified': [
                    'Buildscapes Construction Ltd.',
                    '560789 Ontario Limited o/a R&M Construction',
                    '2220742 Ontario Ltd o/a Bronte Construction',
                ],
                'invited_to_tender': [
                    '2220742 Ontario Ltd o/a Bronte Construction',
                    '560789 Ontario Limited o/a R&M Construction',
                    'Dynex Construction Inc.',
                ],
                'bids': [
                    {
                        'supplier_name': 'Dynex Construction Inc.',
                        'amount_raw': None,
                        'amount_basis': None,
                        'status': 'compliant',
                        'rank': 1,
                        'amount_evidence': None,
                    }
                ],
                'awards': [],
            },
            {
                'reference': None,
                'title': 'Gardiner Expressway Design-Build (ongoing)',
                'stage': 'single_stage',
                'declared_submissions': None,
                'prequalified': [],
                'invited_to_tender': [],
                'bids': [],
                'awards': [],
            },
        ]
    }
    validated, interventions = validate_candidate_extraction(raw, 'source text')
    assert len(validated['contracts']) == 1
    assert validated['contracts'][0]['prequalified'] == ['Buildscapes Construction Ltd.']
    rules = [i['rule'] for i in interventions]
    assert rules.count('prequalified_only_membership') == 2
    assert rules.count('ongoing_procurement_empty_stub') == 1


def test_summarize_compares_prompt_only_vs_evidence_validated(tmp_path):
    # Copy minimal two-document cohort (1 held_out_new_corpora + 1 regression) into tmp_path
    src_dir = tmp_path / 'sources'
    src_dir.mkdir()
    zoo_text = (CAMPAIGN_22 / 'sources/zoo-fence.txt').read_text()
    gardiner_text = (CAMPAIGN_22 / 'sources/held-ep-status.txt').read_text()
    (src_dir / 'zoo-fence.txt').write_text(zoo_text)
    (src_dir / 'held-ep-status.txt').write_text(gardiner_text)

    base_cohort = json.loads((CAMPAIGN_22 / 'cohort.json').read_text())
    by_id = {d['id']: d for d in base_cohort['documents']}
    zoo_doc = copy.deepcopy(by_id['zoo-fence'])
    zoo_doc['evaluation_partition'] = 'held_out_new_corpora'
    zoo_doc['supplier_aliases'] = [{
        'alias': 'Imperial Fence, Inc.',
        'canonical': 'Imperial Fence Inc.',
        'evidence': ['Imperial Fence, Inc. (of Scotland, Ontario)', 'Imperial Fence Inc.'],
    }]
    gardiner_doc = copy.deepcopy(by_id['held-ep-status'])
    gardiner_doc['evaluation_partition'] = 'regression'

    cohort_path = tmp_path / 'cohort.json'
    cohort_path.write_text(json.dumps({
        'version': 1,
        'thresholds': base_cohort['thresholds'],
        'documents': [zoo_doc, gardiner_doc],
    }, indent=2))

    manifest = prepare_candidate_campaign(cohort_path, tmp_path, campaign_id='test-cand')
    # Populate sync-1 with raw responses exhibiting both Zoo fence price copy and Gardiner stub
    sync1_path = tmp_path / manifest['run_paths'][0]
    trial = json.loads(sync1_path.read_text())

    zoo_ext = {
        'contracts': [{
            'reference': 'RFT-42',
            'solicitation_type': 'RFT',
            'title': 'Perimeter Fence',
            'buyer': 'Toronto Zoo',
            'posted_date': None,
            'closed_date': None,
            'documents_taken': None,
            'declared_submissions': 4,
            'declared_compliant': None,
            'stage': 'single_stage',
            'prequalified': [],
            'invited_to_tender': [],
            'bids': [{
                'supplier_name': 'Imperial Fence Inc.',
                'amount_raw': '$1,639,615.00',
                'amount_basis': 'plus_HST',
                'status': 'compliant',
                'rank': 1,
                'amount_evidence': 'for up to $1,639,615.00 (excluding HST), the lowest bid among all four received',
            }],
            'awards': [{
                'supplier_name': 'Imperial Fence, Inc.',
                'amount_raw': '$1,639,615.00',
                'amount_basis': 'plus_HST',
                'contingency_raw': None,
                'value_confidential': False,
            }],
            'funding_source': None,
            'decision_date': None,
            'decision_body': 'Board of Management of the Toronto Zoo',
        }]
    }
    gardiner_ext = {
        'contracts': [{
            'reference': None,
            'solicitation_type': 'unknown',
            'title': 'Gardiner Design-Build',
            'buyer': 'City of Toronto',
            'posted_date': None,
            'closed_date': None,
            'documents_taken': None,
            'declared_submissions': None,
            'declared_compliant': None,
            'stage': 'single_stage',
            'prequalified': [],
            'invited_to_tender': [],
            'bids': [],
            'awards': [],
            'funding_source': None,
            'decision_date': None,
            'decision_body': None,
        }]
    }

    for doc_id, ext, resp_id in [('zoo-fence', zoo_ext, 'msg_1'), ('held-ep-status', gardiner_ext, 'msg_2')]:
        attempt = trial['attempts'][doc_id]
        raw_resp = {
            'id': resp_id,
            'model': 'claude-sonnet-5-5',
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 100, 'output_tokens': 50},
            'content': [{'type': 'text', 'text': json.dumps(ext)}],
        }
        attempt.update(
            state='success', dispatch='received', reason=None,
            extraction=ext, raw_response=raw_resp,
            usage=raw_resp['usage'], charged_microdollars=700,
        )
        attempt['provenance'].update(response_id=resp_id, response_sha256=identity(ext))

    sync1_path.write_text(json.dumps(trial, indent=2))
    summary = summarize_candidate_campaign(tmp_path)

    # Prompt-only fails both Zoo fence (1/2) and Gardiner (0/1 exact_documents);
    # evidence-backed validation fixes both to 2/2 exact_relations and 2/2 exact_documents.
    assert summary['prompt_only']['total']['exact_relations']['correct'] == 1
    assert summary['prompt_only']['total']['exact_documents'] == 0
    assert summary['evidence_validated']['total']['exact_relations']['correct'] == 2
    assert summary['evidence_validated']['total']['exact_documents'] == 2
    assert len(summary['validation_interventions']) == 2
    assert summary['production_qualified'] is False


def test_2026_11_22_campaign_reproducible_and_disjoint():
    cohort = load_cohort(CAMPAIGN_22 / 'cohort.json')
    held_out = [d for d in cohort['documents'] if d['evaluation_partition'] == 'held_out_new_corpora']
    assert {d['corpus'] for d in held_out} == {
        'award_summary', 'ba_report', 'bd_report', 'committee', 'composite', 'ttc', 'waterfront'
    }
    assert len(cohort['documents']) == 18

    prior_hashes = set()
    for p in (
        QUAL_ROOT / 'cohort.json',
        QUAL_ROOT / 'cohort-comparison-2026-11-13.json',
        QUAL_ROOT / 'followups/2026-11-15/cohort.json',
    ):
        prior_hashes.update(d['text_sha256'] for d in load_cohort(p)['documents'])
    assert not ({d['text_sha256'] for d in held_out} & prior_hashes)

    mod = _load_summarize_22()
    reproduced = mod.summarize()
    committed = json.loads((CAMPAIGN_22 / 'summary.json').read_text())
    assert reproduced == committed
    assert committed['prompt_only']['total']['requests'] == 72
    assert committed['prompt_only']['total']['states'] == {'pending': 72}
    assert committed['prompt_only']['total']['charged_microdollars'] == 0
    assert committed['production_qualified'] is False
