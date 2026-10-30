"""A missing recipient needs an exact, independently reviewed publisher relationship."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from toronto_bids.export.document import build_export_document
from toronto_bids.sources import waterfront as wf
from toronto_bids.store import db

FIXTURES=Path(__file__).parent/'fixtures/waterfront'
PILOT=json.loads((FIXTURES/'2023-03.json').read_text())
HTML=(FIXTURES/'awards.html').read_bytes()


def march():
    return [r for r in wf.parse_publication(HTML) if r['publication_month']=='2023-03']


def test_official_corroboration_recovers_recipient_without_editing_model_output():
    original=copy.deepcopy(PILOT['result'])
    claims,errors=wf.verified_claims(march(),original)
    assert errors==[] and len(claims)==5
    recovered=next(c for c in claims if c['supplier_name_raw']=='WJ Groundwater')
    assert recovered['award_amount']=='$313,750' and recovered['award_amount_numeric']==313750
    proof=json.loads(recovered['evidence_json'])
    assert proof['model_supplier']=='Ellis Don Civil Ltd'
    assert proof['sources'][0]['page']==27 and proof['sources'][0]['url'].endswith('2023_0.pdf')
    assert 'WJ Groundwater' in proof['sources'][0]['passages'][0]
    assert original==PILOT['result']
    assert not any(c['supplier_name_raw']=='Ellis Don Civil Ltd' for c in claims)


def test_corroborated_fact_cache_and_export_are_stable(conn,tmp_path):
    sha=hashlib.sha256(PILOT['text'].encode()).hexdigest()
    raw=json.dumps(PILOT['result']);db.mark_extracted(conn,sha,wf.EXTRACTOR_VERSION,raw);conn.commit()
    class Page:
        def get_bytes(self,url):return HTML
    first=wf.capture_awards(conn,Page(),data_dir=tmp_path,cached_only=True,only_extractions={sha},log=lambda _:None)
    assert first['failures']==[] and first['awards']==5 and first['requests']==0
    assert db.get_extraction(conn,sha,wf.EXTRACTOR_VERSION)==raw
    before=[tuple(r) for r in conn.execute('SELECT source_key,first_seen,evidence_json FROM published_award ORDER BY source_key')]
    wf.capture_awards(conn,Page(),data_dir=tmp_path,cached_only=True,only_extractions={sha},log=lambda _:None)
    assert [tuple(r) for r in conn.execute('SELECT source_key,first_seen,evidence_json FROM published_award ORDER BY source_key')]==before
    buyer=next(b for b in build_export_document(conn,generated_at='fixed')['buyers'] if b['slug']=='waterfront-toronto')
    award=next(a for a in buyer['published_awards'] if a['supplier_name_raw']=='WJ Groundwater')
    assert isinstance(award['evidence_json'],dict) and award['evidence_json']['sources']
    # A changed model response must not silently reuse the old recipient review or
    # delete any previously verified fact during a partial refresh.
    changed=copy.deepcopy(PILOT['result']);changed['contracts'][4]['title']+=' changed'
    db.mark_extracted(conn,sha,wf.EXTRACTOR_VERSION,json.dumps(changed));conn.commit()
    again=wf.capture_awards(conn,Page(),data_dir=tmp_path,cached_only=True,only_extractions={sha},log=lambda _:None)
    assert again['failures'] and 're-review' in again['failures'][0][1]
    assert [tuple(r) for r in conn.execute('SELECT source_key,first_seen,evidence_json FROM published_award ORDER BY source_key')]==before


@pytest.mark.parametrize('change',['payload','row','version','month_context'])
def test_changed_source_or_result_requires_new_review(monkeypatch,change):
    rows=march();result=copy.deepcopy(PILOT['result'])
    if change=='payload':result['contracts'][4]['awards'][0]['amount_raw']='$1'
    if change=='row':rows[-1]['raw_text']+=' changed'
    if change=='version':monkeypatch.setattr(wf,'EXTRACTOR_VERSION','changed-version')
    if change=='month_context':rows=rows+[dict(rows[-1],source_key='another',raw_text='Another source row')]
    with pytest.raises(ValueError,match='re-review'):wf.verified_claims(rows,result)


def test_review_quotes_the_archived_official_pdf_page():
    import subprocess
    from toronto_bids.linking.publication_evidence import _evidence
    proof=next(iter(_evidence().values()));source=proof['sources'][0]
    pdf=FIXTURES/'cc6567-source-27.pdf'
    assert hashlib.sha256(pdf.read_bytes()).hexdigest()==source['excerpt_sha256']
    text=subprocess.check_output(['pdftotext','-layout',str(pdf),'-'],text=True)
    normalized=''.join(text.split())
    assert all(''.join(p.split()) in normalized for p in source['passages'])
    assert 'APPROVED' in text and 'CONTINGENCY' in text
    # The corroboration supplies the recipient, never a replacement price.
    assert '332,261.25' in text and proof['amount_raw']=='$313,750'
