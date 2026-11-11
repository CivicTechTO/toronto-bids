"""A contract's source evidence must never assign its sibling contracts to it."""
import hashlib
import json

from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.models import BackgroundPdf,CompositeAward,CouncilItem
from toronto_bids.store import db

TEXT='''Call No:
Request for Quotation 3905-14-0097
Description:
Works
Recommended Bidder:
Alpha Construction Ltd.
Contract Award Value:
$100,000 net of taxes
Number of Bids:
One (1)

APPENDIX 2
Call No:
Request for Quotation 4000-14-0001
Description:
A different contract
Recommended Bidder:
Beta Ltd.
Number of Bids:
One (1)
'''


def setup_source(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive,archive_fixture
    from toronto_bids.linking import city_crosswalk as cw
    from toronto_bids.linking.city_archived_awards import load_evidence
    _,publisher,registry=setup_archive(conn,tmp_path,monkeypatch)
    bundle=json.loads(registry.read_text());bundle['proofs']=[];registry.write_text(json.dumps(bundle))
    conn.execute('DELETE FROM bid')
    reference='2014.BD1.1';title='Contract Awards – Bid Committee Composite Report'
    db.upsert_row(conn,CouncilItem(reference,title=title),overwrite=True)
    url='https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/composite.pdf';path=tmp_path/'composite.pdf';body=b'%PDF-pinned fixture';path.write_bytes(body);sha=hashlib.sha256(body).hexdigest()
    db.upsert_row(conn,BackgroundPdf(url,reference=reference,kind='bgrd',local_path=str(path),sha256=sha,text=TEXT),overwrite=True)
    contracts=[dict(reference='RFQ 3905-14-0097',stage='single_stage',declared_submissions=1,bids=[dict(supplier_name='Alpha Construction Ltd.',amount_raw='$100,000.55',status='compliant')]),
               dict(reference='RFQ 4000-14-0001',stage='single_stage',declared_submissions=1,bids=[dict(supplier_name='Beta Ltd.',amount_raw='$9,000')])]
    conn.execute('INSERT INTO extraction_cache(sha256,extractor_version,result_json) VALUES (?,?,?)',(sha,EXTRACTOR_VERSION,json.dumps(dict(contracts=contracts))))
    db.upsert_row(conn,CompositeAward('3905-14-0097',reference=reference,title='Works',supplier_name_raw='Alpha Construction Ltd.',award_value='$100,000',source='original_composite'),overwrite=True)
    db.upsert_row(conn,CompositeAward('4000-14-0001',reference=reference,title='Different',supplier_name_raw='Beta Ltd.',award_value='$9,000',source='original_composite'),overwrite=True)
    conn.commit()
    evidence=load_evidence(bundle,registry)
    metadata=archive_fixture()[1]
    return dict(conn=conn,evidence=evidence,document_number=publisher['Solicitation_Document_Number'],archive_source_sha256=metadata['sha256'],archive_row_ordinal=0,report_url=url,contract_index=0,scope_start=0,scope_end=TEXT.index('Call No:',1))


def test_single_submission_uses_selected_contract_and_withholds_award_price(conn,tmp_path,monkeypatch):
    from toronto_bids.linking.city_native_participation import propose
    kwargs=setup_source(conn,tmp_path,monkeypatch)
    proof=propose(**kwargs)
    assert proof['call_number']=='3905-14-0097'
    assert proof['supplier_name']=='Alpha Construction Ltd.'
    assert proof['source_role']=='single_scoped_submission'
    assert proof['withheld_model_price']=='$100,000.55'
    assert proof['submission_count_quotation']=='One (1)'


def test_replay_links_only_the_native_contract_and_keeps_sibling_identity(conn,tmp_path,monkeypatch):
    from toronto_bids.linking import city_native_participation as native
    from toronto_bids.linking.procurement import resolve_procurements
    kwargs=setup_source(conn,tmp_path,monkeypatch);proof=native.propose(**kwargs)
    registry=tmp_path/'native.json';registry.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[proof])));monkeypatch.setattr(native,'PATH',registry)
    resolve_procurements(conn)
    doc=proof['document_number'];pid=conn.execute('SELECT procurement_id FROM solicitation WHERE document_number=?',(doc,)).fetchone()[0]
    bid=dict(conn.execute("SELECT * FROM bid WHERE source='city_native_review'").fetchone())
    assert bid['reference'] is None and bid['document_number']==doc
    assert bid['bid_price'] is None and bid['status'] is None
    assert conn.execute("SELECT procurement_id FROM composite_award WHERE call_number='3905-14-0097'").fetchone()[0]==pid
    assert conn.execute("SELECT procurement_id FROM composite_award WHERE call_number='4000-14-0001'").fetchone()[0]!=pid
    assert not conn.execute('SELECT 1 FROM solicitation_link').fetchone()
    assert not conn.execute("SELECT 1 FROM procurement_key WHERE namespace='city_council_bid'").fetchone()
    assert conn.execute('SELECT 1 FROM procurement_document WHERE procurement_id=? AND url=?',(pid,proof['report_url'])).fetchone()
    state=[tuple(r) for r in conn.execute('SELECT * FROM procurement ORDER BY id')]
    resolve_procurements(conn)
    assert [tuple(r) for r in conn.execute('SELECT * FROM procurement ORDER BY id')]==state


