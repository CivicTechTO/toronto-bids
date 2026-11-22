"""Separately versioned extraction candidate and evidence-backed validation (#302).

Isolated from production extraction: never modifies EXTRACTOR_VERSION,
EXTRACTION_SCHEMA, production caches, or archived facts.

Each candidate request adds a verbatim `amount_evidence` field on bids and
incorporates the settled #302 policy rules (strictly local tax basis and empty
contract arrays for ongoing-procurement status updates). Offline scoring
compares raw prompt-only outputs against deterministic evidence-backed
validation on the exact same responses.
"""

from collections import Counter
import copy
import json
import os
from pathlib import Path
import re

from toronto_bids.claude import DEFAULT_MODEL, EXTRACTION_SCHEMA, extraction_request
from toronto_bids.extract import EXTRACTOR_VERSION, build_prompt, validate_extraction
from toronto_bids.qualification import _evaluate, _text, digest, identity, load_cohort, qualification_report
from toronto_bids.qualification_comparison import _normal_name, _unpriced_checks
from toronto_bids.qualification_trials import prepare_trials


_AWARD_CEILING_RE = re.compile(
    r'\b(?:for\s+up\s+to|up\s+to|upset\s+limit(?:\s+of|\s+in\s+the\s+amount\s+of)?)\s+',
    re.IGNORECASE,
)


def _build_candidate_schema():
    schema = copy.deepcopy(EXTRACTION_SCHEMA)
    bid_schema = schema['properties']['contracts']['items']['properties']['bids']['items']
    bid_schema['properties']['amount_evidence'] = {'type': ['string', 'null']}
    bid_schema['required'] = list(bid_schema['properties'])
    return schema


CANDIDATE_SCHEMA = _build_candidate_schema()

CANDIDATE_CLARIFICATIONS = """SOURCE-RELATION CLARIFICATIONS FOR THIS EVALUATION:

Read every contract, table, appendix and procurement narrative. Preserve all
source-supported relationships, even when the document has no bidder-price table.

1. Explicit competitive participation belongs in bids even when no submitted price
is published. A named submitted tenderer, lowest acceptable bidder, or highest
scoring qualified proponent is a bidder. A recommended winner in a document that
explicitly establishes competitive bidding and that firm's submitted participation
must have a bid with amount_raw null if its tender price is absent. Merely naming
an award recipient, historical supplier, consultant or sole-source firm is not
enough. Do not manufacture the other bidder names to meet a declared count.

2. Keep each amount in its published role and ground every submitted bid price in
literal source evidence. A submitted price is not an award value. An award
allocation, upset limit, combined ceiling, contingency, option-year total, annual
service-spending forecast, funding budget or estimated demand is not a submitted
price. Unallocated forecasts/shared limits are not individual award values either.
For every entry in bids:
- If amount_raw is non-null, set amount_evidence to the verbatim literal passage
  from the document text containing the exact amount_raw string and establishing
  it as that bidder's submitted bid/tender price (not an award or upset limit).
- If no submitted bid price is published for that bidder, set BOTH amount_raw and
  amount_evidence to null. Never copy an award value or "for up to" award ceiling
  into bids[].amount_raw unless the document explicitly states that exact figure
  as the bidder's submitted bid price.
Choose the stated initial-term award rather than an optional-renewal or
total-potential figure. Keep explicit per-supplier allocations distinct from
whole-tender prices and shared limits.

3. Tax basis (amount_basis) is strictly local to the clause or column heading
governing that specific amount. Read tax qualifiers in column headings and
narrative, not only beside a number:
- Use plus_HST ONLY when that amount's own clause/heading explicitly states
  plus/excluding/excl. HST.
- Use net_of_taxes ONLY when explicitly net of all applicable taxes.
- Use including_HST ONLY when HST inclusion is explicit.
- Generic wording such as "plus applicable taxes" or "including applicable taxes"
  (without naming HST), or pre-HST "net of G.S.T.", MUST be unknown. NEVER infer
  plus_HST on an award from a separate bid table or another amount in the document.
Apply the appropriate source heading to each amount, including an unpriced bidder
only when that heading actually applies.

4. Compliance status: use compliant ONLY when the document explicitly states that
the submitted bidder met specifications/requirements, passed the mandatory or
technical threshold, or was acceptable/compliant. High evaluation ranking,
recommendation for award, or satisfaction with services alone does NOT establish
compliant: use not_stated. Use non_compliant when the document explicitly states
the bidder did not meet mandatory/technical requirements or specifications (for
example, failing a minimum technical threshold). Keep a category-specific
disqualification attached to that participation only. Do not mark every listed
firm compliant just because the contract has a winner.

5. The prequalified array means prequalification-ONLY firms: strictly exclude any
firm that proceeded to invited_to_tender or bids. Never repeat a firm from
invited_to_tender or bids inside prequalified. invited_to_tender retains every
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

8. Scope of contracts: emit a contract only when the document describes concrete
procurement participation or decisions (bids, awards, prequalification,
invitations to tender, or a specific contract award/rescission action). For
non-procurement reports or ongoing/planned procurement status updates with no
bids, awards, prequalification, or invitations (and no numbered contract action),
return {"contracts": []}. Never emit an empty unnumbered contract stub.

Before returning the required JSON, check every named supported participant and
each bid/award amount against its contract, stage, role, tax heading, evidence
quote and status. Preserve negative documents as contracts: [] and unknown facts
as null/unknown; never turn a missing source fact into an invented recipient or
submitted price.

"""


