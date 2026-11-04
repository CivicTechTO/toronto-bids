"""Source-bound participation and individual award admissions; raw extraction is immutable."""
from datetime import date

from toronto_bids.linking.agency_identity import IdentityReviewRequired


_CONTEXT = {'report_url', 'contract_index', 'contract_count', 'result_sha256'}
_ROLES = {'prequalification_submissions', 'qualified_for_tender', 'invited_to_tender', 'tender_submissions'}


def validate_review(proof):
    """Validate optional review vocabulary after the enclosing source context is validated."""
    if not {'award_review', 'participation_review'}.intersection(proof):
        return
    if (proof['corpus'] != 'trca' or not _CONTEXT.issubset(proof)
            or proof.get('withheld_awards_reason') or proof.get('representative_source')):
        raise ValueError('claim review requires an exact TRCA context without conflicting contract-wide withholding')
    participation = proof.get('participation_review')
    if participation is not None:
        if not isinstance(participation, dict) or set(participation) != _ROLES:
            raise ValueError('participation review requires explicit procurement roles')
        for role, names in participation.items():
            if names is None and role == 'prequalification_submissions':
                continue  # A roster of qualified firms does not disclose the original applicant pool.
            if (not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names)
                    or len(names) != len(set(names))):
                raise ValueError('reviewed participation requires unique literal names, or an unknown applicant pool')
    review = proof.get('award_review')
    if review is None:
        return
    if not isinstance(review, list) or not review:
        raise ValueError('award review requires individual claims')
    seen = set()
    for claim in review:
        if (set(claim) != {'award_index', 'disposition', 'reason', 'decision_source', 'decision_date'}
                or type(claim['award_index']) is not int or claim['award_index'] < 0
                or claim['award_index'] in seen or not isinstance(claim['reason'], str) or not claim['reason'].strip()
                or claim['disposition'] not in {'adopted', 'shared_limit', 'withheld'}):
            raise ValueError('invalid or duplicate individual award admission')
        seen.add(claim['award_index'])
        if claim['disposition'] == 'withheld':
            if claim['decision_source'] is not None or claim['decision_date'] is not None:
                raise ValueError('withheld claim cannot assert a decision')
            continue
        index = claim['decision_source']
        if type(index) is not int or not 0 <= index < len(proof['sources']):
            raise ValueError('accepted award requires a bound decision source')
        source = proof['sources'][index]
        if (source.get('role') != 'decision' or not isinstance(claim['decision_date'], str)
                or source.get('decision_date') != claim['decision_date']
                or date.fromisoformat(claim['decision_date']).isoformat() != claim['decision_date']):
            raise ValueError('accepted award requires an explicit reviewed decision and ISO date')


def admit_claims(conn, contract, proof):
    """Apply an already identity-bound review, verifying every cited held source first.

    Accepted shared limits retain recipients with unknown allocations. No new price or
    supplier can be introduced. A missing/changed source aborts before any derived-row swap.
    """
    for source in proof['sources']:
        held = conn.execute('SELECT sha256,text FROM background_pdf WHERE url=?', (source['url'],)).fetchone()
        if held is None or held['sha256'] != source['sha256'] or not held['text']:
            raise IdentityReviewRequired('reviewed claim source missing or changed; re-review required')
        text = ' '.join(held['text'].split())
        if not all(' '.join(p.split()) in text for p in source['passages']):
            raise IdentityReviewRequired('reviewed claim source passage changed; re-review required')
    participation = proof.get('participation_review')
    if participation is not None and participation['tender_submissions'] != [b.get('supplier_name') for b in contract.get('bids', [])]:
        raise IdentityReviewRequired('reviewed tender participation differs from raw submitted bids; re-review required')
    review = proof.get('award_review')
    if review is None:
        return None
    raw = contract.get('awards', [])
    if {c['award_index'] for c in review} != set(range(len(raw))):
        raise IdentityReviewRequired('review must account for every raw award claim; re-review required')
    result = {'accepted': [], 'withheld': [], 'amounts_withheld': []}
    for claim in review:
        index = claim['award_index']
        if claim['disposition'] == 'withheld':
            result['withheld'].append({'award_index': index, 'reason': claim['reason']})
            continue
        award = dict(raw[index])
        decision = ' '.join(' '.join(p.split()) for p in proof['sources'][claim['decision_source']]['passages'])
        subjects = [proof['native_ref'], award.get('supplier_name'), award.get('amount_raw')]
        if ('carried' not in decision.casefold() or any(value and ' '.join(value.split()) not in decision for value in subjects)):
            raise IdentityReviewRequired('reviewed carried decision does not bind this award claim; re-review required')
        if claim['disposition'] == 'shared_limit':
            if award.get('amount_raw') is None:
                raise IdentityReviewRequired('reviewed shared limit no longer has its expected raw amount')
            award.update(amount_raw=None, value_confidential=False)
            result['amounts_withheld'].append(index)
        result['accepted'].append({'award_index': index, 'award': award, 'award_date': claim['decision_date']})
    return result
