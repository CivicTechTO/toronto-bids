"""Frozen model/effort/prompt comparison, isolated from production extraction.

The clarifications are general source-role rules, without supplier names, call
numbers or target prices. They extend the existing prompt only in trial requests.
"""

from collections import Counter
from pathlib import Path
import json

from toronto_bids.claude import extraction_request
from toronto_bids.extract import EXTRACTOR_VERSION, build_prompt
from toronto_bids.qualification import digest, load_cohort, qualification_report
from toronto_bids.qualification_trials import prepare_trials


DOCUMENTS = ('award-summary', 'bd-report', 'ep-bus-duct', 'ep-negative', 'zoo-fence',
             'composite', 'committee', 'trca-D01', 'trca-D02', 'trca-D04')

CLARIFICATIONS = """SOURCE-RELATION CLARIFICATIONS FOR THIS EVALUATION:

Read every contract, table, appendix and procurement narrative. Preserve all
source-supported relationships, even when the document has no bidder-price table.

1. Explicit competitive participation belongs in bids even when no submitted price
is published. A named submitted tenderer, lowest acceptable bidder, or highest
scoring qualified proponent is a bidder. A recommended winner in a document that
explicitly establishes competitive bidding and that firm's submitted participation
must have a bid with amount_raw null if its tender price is absent. Merely naming
an award recipient, historical supplier, consultant or sole-source firm is not
enough. Do not manufacture the other bidder names to meet a declared count.

2. Keep each amount in its published role. A submitted price is not an award value.
An award allocation, combined ceiling, contingency, option-year total, annual
service-spending forecast, funding budget or estimated demand is not a submitted
price. Unallocated forecasts/shared limits are not individual award values either.
Use null for an unreported role-specific amount. Choose the stated initial-term
award rather than an optional-renewal or total-potential figure. Keep explicit
per-supplier allocations distinct from whole-tender prices and shared limits.

3. Read tax qualifiers in column headings and narrative, not only beside a number.
Use plus_HST for plus/excluding/excl. HST; net_of_taxes only for explicitly net of
all applicable taxes; including_HST only when HST inclusion is explicit. A generic
including applicable taxes statement does not establish HST: use unknown. Apply
the appropriate source heading to each amount, including an unpriced bidder only
when that heading actually applies. Never infer a basis from another amount.

4. A stated finding that a submitted bidder meets specifications/requirements or
is acceptable supports compliant. Use not_stated where no status is supported.
Keep a category-specific disqualification attached to that participation only.
Do not mark every listed firm compliant just because the contract has a winner.

5. The prequalified array means prequalification-ONLY firms: exclude firms that
proceeded to invitation/tender participation. invited_to_tender retains every
explicitly invited firm, including those that submitted. Their actual submissions
also belong in bids. declared_submissions describes the tender/proposal stage for
the contract, not a preceding prequalification count or document-download count.

6. Separate service-category participations are separate bid/award entries even
when a supplier, reference and price repeat. Preserve their different statuses
and unpriced memberships. The schema has no category field: do not add fields or
invent modified references to encode a category. Do not collapse genuine repeated
source relationships into one supplier entry.

7. Keep names and dollar strings as printed, but exclude an explicitly identified
table footnote marker from the supplier name and monetary amount itself. Retain
the actual currency spelling, separators and spacing. A footnote is not a new
supplier or another amount. Do not normalize legal suffixes or identifiers.

Before returning the required JSON, check every named supported participant and
each bid/award amount against its contract, stage, role, tax heading and status.
Preserve negative documents as contracts: [] and unknown facts as null/unknown;
never turn a missing source fact into an invented recipient or submitted price.

"""

ARMS = (
    {'id': 'sonnet-high', 'model': 'claude-sonnet-5-5', 'effort': 'high', 'clarified': False},
    {'id': 'sonnet-xhigh', 'model': 'claude-sonnet-5-5', 'effort': 'xhigh', 'clarified': False},
    {'id': 'opus-high', 'model': 'claude-opus-5-5', 'effort': 'high', 'clarified': False},
    {'id': 'sonnet-clarified', 'model': 'claude-sonnet-5-5', 'effort': 'high', 'clarified': True},
)


def comparison_request(text, arm):
    request = extraction_request(text, model=arm['model'], effort=arm['effort'])
    if arm['clarified']:
        marker = 'DOCUMENT TEXT:\n\n'
        original = build_prompt('')
        assert original.endswith(marker)
        request['messages'][0]['content'][0]['text'] = original.removesuffix(marker) + CLARIFICATIONS + marker
    return request


