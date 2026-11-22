"""Admission rules for extracted historical BD report bids.

Requires agreement between the extracted Call Number, agenda title, and report text.
"""

import re

from toronto_bids.linking.call_number import find_call_numbers
from toronto_bids.models import Bid

# Refuse multi-part/lot reports; the bid schema has no lot dimension.
_PART = re.compile(r"\b(?:parts?|lots?)\s*[-–:]?\s*[\"'“”‘’]?([A-Z]|\d+)\b", re.I)
_ROLE_IN_NAME = re.compile(r"\b(?:informal|non[-_ ]?compliant|disqualified|withdrawn|no[-_ ]?bid)\s*$", re.I)
# Prices with these qualifications are withheld; participation is still admitted.
_QUALIFIED_PRICE = re.compile(r"\bmiscellaneous\b|\bprompt\s+payment\b|\bunit\s+(?:prices?|rates?)\b", re.I)


def _literal(text):
    return " ".join(text.split()).casefold()


def admit_report(*, reference, title, text, result):
    """Return supported bid rows and an inspectable decision for one held report."""
    decision = {"reference": reference, "accepted": False, "call_number": None,
                "reason": None, "prices_withheld": 0, "statuses_withheld": 0}
    contracts = result.get("contracts", [])

    def refuse(reason):
        decision['reason'] = reason
        decision['prices_withheld'] = 0
        decision['statuses_withheld'] = 0
        return [], decision

    if len(contracts) != 1:
        return refuse('requires exactly one contract; multi-contract identity unresolved')
    if not text or not text.strip():
        return refuse('held report has no readable text')
    literal_text = _literal(text)
    contract = contracts[0]
    calls = find_call_numbers(contract.get('reference'))
    if len(calls) != 1:
        return refuse('extracted reference must name exactly one native Call Number')
    call = next(iter(calls))
    decision['call_number'] = call
    if find_call_numbers(title) != {call} or call not in find_call_numbers(text):
        return refuse('Call Number does not agree with original agenda title and held text')
    parts = {m.group(1).upper() for m in _PART.finditer(text)}
    if parts:
        return refuse('part/lot scope cannot be represented by the bid schema')
    bids = contract.get('bids', [])
    if not bids:
        return refuse('no named bid observations extracted')
    # A declared count is a lower bound (some reports count only compliant bids).
    declared = contract.get('declared_submissions')
    if isinstance(declared, int) and len(bids) < declared:
        return refuse('extracted bids fall short of the declared submission count')
    names = [_literal(b.get('supplier_name') or '') for b in bids]
    if any(not n or n not in literal_text for n in names):
        return refuse('bidder name is not literal evidence in the held text')
    if any(_ROLE_IN_NAME.search(b['supplier_name']) for b in bids):
        return refuse('supplier name contains a submission-role annotation; role attribution unresolved')
    if any(b.get('status') == 'no_bid' for b in bids):
        return refuse('no-bid outcome cannot establish submitted-bid participation')
    if len(names) != len(set(names)):
        return refuse('repeated bidder observations require unresolved scope')
    qualified_price = bool(_QUALIFIED_PRICE.search(text))
    derived = []
    for bid in bids:
        raw = bid.get('amount_raw')
        if raw and _literal(raw) not in literal_text:
            return refuse('bid price is not literal evidence in the held text')
        withheld = bool(raw and qualified_price)
        decision['prices_withheld'] += int(withheld)
        # Status is withheld because extraction can over-infer compliance.
        decision['statuses_withheld'] += int(bid.get('status') not in (None, 'not_stated'))
        derived.append(Bid(
            reference=reference, document_number=None,
            bidder_name_raw=bid['supplier_name'],
            bid_price=None if withheld else raw,
            hst_basis=None if withheld or not raw else {'including_HST':'including', 'plus_HST':'excluding'}.get(bid.get('amount_basis')),
            price_header=None if withheld else bid.get('price_header'), status=None,
            source='bd_report',
        ))
    decision['accepted'] = True
    decision['reason'] = ('participation admitted; unqualified price/status claims retained in raw cache only'
                          if decision['prices_withheld'] or decision['statuses_withheld']
                          else 'single Call Number and source context agree')
    return derived, decision
