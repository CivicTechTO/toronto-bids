"""Reproduce the frozen follow-up measurements offline, from the repository root.

uv run --project scrapers python docs/ground-truth/extraction-qualification/followups/2026-11-15/summarize.py
"""
from collections import Counter
import copy
import json
from pathlib import Path

from toronto_bids.qualification import digest, load_cohort, qualification_report, _evaluate
from toronto_bids.qualification_comparison import _unpriced_checks

ROOT = Path(__file__).parent


def summarize(root=ROOT):
    root = Path(root)
    manifest = json.loads((root / 'campaign.manifest').read_text())
    cohort_path = root / 'cohort.json'
    if digest(cohort_path.read_bytes()) != manifest['cohort_sha256']:
        raise ValueError('Frozen source labels changed')
    cohort = load_cohort(cohort_path)
    docs = {d['id']: d for d in cohort['documents']}
    if set(manifest['held_out']) & set(manifest['regression']):
        raise ValueError('Holdout and regression partitions overlap')
    if set(manifest['held_out']) | set(manifest['regression']) != set(docs):
        raise ValueError('Partition membership changed')
    prior_path = root.parents[1] / 'cohort-comparison-2026-11-13.json'
    if digest(prior_path.read_bytes()) != manifest['prior_cohort_sha256']:
        raise ValueError('Prior comparison identity changed')
    prior = load_cohort(prior_path)
    if {docs[id]['text_sha256'] for id in manifest['held_out']} & {d['text_sha256'] for d in prior['documents']}:
        raise ValueError('Held-out source duplicates a prior input')
    paths = [root / p for p in manifest['run_paths']]
    prior_trial = json.loads((root.parents[1] / 'comparisons/2026-11-13/sonnet-clarified/2026-11-13-comparison-sonnet-clarified-sync-1.json').read_text())
    prior_profile = next(iter(prior_trial['attempts'].values()))['provenance']
    report = qualification_report(cohort_path, paths)
    by_run = {run['id']: run for run in report['runs']}
    rows = []
    profiles = set()
    for path in paths:
        run = json.loads(path.read_text())
        if set(run['attempts']) != set(docs):
            raise ValueError('Planned attempt membership changed')
        if run['cohort_sha256'] != manifest['cohort_sha256']:
            raise ValueError('Trial label identity changed')
        for row in by_run[run['id']]['documents']:
            attempt = run['attempts'][row['document']]
            p = attempt['provenance']
            if p.get('model') != manifest['model'] or p.get('effort') != manifest['effort']:
                raise ValueError('Model/effort differs from frozen plan')
            if (p.get('experimental_prompt') != prior_profile.get('experimental_prompt')
                    or p['schema_sha256'] != prior_profile['schema_sha256']):
                raise ValueError('Revised prompt/schema differs from preceding experiment')
            profiles.add((p['prompt_version'], p['schema_sha256']))
            rows.append(dict(run=run['id'], mode=run['mode'], repeat=run['repeat'], **row))
    if len(profiles) != 1 or not all(r.get('experimental_prompt') for r in rows):
        raise ValueError('Campaign must use one isolated experimental prompt/schema')

    def aggregate(selected):
        scored = [r for r in selected if r['state'] == 'success' and 'metrics' in r]
        exact = {k: sum(r['metrics'][k] for r in scored) for k in ('expected', 'observed', 'correct')}
        exact.update({k: sum(len(r['metrics'][k]) for r in scored) for k in
                      ('omissions', 'attribute_errors', 'false_joins', 'fabrications', 'duplicates')})
        exact['planned_expected'] = sum(len(docs[r['document']]['relations']) for r in selected)
        return dict(requests=len(selected), states=dict(Counter(r['state'] for r in selected)),
                    exact_relations=exact,
                    numeric_correct=sum(r['amount_value_metrics']['correct'] for r in scored),
                    exact_documents=sum(r.get('contracts_match', False) and r['metrics']['correct'] == r['metrics']['expected'] == r['metrics']['observed'] for r in scored),
                    provenance_issues=sum(len(r.get('provenance_issues', [])) for r in selected),
                    charged_microdollars=sum(r.get('charged_microdollars') or 0 for r in selected))

    partitions = {}
    for partition, key in [('held_out', 'held_out'), ('regression', 'regression')]:
        selected = [r for r in rows if r['document'] in manifest[key]]
        partitions[partition] = dict(aggregate(selected), by_mode={
            mode: aggregate([r for r in selected if r['mode'] == mode]) for mode in manifest['modes']})
    per_document = []
    for id, doc in docs.items():
        samples = [r for r in rows if r['document'] == id]
        successful = [r for r in samples if r['state'] == 'success']
        per_document.append(dict(document=id, partition=doc['evaluation_partition'],
            relationship_variants=len({r['response_identity'] for r in successful}),
            observations=[dict(mode=r['mode'], repeat=r['repeat'], state=r['state'],
                contracts_match=r.get('contracts_match'), metrics=r.get('metrics'),
                numeric_metrics=r.get('amount_value_metrics'),
                reference_normalizations=r.get('reference_normalizations'),
                supplier_normalizations=r.get('supplier_normalizations'),
                contract_extras=r.get('contract_extras'), contract_omissions=r.get('contract_omissions'),
                count_discrepancies=r.get('count_discrepancies')) for r in samples]))
    # Disclosed supplementary source equivalences never mutate frozen labels or
    # overwrite the primary report. Structural scope disagreements stay failures.
    reviewed_docs = copy.deepcopy(docs)
    reviews = {}
    reviews_path = root / 'source-reviews.json'
    if reviews_path.exists():
        reviews = json.loads(reviews_path.read_text())
        if reviews.get('version') != 1:
            raise ValueError('Unsupported source-review version')
        for field in ('reference_aliases', 'supplier_aliases'):
            for review in reviews.get(field, []):
                doc = reviewed_docs[review['document']]
                text = ' '.join((root / doc['text_path']).read_text().split())
                canonical_values = doc['contracts'] if field == 'reference_aliases' else [r['supplier'] for r in doc['relations']]
                if (review['canonical'] not in canonical_values or not review.get('evidence')
                    or ' '.join(review['alias'].split()) not in text
                    or any(' '.join(q.split()) not in text for q in review['evidence'])):
                    raise ValueError('Supplementary review is not source bound')
                if any(a['alias'] == review['alias'] for a in doc.get(field, [])):
                    raise ValueError('Duplicate supplementary alias')
                doc.setdefault(field, []).append(review)
    reviewed_rows = []
    protected = []
    for path in paths:
        run = json.loads(path.read_text())
        for id, attempt in run['attempts'].items():
            reviewed_rows.append(dict(mode=run['mode'], repeat=run['repeat'],
                **_evaluate(reviewed_docs[id], attempt, root)))
            if attempt['state'] == 'success':
                protected.extend(dict(mode=run['mode'], repeat=run['repeat'], **check)
                    for check in _unpriced_checks(docs[id], attempt, reviews.get('supplier_aliases', [])))
    reviewed_partitions = {}
    for partition in ('held_out', 'regression'):
        selected = [r for r in reviewed_rows if r['document'] in manifest[partition]]
        reviewed_partitions[partition] = dict(aggregate(selected), by_mode={
            mode: aggregate([r for r in selected if r['mode'] == mode]) for mode in manifest['modes']})
    comparisons = []
    for id in docs:
        selected = [r for r in reviewed_rows if r['document'] == id and r['state'] == 'success']
        signatures = {mode: {r['response_identity'] for r in selected if r['mode'] == mode} for mode in manifest['modes']}
        comparisons.append(dict(document=id, successful_observations=len(selected),
            relationship_variants=len(set.union(*signatures.values())),
            mode_variant_counts={mode: len(values) for mode, values in signatures.items()},
            observed_mode_sets_agree=bool(signatures['sync']) and signatures['sync'] == signatures['batch']))
    ids = [r['provenance'].get('response_id') for r in rows if r['state'] == 'success']
    summary = dict(campaign=manifest['id'], cohort_sha256=manifest['cohort_sha256'],
                   prompt_version=next(iter(profiles))[0], partitions=partitions,
                   total=aggregate(rows), unique_response_ids=len(set(ids)),
                   per_document=per_document, production_qualified=report['qualified'],
                   supplementary_source_reviewed=reviewed_partitions,
                   protected_unpriced_participations=protected,
                   supplementary_mode_comparisons=comparisons,
                   limitations=manifest['limitations'])
    return summary, report


if __name__ == '__main__':
    summary, report = summarize()
    for name, value in [('summary.json', summary), ('report.json', report)]:
        (ROOT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'per_document'}, indent=2))