def candidate_request(text, *, model=DEFAULT_MODEL, effort='high'):
    """Build an isolated, separately versioned candidate request with bid price evidence."""
    request = extraction_request(text, model=model, effort=effort)
    marker = 'DOCUMENT TEXT:\n\n'
    original = build_prompt('')
    assert original.endswith(marker)
    request['messages'][0]['content'][0]['text'] = (
        original.removesuffix(marker) + CANDIDATE_CLARIFICATIONS + marker
    )
    request['output_config']['format']['schema'] = copy.deepcopy(CANDIDATE_SCHEMA)
    return request


def _is_award_ceiling_quote(evidence_norm, amount_norm):
    """Detect an upset-limit/award-ceiling phrase immediately preceding the amount."""
    pattern = re.compile(_AWARD_CEILING_RE.pattern + re.escape(amount_norm), re.IGNORECASE)
    return bool(pattern.search(evidence_norm))


def validate_candidate_extraction(extraction, source_text):
    """Apply deterministic, source-bound validation to a raw candidate extraction.

    Returns (validated_extraction, interventions) without mutating `extraction`.
    Every modification is recorded explicitly in `interventions` so raw prompt-only
    behavior and evidence-backed validation can be compared transparently.
    """
    validate_extraction(extraction)
    validated = copy.deepcopy(extraction)
    interventions = []
    source_norm = _text(source_text)

    kept_contracts = []
    for contract in validated['contracts']:
        ref = contract.get('reference')
        bids = contract.get('bids', [])
        awards = contract.get('awards', [])
        prequalified = contract.get('prequalified', [])
        invited = contract.get('invited_to_tender', [])

        # Rule 3: Remove empty unnumbered ongoing-procurement stubs.
        if (ref is None and not bids and not awards and not prequalified
                and not invited and contract.get('declared_submissions') is None):
            interventions.append({
                'rule': 'ongoing_procurement_empty_stub',
                'contract': None,
                'title': contract.get('title'),
                'reason': 'empty unnumbered contract with no bids, awards, prequalification, or invitations',
            })
            continue

        # Rule 2: Enforce prequalification-only membership.
        tender_participants = (
            {_normal_name(name) for name in invited if isinstance(name, str) and name.strip()}
            | {_normal_name(b['supplier_name']) for b in bids if isinstance(b.get('supplier_name'), str)}
        )
        if prequalified and tender_participants:
            filtered_preq = []
            for name in prequalified:
                if isinstance(name, str) and _normal_name(name) in tender_participants:
                    interventions.append({
                        'rule': 'prequalified_only_membership',
                        'contract': ref,
                        'supplier': name,
                        'reason': 'firm appears in invited_to_tender or bids',
                    })
                else:
                    filtered_preq.append(name)
            contract['prequalified'] = filtered_preq

        # Rule 1: Require literal source evidence for submitted bid prices.
        contract_award_amounts = {
            _text(award['amount_raw'])
            for award in awards
            if isinstance(award.get('amount_raw'), str) and award['amount_raw'].strip()
        }

        for bid in bids:
            amount_raw = bid.get('amount_raw')
            if amount_raw is None:
                continue
            amount_norm = _text(amount_raw)
            evidence = bid.get('amount_evidence')
            reason = None
            if not isinstance(evidence, str) or not evidence.strip():
                reason = 'missing amount_evidence for non-null submitted bid price'
            else:
                evidence_norm = _text(evidence)
                if evidence_norm not in source_norm:
                    reason = 'amount_evidence is not a verbatim substring of source text'
                elif amount_norm not in evidence_norm:
                    reason = 'amount_raw does not appear inside amount_evidence'
                elif (amount_norm in contract_award_amounts
                      and _is_award_ceiling_quote(evidence_norm, amount_norm)):
                    reason = 'amount_evidence quotes an upset-limit/award-ceiling phrase matching an award amount'
            if reason is not None:
                interventions.append({
                    'rule': 'submitted_price_evidence',
                    'contract': ref,
                    'supplier': bid['supplier_name'],
                    'original_amount': amount_raw,
                    'original_basis': bid.get('amount_basis'),
                    'original_evidence': evidence,
                    'reason': reason,
                })
                bid['amount_raw'] = None
                bid['amount_basis'] = None
                bid['amount_evidence'] = None

        kept_contracts.append(contract)

    validated['contracts'] = kept_contracts
    return validated, interventions