import pytest


@pytest.mark.parametrize('change',['mixed_count','partial_scope','sibling_contract','no_bid','source_bytes','price_changed_after_replay'])
def test_single_receipt_refuses_mixed_scopes_and_changed_source(conn,tmp_path,monkeypatch,change):
    from toronto_bids.linking import city_native_participation as native
    from toronto_bids.linking.procurement import resolve_procurements
    kwargs=setup_source(conn,tmp_path,monkeypatch)
    if change=='mixed_count':
        text=TEXT.replace('One (1)','One (1) Formal Bid, one (1) Non-Compliant Bid for a total of two (2) Bids',1)
        conn.execute('UPDATE background_pdf SET text=?',(text,));kwargs['scope_end']=text.index('Call No:',1)
    elif change=='partial_scope':kwargs['scope_end']=TEXT.index('APPENDIX')
    elif change=='sibling_contract':kwargs['contract_index']=1
    elif change=='no_bid':
        raw=json.loads(conn.execute('SELECT result_json FROM extraction_cache').fetchone()[0]);raw['contracts'][0]['bids'][0]['status']='no_bid'
        conn.execute('UPDATE extraction_cache SET result_json=?',(json.dumps(raw),))
    elif change=='source_bytes':(tmp_path/'composite.pdf').write_bytes(b'changed')
    else:
        proof=native.propose(**kwargs);registry=tmp_path/'native.json';registry.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[proof])));monkeypatch.setattr(native,'PATH',registry)
        resolve_procurements(conn);conn.execute("UPDATE bid SET bid_price='$100,000.55' WHERE source='city_native_review'")
        with pytest.raises(ValueError,match='explicit repair'):resolve_procurements(conn)
        return
    with pytest.raises(ValueError):native.propose(**kwargs)


def test_public_export_retains_native_receipt_provenance_and_original_report(conn,tmp_path,monkeypatch):
    from toronto_bids.linking import city_native_participation as native
    from toronto_bids.linking.procurement import resolve_procurements
    from toronto_bids.export.document import build_export_document
    kwargs=setup_source(conn,tmp_path,monkeypatch);proof=native.propose(**kwargs)
    registry=tmp_path/'native.json';registry.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[proof])));monkeypatch.setattr(native,'PATH',registry)
    resolve_procurements(conn);public=build_export_document(conn)
    sol=next(s for s in public['solicitations'] if s['document_number']==proof['document_number'])
    assert sol['bids'][0]['bidder_name_raw']=='Alpha Construction Ltd.'
    assert sol['bids'][0]['bid_price'] is None
    assert proof['report_url'] in [d['url'] for d in sol['documents']]
    assert public['city_native_bid_evidence'][0]['proof_json']['call_number']=='3905-14-0097'


def test_explicit_next_field_ends_the_submission_count_without_a_blank_line(conn,tmp_path,monkeypatch):
    from toronto_bids.linking.city_native_participation import propose
    kwargs=setup_source(conn,tmp_path,monkeypatch)
    text=TEXT.replace('One (1)\n\nAPPENDIX','One (1)\nFinancial Impact:\nThe total potential award is $999.\n\nAPPENDIX',1)
    conn.execute('UPDATE background_pdf SET text=?',(text,));kwargs['scope_end']=text.index('Call No:',1)
    assert propose(**kwargs)['submission_count_quotation']=='One (1)'


