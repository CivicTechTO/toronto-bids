"""Participation, alternatives and matrix cells retain distinct source meanings."""
import hashlib,json,shutil
from pathlib import Path

import pytest

from toronto_bids import pdf_witness
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.linking import city_scoped_bids as scoped
from toronto_bids.linking.city_crosswalk import digest
from toronto_bids.models import BackgroundPdf,CouncilItem,Solicitation
from toronto_bids.store import db

DOC='1234567890';REF='2023.BA1.1';TITLE='Award of Request for Quotation Doc1234567890'


def make_pdf(path):
    entries=[(40,20,'Doc1234567890'),(40,40,'Part A'),(40,65,'Alpha Ltd.'),(180,65,'Bid 1'),(300,65,'10%'),(40,90,'Beta Inc.'),(300,90,'No Bid'),(40,115,'Alpha Ltd.'),(180,115,'Bid 2'),(300,115,'15%'),(40,180,'Part B'),(40,205,'Alpha Ltd.'),(300,205,'$100.00'),(40,230,'Beta Inc.'),(300,230,'$120.00'),(300,45,'Percentage Off MSRP'),(300,185,'Bid Price including HST')]
    stream='\n'.join(f'BT /F1 10 Tf 1 0 0 1 {x} {792-y} Tm ({t}) Tj ET' for x,y,t in entries).encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    result=b'%PDF-1.4\n';offsets=[]
    for i,obj in enumerate(objects,1):offsets.append(len(result));result+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(result);result+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()+b''.join(f'{off:010} 00000 n \n'.encode() for off in offsets)+f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode();path.write_bytes(result)


def setup(conn,tmp_path):
    pdf=tmp_path/'source.pdf';make_pdf(pdf);sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    witnesses={}
    def w(key,box,text):witnesses[key]=dict(page=1,box=list(box),text=text);return key
    def area(box):return dict(page=1,box=list(box))
    w('a',[35,30,100,43],'Part A');w('b',[35,170,100,183],'Part B')
    w('percent',[295,35,430,47],'Percentage Off MSRP');w('money',[295,175,460,187],'Bid Price including HST')
    responses=[]
    for key,y,name,value,scope,kind,alt in [('a1',65,'Alpha Ltd.','10%','a','submitted','Bid 1'),('a2',90,'Beta Inc.','No Bid','a','no_bid',None),('a3',115,'Alpha Ltd.','15%','a','submitted','Bid 2'),('b1',205,'Alpha Ltd.','$100.00','b','submitted',None),('b2',230,'Beta Inc.','$120.00','b','submitted',None)]:
        nw=w(key+'-name',[35,y-10,105,y+3],name);vw=w(key+'-value',[295,y-10,350,y+3],value)
        rowtext=name+(' '+alt if alt else '')+' '+value
        rw=w(key+'-row',[35,y-10,350,y+3],rowtext)
        r=dict(key=key,scope=scope,name=name,name_witness=nw,row_witness=rw,party_area=area([35,y-10,350,y+3]),participant='alpha' if name.startswith('Alpha') else 'beta',kind=kind,status=None,prices=[])
        if alt:
            r.update(alternative_label=alt,alternative_witness=w(key+'-alt',[175,y-10,220,y+3],alt),alternative_area=area([35,y-10,350,y+3]))
        if kind=='submitted':r['prices']=[dict(key='quoted',raw=value,value_witness=vw,header_witness='percent' if scope=='a' else 'money',kind='discount_percentage' if scope=='a' else 'money',unit='percent_off_MSRP' if scope=='a' else 'total',hst_basis=None if scope=='a' else 'including',tax_witness='money')]
        responses.append(r)
    contracts=[dict(reference='Doc'+DOC,bids=[dict(supplier_name='Alpha Ltd.'),dict(supplier_name='Beta Inc.')])];encoded=json.dumps(dict(contracts=contracts))
    proof=dict(document_number=DOC,reference=REF,source='ba_report',report_url='https://example.test/source.pdf',report_sha256=sha,text_sha256=hashlib.sha256(('Doc'+DOC).encode()).hexdigest(),result_sha256=hashlib.sha256(encoded.encode()).hexdigest(),extractor_version=EXTRACTOR_VERSION,contract_count=1,contracts_sha256=digest(contracts),agenda_title=TITLE,identity=dict(kind='primary_document'),participants=[dict(key='alpha',name='Alpha Ltd.',name_witness='a1-name'),dict(key='beta',name='Beta Inc.',name_witness='a2-name')],scopes=[dict(key='a',kind='part',label='Part A',header_witnesses=['a'],leaf_header_witness='a',areas=[area([30,25,500,140])]),dict(key='b',kind='part',label='Part B',header_witnesses=['b'],leaf_header_witness='b',areas=[area([30,165,500,250])])],responses=responses,witnesses=witnesses,cache_contract_bid_counts=[2],cache_accounting=[dict(contract_index=0,bid_index=i,reason='Replaced flattened cache with source-reviewed matrix cells.') for i in range(2)])
    path=tmp_path/'reviews.json';path.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[proof])))
    for r in [Solicitation(DOC),CouncilItem(REF,title=TITLE),BackgroundPdf(proof['report_url'],reference=REF,kind='bgrd',sha256=sha,local_path=str(pdf),text='Doc'+DOC)]:db.upsert_row(conn,r,overwrite=True)
    db.mark_extracted(conn,sha,EXTRACTOR_VERSION,encoded);conn.commit()
    return proof,path,pdf


