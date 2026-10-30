"""Exact publisher corroboration for missing monthly award recipients.

This supplements a source observation; it never matches procurements by supplier,
amount, title similarity or an awarding-agent annotation. Raw model results survive.
"""
import hashlib
import json
import re
from functools import cache
from importlib import resources


def result_fingerprint(result):
    return hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


@cache
def _evidence():
    data=json.loads(resources.files('toronto_bids.data').joinpath('publication_award_evidence.json').read_text())
    if data.get('version')!=1 or not isinstance(data.get('decisions'),list):
        raise ValueError('invalid publication recipient evidence; re-review required')
    result={}
    for proof in data['decisions']:
        if (proof.get('buyer')!='waterfront-toronto' or not proof.get('basis')
                or not proof.get('recipient') or not proof.get('model_supplier')
                or not proof.get('source_text') or not proof.get('extractor_version')
                or not isinstance(proof.get('amount_raw'),str)
                or not re.fullmatch(r'\d{4}-\d{2}',proof.get('publication_month',''))
                or proof.get('primary_url')!='https://www.waterfrontoronto.ca/opportunities/procurement/awarded-contracts'
                or any(type(proof.get(k)) is not int or proof[k]<0 for k in ('contract_index','award_index'))):
            raise ValueError('invalid publication recipient scope; re-review required')
        for sha in [proof['publication_key'],proof['extraction_sha256'],proof['result_sha256']]:
            if not re.fullmatch('[a-f0-9]{64}',sha):
                raise ValueError('invalid publication recipient fingerprint; re-review required')
        if not proof.get('sources'):
            raise ValueError('recipient requires independent publisher evidence; re-review required')
        for source in proof['sources']:
            if (not source['url'].startswith('https://www.waterfrontoronto.ca/')
                    or not re.fullmatch('[a-f0-9]{64}',source['sha256'])
                    or type(source.get('page')) is not int or source['page']<1
                    or not source.get('passages') or not all(source['passages'])
                    or not any(proof['recipient'] in p and source['identifier'] in p for p in source['passages'])):
                raise ValueError('invalid independent publisher relationship; re-review required')
        key=proof['publication_key']
        if key in result:raise ValueError('duplicate publication recipient review; re-review required')
        result[key]=proof
    return result


def reviewed_recipients(rows,result,*,extraction_sha256,extractor_version):
    """Return exact reviewed award positions, or refuse changed reviewed context."""
    evidence=_evidence();reviewed={}
    for row in rows:
        proof=evidence.get(row['source_key'])
        if proof is None:continue
        if (row.get('raw_text')!=proof['source_text']
                or row.get('publication_month')!=proof['publication_month']
                or extraction_sha256!=proof['extraction_sha256']
                or extractor_version!=proof['extractor_version']
                or result_fingerprint(result)!=proof['result_sha256']):
            raise ValueError('reviewed publication recipient context changed; re-review required')
        try:award=result['contracts'][proof['contract_index']]['awards'][proof['award_index']]
        except (IndexError,KeyError,TypeError) as exc:
            raise ValueError('reviewed publication award missing; re-review required') from exc
        if award.get('supplier_name')!=proof['model_supplier'] or award.get('amount_raw')!=proof['amount_raw']:
            raise ValueError('reviewed publication award changed; re-review required')
        reviewed[proof['contract_index'],proof['award_index']]=proof
    return reviewed