def prepare_comparison(cohort_path, output_dir, *, campaign_id):
    """Freeze one-factor-at-a-time arms and two observations per selected input."""
    cohort_path, output_dir = Path(cohort_path), Path(output_dir)
    cohort = load_cohort(cohort_path)
    by_id = {d['id']: d for d in cohort['documents']}
    if set(DOCUMENTS) - by_id.keys():
        raise ValueError('Comparison cohort lacks predeclared documents')
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = {**cohort, 'documents': [by_id[id] for id in DOCUMENTS]}
    # All inputs, including controls, get two repeats for a balanced comparison.
    for doc in selected['documents']:
        doc['difficult'] = True
        doc['text_path'] = str((cohort_path.parent / doc['text_path']).resolve())
        for supplement in doc.get('additional_evidence', []):
            supplement['text_path'] = str((cohort_path.parent / supplement['text_path']).resolve())
    selected_path = output_dir / 'selected.cohort'
    with selected_path.open('x') as stream:
        stream.write(json.dumps(selected, indent=2, ensure_ascii=False) + '\n')
    run_paths = []
    for arm in ARMS:
        paths = prepare_trials(selected_path, output_dir / arm['id'], campaign_id=campaign_id + '-' + arm['id'],
                               request_factory=lambda text, a=arm: comparison_request(text, a))
        for path in paths:
            trial = json.loads(path.read_text())
            if trial['mode'] == 'batch':
                # This experiment holds execution mode constant. These offline
                # manifests are retained but are deliberately never submitted.
                for attempt in trial['attempts'].values():
                    attempt.update(state='skipped', dispatch='not_selected', charged_microdollars=0,
                                   reason='Mode held synchronous in this comparison')
                path.write_text(json.dumps(trial, indent=2, ensure_ascii=False) + '\n')
                continue
            trial['comparison_arm'] = arm
            path.write_text(json.dumps(trial, indent=2, ensure_ascii=False) + '\n')
            run_paths.append(path)
    manifest = {'id': campaign_id, 'base_prompt_version': EXTRACTOR_VERSION,
                'source_cohort_sha256': digest(cohort_path.read_bytes()), 'documents': list(DOCUMENTS),
                'arms': list(ARMS), 'mode': 'sync', 'repeats': 2, 'requests': len(DOCUMENTS) * len(ARMS) * 2,
                'clarifications': CLARIFICATIONS, 'clarifications_sha256': digest(CLARIFICATIONS.encode()),
                'run_paths': [str(p.relative_to(output_dir)) for p in run_paths],
                'scope': 'Known failure cases and two regression controls; no held-out accuracy claim'}
    with (output_dir / 'comparison.manifest').open('x') as stream:
        stream.write(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    return manifest


def _normal_name(value):
    return ' '.join(value.replace('’', "'").split()).casefold()


def _unpriced_checks(doc, attempt, supplier_reviews):
    """Protect the five independently reviewed unpriced participation facts.

    The Zoo's two printed spellings are a source-supported alternative, recorded
    here explicitly without changing any frozen exact-match labels or producer.
    """
    if doc['id'] not in ('composite', 'zoo-fence'):
        return []
    aliases = {a['alias']: a['canonical'] for a in doc.get('reference_aliases', [])}
    checks = []
    for expected in (r for r in doc['relations'] if r['role'] == 'bid'):
        assert expected['amount'] is None
        names = {_normal_name(expected['supplier'])}
        names.update(_normal_name(a['alias']) for a in supplier_reviews
                     if a['document'] == doc['id'] and a['canonical'] == expected['supplier'])
        candidates = [b for c in attempt.get('extraction', {}).get('contracts', [])
                      if aliases.get(c.get('reference'), c.get('reference')) == expected['contract']
                      for b in c.get('bids', []) if _normal_name(b['supplier_name']) in names]
        checks.append({'document': doc['id'], 'supplier': expected['supplier'], 'contract': expected['contract'],
                       'named_participation_present': bool(candidates),
                       'correctly_unpriced': bool(candidates) and all(b.get('amount_raw') is None for b in candidates),
                       'unsupported_priced_observations': sum(b.get('amount_raw') is not None for b in candidates)})
    return checks


def comparison_summary(cohort_path, comparison_dir):
    comparison_dir = Path(comparison_dir)
    manifest = json.loads((comparison_dir / 'comparison.manifest').read_text())
    if digest(Path(cohort_path).read_bytes()) != manifest['source_cohort_sha256']:
        raise ValueError('Comparison labels changed after preparation; use the frozen cohort')
    cohort = load_cohort(cohort_path)
    docs = {d['id']: d for d in cohort['documents']}
    reviews_path = comparison_dir / 'component-reviews.json'
    supplier_reviews = []
    if reviews_path.exists():
        reviews = json.loads(reviews_path.read_text())
        if reviews.get('version') != 1:
            raise ValueError('Unsupported component-review version')
        supplier_reviews = reviews['supplier_aliases']
        if len({(r['document'], _normal_name(r['alias'])) for r in supplier_reviews}) != len(supplier_reviews):
            raise ValueError('Ambiguous component supplier review')
        for review in supplier_reviews:
            doc = docs[review['document']]
            source = ' '.join((Path(cohort_path).parent / doc['text_path']).read_text().split())
            if (review['canonical'] not in {r['supplier'] for r in doc['relations']}
                    or ' '.join(review['alias'].split()) not in source
                    or not review.get('evidence') or any(' '.join(q.split()) not in source for q in review['evidence'])):
                raise ValueError('Component supplier review is not source bound')
    arms = []
    for arm in manifest['arms']:
        paths = [comparison_dir / p for p in manifest['run_paths']
                 if Path(p).parts[0] == arm['id']]
        report = qualification_report(cohort_path, paths)
        rows = [r for run in report['runs'] for r in run['documents'] if r['document'] in manifest['documents']]
        metrics = [r['metrics'] for r in rows if r['state'] == 'success']
        exact = {k: sum(m[k] for m in metrics) for k in ('expected', 'observed', 'correct')}
        exact.update({k: sum(len(m[k]) for m in metrics) for k in
                      ('omissions', 'false_joins', 'attribute_errors', 'fabrications', 'duplicates')})
        per_doc = []
        for id in manifest['documents']:
            samples = [r for r in rows if r['document'] == id]
            per_doc.append({'document': id, 'scope_complete': docs[id].get('scope_complete', True),
                            'observations': [{'state': r['state'], 'correct': r.get('metrics', {}).get('correct'),
                                              'expected': r.get('metrics', {}).get('expected'),
                                              'observed': r.get('metrics', {}).get('observed'),
                                              'numeric_correct': r.get('amount_value_metrics', {}).get('correct')}
                                             for r in samples],
                            'relationship_variants': len({r['response_identity'] for r in samples
                                                          if 'response_identity' in r})})
        unpriced = []
        for path in paths:
            run = json.loads(path.read_text())
            for id, attempt in run['attempts'].items():
                for check in _unpriced_checks(docs[id], attempt, supplier_reviews):
                    unpriced.append({'run': run['id'], **check})
        negative = [r for r in rows if r['document'] == 'ep-negative']
        positive = [r for r in rows if r['document'] == 'bd-report']
        arms.append({'arm': arm, 'requests': len(rows), 'states': dict(Counter(r['state'] for r in rows)),
                     'provenance_issues': [{'document': r['document'], 'issues': r['provenance_issues']}
                                           for r in rows if r.get('provenance_issues')],
                     'exact_relations': exact,
                     'numeric_correct': sum(r['amount_value_metrics']['correct'] for r in rows if r['state'] == 'success'),
                     'exact_document_observations': sum(r['state'] == 'success' and r.get('contracts_match', False)
                        and r['metrics']['correct'] == r['metrics']['expected'] == r['metrics']['observed'] for r in rows),
                     'negative_controls_passed': sum(r.get('contracts_match', False)
                        and r.get('metrics', {}).get('observed') == 0 for r in negative),
                     'positive_controls_passed': sum(r.get('contracts_match', False)
                        and r['metrics']['correct'] == r['metrics']['expected'] == r['metrics']['observed'] for r in positive),
                     'protected_unpriced': {'expected': len(unpriced),
                        'named_participations_present': sum(c['named_participation_present'] for c in unpriced),
                        'correctly_unpriced': sum(c['correctly_unpriced'] for c in unpriced),
                        'unsupported_priced_observations': sum(c['unsupported_priced_observations'] for c in unpriced),
                        'observations': unpriced},
                     'charged_microdollars': report['usage']['known_charged_microdollars'],
                     'unknown_charges': report['usage']['attempts_without_charge'], 'documents': per_doc})
    return {'campaign': manifest['id'], 'cohort_sha256': manifest['source_cohort_sha256'],
            'component_reviews_sha256': digest(reviews_path.read_bytes()) if reviews_path.exists() else None,
            'scope': manifest['scope'], 'requests_expected': manifest['requests'],
            'requests_complete': sum(a['states'].get('success', 0) for a in arms),
            'charged_microdollars': sum(a['charged_microdollars'] for a in arms),
            'arms': arms, 'production_qualification': False,
            'limitations': ['Known failure cases informed prompt design; not held-out accuracy.',
                            'All execution is synchronous; no new batch parity claim.',
                            'Category fields and source/identity proof gaps are unchanged.',
                            'Strict labels retain the Zoo punctuation mismatch; separate unpriced checks accept both printed forms.']}