def test_no_bid_and_price_variants_do_not_multiply_participating_firms(conn,tmp_path):
    p,path,pdf=setup(conn,tmp_path)
    quotes=pdf_witness.verify(p['witnesses'],pdf,p['report_sha256'])
    bids=scoped.projection(p,quotes,DOC)
    assert len(bids)==2 and all(b.bid_price is None and b.status is None for b in bids)
    assert [r['kind'] for r in p['responses']].count('submitted')==4
    assert len(p['responses'])==5


@pytest.mark.parametrize('change',['scope','supplier','price','alternative','no_bid','percentage','cache_accounting'])
def test_crossed_source_cells_and_fabricated_semantics_are_refused(conn,tmp_path,change):
    p,path,pdf=setup(conn,tmp_path);quotes=pdf_witness.verify(p['witnesses'],pdf,p['report_sha256'])
    r=p['responses'][0]
    if change=='scope':r['scope']='b'
    if change=='supplier':r['participant']='beta'
    if change=='price':r['prices'][0].update(raw='$100.00',value_witness='b1-value')
    if change=='alternative':r['alternative_label']='Bid 2'
    if change=='no_bid':p['responses'][1]['kind']='submitted'
    if change=='percentage':r['prices'][0].update(kind='money',unit='total')
    if change=='cache_accounting':p['cache_accounting'].pop()
    with pytest.raises(ValueError):scoped.projection(p,quotes,DOC)


def test_persisted_scopes_prices_and_receipts_are_idempotent_and_portable(conn,tmp_path,monkeypatch):
    from toronto_bids.linking.procurement import resolve_procurements
    p,path,pdf=setup(conn,tmp_path);monkeypatch.setattr(scoped,'PATH',path)
    resolve_procurements(conn);conn.commit()
    assert conn.execute('SELECT COUNT(*) FROM bid').fetchone()[0]==2
    assert conn.execute('SELECT COUNT(*) FROM bid_response').fetchone()[0]==5
    assert conn.execute("SELECT COUNT(*) FROM bid_response WHERE response_kind='submitted'").fetchone()[0]==4
    assert conn.execute('SELECT COUNT(*) FROM bid_response_price').fetchone()[0]==4
    assert conn.execute("SELECT COUNT(*) FROM bid_response_price WHERE value_kind='discount_percentage' AND value_numeric IS NULL").fetchone()[0]==2
    before={table:[tuple(r) for r in conn.execute(f'SELECT id,first_seen FROM {table} ORDER BY id')] for table in ('bid','bid_scope','bid_response','bid_response_price')}
    pdf.unlink();resolve_procurements(conn)
    assert all([tuple(r) for r in conn.execute(f'SELECT id,first_seen FROM {table} ORDER BY id')]==rows for table,rows in before.items())
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_export_counts_firms_submitted_cells_and_no_bid_separately(conn,tmp_path,monkeypatch):
    from toronto_bids.export.json_export import export_json
    p,path,pdf=setup(conn,tmp_path);monkeypatch.setattr(scoped,'PATH',path)
    out=tmp_path/'bids.json';export_json(conn,out);doc=json.loads(out.read_text())
    summary=doc['solicitations'][0]['bid_scope_summary']
    assert summary==dict(participating_firms=2,response_observations=5,submitted_response_cells=4,resolved_scoped_submissions=4,explicit_no_bid_cells=1,unscoped_outcomes=0,unresolved_party_cells=0,price_variants=4)
    assert len(doc['solicitations'][0]['bids'])==2
    assert len(doc['city_scoped_bid_evidence'])==1


