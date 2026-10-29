"""Reviewed source relationships for agency identities, separate from extraction (#258).

Evidence binds a buyer, held document, extraction version and exact contract payload.
Unmatched legacy decisions retain their existing identity rules. Known TTC sources
require re-review when context changes. This registry does not infer matches from
names, titles, amounts, document positions or digit substrings.
"""
import hashlib
import json
import re
from functools import cache
from importlib import resources

from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.linking.agency_reference import normalize_agency_reference


class IdentityReviewRequired(ValueError):
    """A previously reviewed TTC source changed; keep its last verified facts."""


def contract_fingerprint(contract):
    return hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@cache
def _evidence():
    data = json.loads(resources.files('toronto_bids.data').joinpath('agency_identity_evidence.json').read_text())
    if data.get('version') != 1 or not isinstance(data.get('decisions'), list):
        raise ValueError('unsupported agency identity evidence format')
    result = {}
    buyers = {'trca': 'trca', 'ep': 'exhibition-place', 'zoo': 'toronto-zoo',
              'ttc': 'toronto-transit-commission'}
    for item in data['decisions']:
        corpus = item['corpus']
        native = item['native_ref']
        if (corpus not in buyers or not isinstance(native, str) or not native or
                normalize_agency_reference(buyers[corpus], native) != native):
            raise ValueError('agency identity evidence requires a canonical buyer-scoped reference')
        kind = item.get('identity_kind', 'solicitation')
        if kind != 'solicitation' and not (corpus == 'ttc' and kind in ('contract', 'report_url_fallback')):
            raise ValueError('unsupported agency identity kind')
        if kind == 'report_url_fallback':
            if (native != item.get('report_url') or item.get('contract_count') != 1
                    or not any(i['role'] == 'administrative' and i['value']
                               and i['authority'] == buyers[corpus] for i in item['identifiers'])):
                raise ValueError('reviewed report identity requires one collection and an administrative identifier')
        elif (corpus == 'ttc' and re.match(r'https?://', native)) or not any(
                i['role'] == kind and i['value'] == native and i['authority'] == buyers[corpus]
                for i in item['identifiers']):
            raise ValueError('agency identity evidence must distinguish the primary printed identifier')
        if not item['basis'] or not item['sources'] or not item['extractor_version']:
            raise ValueError('agency identity evidence requires a basis, sources and extraction version')
        for sha in [item['document_sha256'], item['contract_sha256'], *[s['sha256'] for s in item['sources']]]:
            if not re.fullmatch('[a-f0-9]{64}', sha):
                raise ValueError('agency identity evidence requires full SHA-256 fingerprints')
        for source in item['sources']:
            if not source['url'].startswith('https://') or not source['passages'] or not all(source['passages']):
                raise ValueError('agency identity evidence requires published source URLs and passages')
        if corpus == 'ttc':
            if (type(item.get('contract_count')) is not int or item['contract_count'] < 1
                    or not any(s['url'] == item.get('report_url') and s['sha256'] == item['document_sha256']
                               for s in item['sources']) or item.get('legacy_native_ref')):
                raise ValueError('TTC identity evidence requires exact report context without an inferred alias')
        key = (corpus, item['document_sha256'], item['extractor_version'], item['contract_sha256'])
        if key in result:
            raise ValueError('duplicate agency identity decision')
        result[key] = item
    return result


def reviewed_identity(corpus, document_sha256, contract, *, report_url=None, contract_count=None):
    """Resolve exact reviewed facts; changed TTC report contexts require fresh review.

    Other agencies retain their existing unmatched-reference policy. TTC decisions
    explicitly bind the held URL and collection count so an administrative footer
    cannot reappear as the identity after a payload/version/source change.
    """
    evidence = _evidence()
    match = evidence.get((corpus, document_sha256, EXTRACTOR_VERSION, contract_fingerprint(contract)))
    if corpus != 'ttc':
        return match
    scoped = [item for item in evidence.values() if item['corpus'] == corpus and
              (item['document_sha256'] == document_sha256 or item.get('report_url') == report_url)]
    if not scoped:
        return None
    if (match is None or match['report_url'] != report_url or type(contract_count) is not int
            or match['contract_count'] != contract_count):
        raise IdentityReviewRequired('reviewed TTC identity no longer matches its source, extraction or collection context; re-review required')
    return match


def history_aliases(decisions):
    """Only reviewed old representations of facts from the same held report may re-key."""
    aliases = []
    for decision in decisions:
        proof = json.loads(decision.get('identity_evidence_json') or 'null')
        if not proof or not proof.get('legacy_native_ref'):
            continue
        contract = json.loads(decision['contract_json'])
        aliases.append(dict(old_native_ref=proof['legacy_native_ref'], native_ref=proof['native_ref'],
                            report_url=decision['report_url'],
                            bids={(f.get('supplier_name'), f.get('amount_raw')) for f in contract.get('bids', [])},
                            awards={(f.get('supplier_name'), f.get('amount_raw')) for f in contract.get('awards', [])}))
    return aliases