@pytest.mark.parametrize('old,new',[
    ('Recommended Bidder:', 'Recommended Bidder'),
    ('Recommended Bidder:', 'Recommended Bidders:'),
    ('One (1)\n\nAPPENDIX', 'One (1)\nFinancial Impact\nAward funding.\n\nAPPENDIX'),
])
def test_literal_source_heading_typography_keeps_single_receipt_scope(conn,tmp_path,monkeypatch,old,new):
    from toronto_bids.linking.city_native_participation import propose
    kwargs=setup_source(conn,tmp_path,monkeypatch);text=TEXT.replace(old,new,1)
    conn.execute('UPDATE background_pdf SET text=?',(text,));kwargs['scope_end']=text.index('Call No:',1)
    assert propose(**kwargs)['supplier_name']=='Alpha Construction Ltd.'


def test_all_cached_price_schedule_observations_are_accounted_for_one_party(conn,tmp_path,monkeypatch):
    from toronto_bids.linking.city_native_participation import propose
    kwargs=setup_source(conn,tmp_path,monkeypatch);raw=json.loads(conn.execute('SELECT result_json FROM extraction_cache').fetchone()[0])
    raw['contracts'].append(dict(raw['contracts'][0],bids=[dict(supplier_name='Alpha Construction Limited',amount_raw='$2,000',status='compliant')]))
    conn.execute('UPDATE extraction_cache SET result_json=?',(json.dumps(raw),));kwargs.pop('contract_index')
    proof=propose(**kwargs,contract_indices=[0,2])
    assert proof['contract_indices']==[0,2]
    assert len(proof['withheld_model_observations'])==2
    assert proof['supplier_name']=='Alpha Construction Ltd.'
    with pytest.raises(ValueError):propose(**kwargs,contract_indices=[0])


def test_price_schedule_qualification_remains_source_context_not_supplier_name(conn,tmp_path,monkeypatch):
    from toronto_bids.linking.city_native_participation import propose
    kwargs=setup_source(conn,tmp_path,monkeypatch)
    text=TEXT.replace('Alpha Construction Ltd.\nContract Award Value:',
        'Alpha Construction Ltd. (Price Schedule A: Fleet Services),\n(Price Schedule B – Facilities Operations)\nContract Award Value:')
    conn.execute('UPDATE background_pdf SET text=?',(text,));kwargs['scope_end']=text.index('Call No:',1)
    proof=propose(**kwargs)
    assert proof['supplier_name']=='Alpha Construction Ltd.'
    assert 'Price Schedule B' in proof['recommendation_scope_qualification']
    assert 'Price Schedule A' in proof['recommendation_quotation']


def test_a_second_recommended_firm_cannot_be_dropped_as_scope_qualification(conn,tmp_path,monkeypatch):
    from toronto_bids.linking.city_native_participation import propose
    kwargs=setup_source(conn,tmp_path,monkeypatch)
    text=TEXT.replace('Alpha Construction Ltd.\nContract Award Value:',
        'Alpha Construction Ltd. (Price Schedule A: Fleet), Beta Ltd. (Price Schedule B: Parks)\nContract Award Value:')
    conn.execute('UPDATE background_pdf SET text=?',(text,));kwargs['scope_end']=text.index('Call No:',1)
    with pytest.raises(ValueError):propose(**kwargs)


def test_unapplied_native_review_waits_for_its_original_source_context(conn,tmp_path,monkeypatch):
    from toronto_bids.linking import city_native_participation as native
    from toronto_bids.linking.procurement import resolve_procurements
    kwargs=setup_source(conn,tmp_path,monkeypatch);proof=native.propose(**kwargs)
    registry=tmp_path/'native.json';registry.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[proof])));monkeypatch.setattr(native,'PATH',registry)
    conn.execute('DELETE FROM background_pdf');conn.execute('DELETE FROM council_item')
    resolve_procurements(conn)
    assert conn.execute('SELECT COUNT(*) FROM city_native_bid_evidence').fetchone()[0]==0
