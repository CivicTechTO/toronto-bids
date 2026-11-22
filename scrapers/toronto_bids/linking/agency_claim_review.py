"""Source-bound participation and individual award admissions; raw extraction is immutable."""
from datetime import date
import re

from toronto_bids.linking.agency_identity import IdentityReviewRequired


_CONTEXT = {'report_url', 'contract_index', 'contract_count', 'result_sha256'}
_ROLES = {'prequalification_submissions', 'qualified_for_tender', 'invited_to_tender', 'tender_submissions'}
_TTC_APPROVAL_RE = re.compile(r'\b(?:approved|adopted|carried)\b', re.I)
_TTC_DISQUALIFYING_RE = re.compile(
    r'\b(?:recommendations?\s+be\s+amended|amended\s+to\s+award|deferred|defeated|'
    r'not\s+approved|withdrawn|received\s+this\s+item\s+for\s+information)\b',
    re.I,
)
_LEGAL_SUFFIX_RE = re.compile(
    r'\b(?:limited|ltd|incorporated|inc|corporation|corp|company|co|llc|llp|lp)\b\.?',
    re.I,
)
_MONEY_RE = re.compile(r'\$\s*([\d,.]+)(?:\s*(million|billion|m|b)\b)?', re.I)


def _norm_alnum(text):
    return re.sub(r'[^a-z0-9]+', '', (text or '').lower())


def _money_values(text):
    values = set()
    for match in _MONEY_RE.finditer(text or ''):
        raw_num = match.group(1).rstrip('.,')
        if not raw_num:
            continue
        # Fix occasional PDF period-for-comma typo (e.g. "$32.500,000.00").
        fixed = re.sub(r'(\d)\.(\d{3}),(\d{3})', r'\1,\2,\3', raw_num).replace(',', '')
        try:
            amount = float(fixed)
        except ValueError:
            continue
        unit = (match.group(2) or '').lower()
        if unit in ('million', 'm'):
            amount *= 1_000_000
        elif unit in ('billion', 'b'):
            amount *= 1_000_000_000
        values.add(round(amount, 2))
    return values


def _ttc_supplier_in_decision(supplier, decision, report_passages):
    dec_alnum = _norm_alnum(decision)
    sup_alnum = _norm_alnum(supplier)
    if sup_alnum and sup_alnum in dec_alnum:
        return True
    core = _norm_alnum(_LEGAL_SUFFIX_RE.sub(' ', supplier))
    if len(core) >= 4 and core in dec_alnum:
        return True
    stop = {'and', 'the', 'for', 'division', 'of', 'inc', 'ltd', 'limited', 'corp', 'corporation', 'company', 'co'}
    words = [w.lower() for w in re.findall(r'[A-Za-z0-9]{3,}', supplier) if w.lower() not in stop]
    if words and all(w in dec_alnum for w in words):
        return True
    # Allow explicit parenthetical short names defined in the bound staff report (e.g. "... (Wood)", "... (J.J. McGuire)").
    prefix = re.escape(supplier[:16])
    for match in re.finditer(prefix + r'[^(]{0,120}\(([^)]{2,30})\)', report_passages, re.I):
        short = match.group(1).strip()
        short_alnum = _norm_alnum(short)
        if len(short_alnum) >= 3 and (
            re.search(rf'\b{re.escape(short)}\b', decision, re.I)
            or (len(short_alnum) >= 6 and short_alnum in dec_alnum)
        ):
            return True
    return False


def _ttc_decision_binds(decision, report_passages, proof, award):
    if not _TTC_APPROVAL_RE.search(decision) or _TTC_DISQUALIFYING_RE.search(decision):
        return False
    supplier = award.get('supplier_name')
    if supplier and not _ttc_supplier_in_decision(supplier, decision, report_passages):
        return False
    amount = award.get('amount_raw')
    if amount:
        award_vals = _money_values(amount)
        dec_vals = _money_values(decision)
        if not award_vals or not (award_vals & dec_vals):
            return False
    ref = proof['native_ref']
    if not ref.startswith('https://'):
        ref_alnum = _norm_alnum(ref)
        if ref_alnum not in _norm_alnum(decision) and ref_alnum not in _norm_alnum(report_passages):
            return False
    return True


def validate_review(proof):
    """Validate optional review vocabulary after the enclosing source context is validated."""
    if not {'award_review', 'participation_review'}.intersection(proof):
        return
    if (proof['corpus'] not in {'trca', 'ttc'} or not _CONTEXT.issubset(proof)
            or type(proof.get('contract_index')) is not int or proof['contract_index'] < 0
            or type(proof.get('contract_count')) is not int or proof['contract_count'] <= proof['contract_index']
            or not re.fullmatch('[a-f0-9]{64}', str(proof.get('result_sha256') or ''))
            or proof.get('withheld_awards_reason') or proof.get('representative_source')):
        raise ValueError('claim review requires an exact reviewed context without conflicting contract-wide withholding')
    participation = proof.get('participation_review')
    if participation is not None:
        if proof['corpus'] != 'trca':
            raise ValueError('participation review is only supported for TRCA')
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
    report_passages = ' '.join(
        ' '.join(p.split())
        for s in proof['sources']
        if s.get('role') != 'decision'
        for p in s['passages']
    )
    result = {'accepted': [], 'withheld': [], 'amounts_withheld': []}
    for claim in review:
        index = claim['award_index']
        if claim['disposition'] == 'withheld':
            result['withheld'].append({'award_index': index, 'reason': claim['reason']})
            continue
        award = dict(raw[index])
        decision = ' '.join(' '.join(p.split()) for p in proof['sources'][claim['decision_source']]['passages'])
        if proof['corpus'] == 'ttc':
            if not _ttc_decision_binds(decision, report_passages, proof, award):
                raise IdentityReviewRequired('reviewed TTC Board decision does not bind this award claim; re-review required')
        else:
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