def prepare_candidate_campaign(cohort_path, output_dir, *, campaign_id, prior_cohorts=(),
                               model=DEFAULT_MODEL, effort='high'):
    """Offline preparation for a separately versioned evidence-backed candidate campaign."""
    cohort_path, output_dir = Path(cohort_path), Path(output_dir)
    cohort = load_cohort(cohort_path)
    docs = {d['id']: d for d in cohort['documents']}
    held_out = [d['id'] for d in cohort['documents'] if d.get('evaluation_partition') == 'held_out_new_corpora']
    regression = [d['id'] for d in cohort['documents'] if d.get('evaluation_partition') == 'regression']
    if not held_out or not regression or set(held_out) & set(regression) or set(held_out) | set(regression) != set(docs):
        raise ValueError('Cohort must partition cleanly into held_out_new_corpora and regression')

    prior_hashes = {}
    prior_text_hashes = set()
    for p in prior_cohorts:
        p = Path(p)
        key = os.path.relpath(p.resolve(), output_dir.resolve())
        prior_hashes[key] = digest(p.read_bytes())
        prior_cohort = load_cohort(p)
        prior_text_hashes.update(d['text_sha256'] for d in prior_cohort['documents'])

    held_out_text_hashes = {docs[id]['text_sha256'] for id in held_out}
    if held_out_text_hashes & prior_text_hashes:
        raise ValueError('Held-out new-corpora source duplicates a prior cohort input')

    runs_dir = output_dir / 'runs'
    run_paths = prepare_trials(
        cohort_path,
        runs_dir,
        campaign_id=campaign_id,
        request_factory=lambda text: candidate_request(text, model=model, effort=effort),
    )
    sample_request = candidate_request('', model=model, effort=effort)
    prefix = sample_request['messages'][0]['content'][0]['text']
    manifest = {
        'id': campaign_id,
        'cohort_sha256': digest(cohort_path.read_bytes()),
        'prior_cohort_sha256s': prior_hashes,
        'base_prompt_version': EXTRACTOR_VERSION,
        'prompt_version': 'eval-' + digest(prefix.encode())[:12],
        'clarifications_sha256': digest(CANDIDATE_CLARIFICATIONS.encode()),
        'schema_sha256': identity(CANDIDATE_SCHEMA),
        'model': model,
        'effort': effort,
        'modes': ['sync', 'batch'],
        'repeats': 2,
        'partitions': {
            'held_out_new_corpora': held_out,
            'regression': regression,
        },
        'planned_requests': sum(len(json.loads(p.read_text())['attempts']) for p in run_paths),
        'run_paths': [str(p.relative_to(output_dir)) for p in run_paths],
        'settled_policies': {
            'award_tax_basis': 'strictly_local_unknown',
            'ongoing_procurement_scope': 'empty_contracts_array',
        },
        'limitations': [
            'Purposive source-level holdout across the seven previously absent corpora, not a random archive sample.',
            'Agent-reviewed source labels, not independent human adjudication.',
            'Experimental candidate prompt and schema cannot confer production qualification without promotion review.',
        ],
    }
    with (output_dir / 'campaign.manifest').open('x') as stream:
        stream.write(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    return manifest


def summarize_candidate_campaign(root, *, prior_cohorts=()):
    """Reproduce offline prompt-only vs evidence-backed validation measurements."""
    root = Path(root)
    manifest = json.loads((root / 'campaign.manifest').read_text())
    cohort_path = root / 'cohort.json'
    if digest(cohort_path.read_bytes()) != manifest['cohort_sha256']:
        raise ValueError('Frozen candidate cohort changed')
    cohort = load_cohort(cohort_path)
    docs = {d['id']: d for d in cohort['documents']}
    held_out = manifest['partitions']['held_out_new_corpora']
    regression = manifest['partitions']['regression']
    if set(held_out) & set(regression) or set(held_out) | set(regression) != set(docs):
        raise ValueError('Campaign partition membership changed')

    for p in prior_cohorts:
        p = Path(p)
        key = os.path.relpath(p.resolve(), root.resolve())
        expected_sha = manifest['prior_cohort_sha256s'].get(key)
        if expected_sha and digest(p.read_bytes()) != expected_sha:
            raise ValueError(f'Prior cohort identity changed: {key}')
        prior = load_cohort(p)
        if {docs[id]['text_sha256'] for id in held_out} & {d['text_sha256'] for d in prior['documents']}:
            raise ValueError('Held-out source duplicates a prior input')

    paths = [root / p for p in manifest['run_paths']]
    report = qualification_report(cohort_path, paths)
    by_run = {run['id']: run for run in report['runs']}

    raw_rows = []
    validated_rows = []
    interventions_log = []
    profiles = set()

    for path in paths:
        run = json.loads(path.read_text())
        if run['cohort_sha256'] != manifest['cohort_sha256']:
            raise ValueError('Trial label identity changed')
        for row in by_run[run['id']]['documents']:
            doc_id = row['document']
            attempt = run['attempts'].get(doc_id)
            if attempt is None:
                continue
            p = attempt['provenance']
            if (p.get('model') != manifest['model'] or p.get('effort') != manifest['effort']
                    or p.get('prompt_version') != manifest['prompt_version']
                    or p.get('schema_sha256') != manifest['schema_sha256']):
                raise ValueError('Trial configuration differs from frozen candidate manifest')
            profiles.add((p['prompt_version'], p['schema_sha256']))
            raw_rows.append(dict(run=run['id'], mode=run['mode'], repeat=run['repeat'], **row))

            if attempt['state'] == 'success':
                source_text = (root / docs[doc_id]['text_path']).read_text()
                validated_ext, interventions = validate_candidate_extraction(attempt['extraction'], source_text)
                if interventions:
                    interventions_log.append({
                        'run': run['id'], 'mode': run['mode'], 'repeat': run['repeat'],
                        'document': doc_id, 'interventions': interventions,
                    })
                # Evaluate the validated projection using the same scorer.
                validated_attempt = copy.deepcopy(attempt)
                validated_attempt['extraction'] = validated_ext
                validated_attempt['provenance']['response_sha256'] = identity(validated_ext)
                v_row = _evaluate(docs[doc_id], validated_attempt, root)
                validated_rows.append(dict(run=run['id'], mode=run['mode'], repeat=run['repeat'], **v_row))
            else:
                validated_rows.append(dict(run=run['id'], mode=run['mode'], repeat=run['repeat'], **row))

    if len(profiles) != 1:
        raise ValueError('Campaign must use one isolated candidate prompt/schema profile')

    def aggregate(selected):
        scored = [r for r in selected if r['state'] == 'success' and 'metrics' in r]
        exact = {k: sum(r['metrics'][k] for r in scored) for k in ('expected', 'observed', 'correct')}
        exact.update({k: sum(len(r['metrics'][k]) for r in scored) for k in
                      ('omissions', 'attribute_errors', 'false_joins', 'fabrications', 'duplicates')})
        exact['planned_expected'] = sum(len(docs[r['document']]['relations']) for r in selected)
        return dict(
            requests=len(selected),
            states=dict(Counter(r['state'] for r in selected)),
            exact_relations=exact,
            numeric_correct=sum(r['amount_value_metrics']['correct'] for r in scored),
            exact_documents=sum(
                r.get('contracts_match', False)
                and r['metrics']['correct'] == r['metrics']['expected'] == r['metrics']['observed']
                for r in scored
            ),
            charged_microdollars=sum(r.get('charged_microdollars') or 0 for r in selected),
        )

    def summarize_view(rows):
        partitions = {}
        for part_name, doc_ids in manifest['partitions'].items():
            selected = [r for r in rows if r['document'] in doc_ids]
            partitions[part_name] = dict(
                aggregate(selected),
                by_mode={mode: aggregate([r for r in selected if r['mode'] == mode]) for mode in manifest['modes']},
            )
        return {'total': aggregate(rows), 'partitions': partitions}

    ids = [r['provenance'].get('response_id') for r in raw_rows if r['state'] == 'success']
    return {
        'campaign': manifest['id'],
        'cohort_sha256': manifest['cohort_sha256'],
        'prompt_version': manifest['prompt_version'],
        'schema_sha256': manifest['schema_sha256'],
        'model': manifest['model'],
        'effort': manifest['effort'],
        'settled_policies': manifest['settled_policies'],
        'unique_response_ids': len({i for i in ids if i}),
        'prompt_only': summarize_view(raw_rows),
        'evidence_validated': summarize_view(validated_rows),
        'validation_interventions': interventions_log,
        'production_qualified': report['qualified'],
        'limitations': manifest['limitations'],
    }
