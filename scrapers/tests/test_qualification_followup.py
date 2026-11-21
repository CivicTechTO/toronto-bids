"""The held-out campaign must remain reproducible and expose repeat failures."""
import json
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[2] / 'docs/ground-truth/extraction-qualification/followups/2026-11-15'


def loader():
    return runpy.run_path(str(ROOT / 'summarize.py'))['summarize']


def test_frozen_holdout_plan_and_candidate_prompt():
    summary, report = loader()()
    assert summary['total']['requests'] == 68
    assert summary['partitions']['held_out']['requests'] == 28
    assert summary['partitions']['regression']['requests'] == 40
    assert summary['partitions']['held_out']['exact_relations']['planned_expected'] == 88
    assert summary['partitions']['regression']['exact_relations']['planned_expected'] == 332
    assert summary['total']['provenance_issues'] == 0
    assert summary['total']['states'] == {'success': 68}
    assert summary['unique_response_ids'] == 68
    assert summary == json.loads((ROOT / 'summary.json').read_text())
    assert summary['prompt_version'] == 'eval-8a5ebb8c14aa'
    assert not summary['production_qualified']
    assert not report['qualified']


def test_retrospective_equivalence_does_not_rewrite_primary_scores():
    summary, _ = loader()()
    primary = summary['partitions']['held_out']['exact_relations']
    reviewed = summary['supplementary_source_reviewed']['held_out']['exact_relations']
    assert reviewed['correct'] > primary['correct']
    assert reviewed['planned_expected'] == primary['planned_expected']
    # The empty ongoing-procurement contract stays a structural mismatch.
    assert summary['supplementary_source_reviewed']['held_out']['exact_documents'] < summary['partitions']['held_out']['requests']
    # At least one observed repeat assigns an unpublished tender price.
    checks = summary['protected_unpriced_participations']
    assert any(c['unsupported_priced_observations'] > 0 for c in checks)


def test_supplementary_alias_must_be_printed_in_source(monkeypatch):
    original = Path.read_text

    def altered(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        if path == ROOT / 'source-reviews.json':
            value = json.loads(text)
            value['reference_aliases'][0]['alias'] = 'unprinted fabricated identifier'
            return json.dumps(value)
        return text

    monkeypatch.setattr(Path, 'read_text', altered)
    with pytest.raises(ValueError, match='not source bound'):
        loader()()


def test_primary_labels_cannot_change_after_preparation(monkeypatch):
    original = Path.read_bytes

    def altered(path):
        raw = original(path)
        return raw + b' ' if path == ROOT / 'cohort.json' else raw

    monkeypatch.setattr(Path, 'read_bytes', altered)
    with pytest.raises(ValueError, match='Frozen source labels changed'):
        loader()()