@pytest.mark.parametrize('change',['text','cache','pdf','rectangle','removed_review','ledger'])
def test_changed_context_never_replaces_archived_scoped_facts(conn,tmp_path,change):
    p,path,pdf=setup(conn,tmp_path);scoped.replay_scoped_bids(conn,registry=path);conn.commit()
    before={t:[tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')] for t in ('bid','bid_scope','bid_response','bid_response_price')}
    if change=='text':conn.execute("UPDATE background_pdf SET text=text||'changed'")
    if change=='cache':conn.execute("UPDATE extraction_cache SET result_json='{}'")
    if change=='pdf':pdf.write_bytes(b'changed original')
    if change=='rectangle':
        p['responses'][0]['prices'][0]['value_witness']='b1-value'
        path.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[p])))
    if change=='removed_review':path.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[])))
    if change=='ledger':conn.execute('UPDATE city_scoped_bid_evidence SET response_count=999')
    conn.commit()
    with pytest.raises(ValueError):scoped.replay_scoped_bids(conn,registry=path)
    assert all([tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')]==rows for t,rows in before.items())


def test_unknown_source_party_keeps_its_price_without_inventing_a_firm(conn,tmp_path,monkeypatch):
    from toronto_bids.export.json_export import export_json
    p,path,pdf=setup(conn,tmp_path)
    p['participants']=[v for v in p['participants'] if v['key']!='beta']
    for r in p['responses']:
        if r['participant']=='beta':r['participant']=None;r['identity_reason']='Original source party identity remains unresolved.'
    path.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[p])))
    monkeypatch.setattr(scoped,'PATH',path)
    out=tmp_path/'export.json';export_json(conn,out)
    sol=json.loads(out.read_text())['solicitations'][0]
    assert len(sol['bids'])==1
    assert sol['bid_scope_summary']['participating_firms']==1
    assert sol['bid_scope_summary']['unresolved_party_cells']==1
    assert conn.execute("SELECT COUNT(*) FROM bid_response_price p JOIN bid_response r ON r.id=p.response_id WHERE r.bid_id IS NULL").fetchone()[0]==1


def test_explicit_backfill_preserves_scoped_children_and_parent_history(conn,tmp_path,monkeypatch):
    from toronto_bids.extraction import backfill_from_extraction
    p,path,pdf=setup(conn,tmp_path);monkeypatch.setattr(scoped,'PATH',path)
    scoped.replay_scoped_bids(conn);conn.commit()
    before={t:[tuple(r) for r in conn.execute(f'SELECT id,first_seen FROM {t} ORDER BY id')] for t in ('bid','bid_scope','bid_response','bid_response_price')}
    backfill_from_extraction(conn,'ba_report')
    assert all([tuple(r) for r in conn.execute(f'SELECT id,first_seen FROM {t} ORDER BY id')]==rows for t,rows in before.items())


def test_late_identity_conflict_rolls_back_all_scoped_tables(conn,tmp_path,monkeypatch):
    from toronto_bids.linking.procurement import resolve_procurements
    from toronto_bids.models import Bid
    p,path,pdf=setup(conn,tmp_path)
    empty=tmp_path/'empty.json';empty.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[])))
    monkeypatch.setattr(scoped,'PATH',empty)
    for d in ('3333333333','4444444444'):db.upsert_row(conn,Solicitation(d),overwrite=True)
    db.upsert_row(conn,Bid('Existing',reference='2024.BA999.1',document_number='3333333333',source='legacy'),overwrite=True)
    resolve_procurements(conn);conn.commit()
    db.upsert_row(conn,Bid('Conflicting',reference='2024.BA999.1',document_number='4444444444',source='legacy'),overwrite=True);conn.commit()
    before=[tuple(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')]
    monkeypatch.setattr(scoped,'PATH',path)
    with pytest.raises(ValueError,match='ambiguous council reference'):resolve_procurements(conn)
    assert [tuple(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')]==before
    assert all(conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]==0 for t in ('bid_scope','bid_response','bid_response_price','city_scoped_bid_evidence'))


def test_outcomes_cannot_be_copied_to_another_supplier_or_scope(conn,tmp_path):
    p,path,pdf=setup(conn,tmp_path);quotes=pdf_witness.verify(p['witnesses'],pdf,p['report_sha256'])
    # An explicitly scoped source outcome may be retained for its own party.
    no_bid=p['responses'][1];no_bid.update(status='No Bid',status_witness='a2-row')
    p['scopes'][0]['outcome_bindings']=[dict(party='beta',witness='a2-row',alternative=None)]
    assert len(scoped.projection(p,quotes,DOC))==2
    p['responses'][-1].update(status='No Bid',status_witness='a2-row')
    with pytest.raises(ValueError,match='supplier and scope'):scoped.projection(p,quotes,DOC)


def test_pdf_witness_cache_does_not_hide_changed_original_bytes(conn,tmp_path):
    p,path,pdf=setup(conn,tmp_path)
    pdf_witness.verify(p['witnesses'],pdf,p['report_sha256'])
    pdf.write_bytes(b'changed PDF after first crop')
    with pytest.raises(ValueError,match='PDF bytes changed'):pdf_witness.verify(p['witnesses'],pdf,p['report_sha256'])


def test_committed_scope_review_accounts_for_the_whole_eleven_case_inventory():
    report=json.loads((Path(__file__).resolve().parents[2]/'docs/research/city-scoped-bid-review-2026-11-05.json').read_text())
    bundle=json.loads(scoped.PATH.read_text());cases={c['document_number']:c for c in report['cases']}
    expected_sha=hashlib.sha256((json.dumps(report['frozen_inventory'],indent=2,ensure_ascii=False)+'\n').encode()).hexdigest()
    assert expected_sha==bundle['inventory_sha256']==report['frozen_inventory_sha256']
    assert len(cases)==len(report['frozen_inventory'])==11
    proofs=[p for p in bundle['reviews'] if p['document_number'] in cases]
    assert len(proofs)==11 and all(digest(p)==cases[p['document_number']]['proof_sha256'] for p in proofs)
    assert sum(len(p['participants']) for p in proofs)==93
    assert sum(len(p['scopes']) for p in proofs)==49
    rows=[r for p in proofs for r in p['responses']]
    assert len(rows)==321 and sum(r['kind']=='submitted' for r in rows)==300
    assert sum(r['kind']=='no_bid' for r in rows)==20
    assert sum(r['kind']=='unscoped_outcome' for r in rows)==1
    assert sum(bool(r.get('identity_reason')) for r in rows)==4
    assert sum(len(r['prices']) for r in rows)==104
    assert sum(v['kind']=='discount_percentage' for r in rows for v in r['prices'])==2
    assert '4566099607' not in cases  # Its contradictory document roles remain outside admission.
    big=next(p for p in proofs if p['document_number']=='4815572097')
    assert len(big['participants'])==26 and len(big['responses'])==124
    fragment=next(p for p in proofs if p['document_number']=='4044208417')
    assert len(fragment['participants'])==31 and len(fragment['responses'])==70
    assert all(r.get('participant') is None for r in fragment['responses'] if r.get('identity_reason'))
