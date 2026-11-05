"""Reviewed cache replay preserves source roles, existing facts and archive history."""
import hashlib,json

import pytest

from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.models import BackgroundPdf,CouncilItem,Solicitation
from toronto_bids.store import db

DOC='1234567890';REF='2023.BA1.1';TITLE='Award of Request for Proposal Doc1234567890'
TEXT='Solicitation Number: Doc1234567890\nNumber of Bids: Two\nBid Price (including H.S.T.)\nAlpha Services Ltd. $120.00*\nBeta Services Ltd. Pricing not submitted\n'
TABLE='Number of Bids: Two Bid Price (including H.S.T.) Alpha Services Ltd. $120.00* Beta Services Ltd. Pricing not submitted'


def setup_review(conn,tmp_path):
    from toronto_bids.linking.city_crosswalk import digest
    pdf=tmp_path/'report.pdf';pdf.write_bytes(b'%PDF-held fixture source')
    sha=hashlib.sha256(pdf.read_bytes()).hexdigest();url='https://www.toronto.ca/legdocs/mmis/2023/ba/bgrd/backgroundfile-1.pdf'
    raw={'contracts':[{'reference':'Doc'+DOC,'bids':[{'supplier_name':'Alpha Services Ltd.','amount_raw':'$120.00','status':'compliant'},{'supplier_name':'Beta Services Ltd.','amount_raw':None,'status':'non_compliant'}]}]};encoded=json.dumps(raw)
    proof=dict(document_number=DOC,reference=REF,source='ba_report',report_url=url,report_sha256=sha,text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),extractor_version=EXTRACTOR_VERSION,result_sha256=hashlib.sha256(encoded.encode()).hexdigest(),contract_sha256=digest(raw['contracts'][0]),contract_index=0,contract_count=1,agenda_title=TITLE,identity={'kind':'primary_document'},source_role='submitted tender/proposal',scope_justification='Two actual proposal submissions in one labelled table.',table_quotation=TABLE,bids=[dict(raw_bid_index=0,name_parts=['Alpha Services Ltd.'],row_quotation='Alpha Services Ltd. $120.00*',bid_price='$120.00*',hst_basis='including',price_header='(including H.S.T.)',role='submitted_bid'),dict(raw_bid_index=1,name_parts=['Beta Services Ltd.'],row_quotation='Beta Services Ltd. Pricing not submitted',bid_price=None,hst_basis=None,price_header=None,role='submitted_bid')])
    registry=tmp_path/'reviews.json';registry.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[proof])))
    for row in [Solicitation(DOC),CouncilItem(REF,title=TITLE),BackgroundPdf(url,reference=REF,kind='bgrd',sha256=sha,text=TEXT,local_path=str(pdf))]:db.upsert_row(conn,row,overwrite=True)
    db.mark_extracted(conn,sha,EXTRACTOR_VERSION,encoded);conn.commit()
    return proof,registry


def test_replay_uses_source_prices_and_preserves_raw_cache_and_history(conn,tmp_path):
    from toronto_bids.linking.city_cached_bids import replay_reviewed_bids
    proof,path=setup_review(conn,tmp_path)
    before=[tuple(r) for r in conn.execute('SELECT * FROM extraction_cache')]
    assert replay_reviewed_bids(conn,registry=path)==2
    bids=[dict(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')]
    assert [(b['bidder_name_raw'],b['bid_price'],b['hst_basis'],b['status']) for b in bids]==[('Alpha Services Ltd.','$120.00*','including',None),('Beta Services Ltd.',None,None,None)]
    assert all(b['document_number']==DOC and b['reference']==REF for b in bids)
    assert [tuple(r) for r in conn.execute('SELECT * FROM extraction_cache')]==before
    ids=[(b['id'],b['first_seen']) for b in bids]
    replay_reviewed_bids(conn,registry=path)
    assert [tuple(r) for r in conn.execute('SELECT id,first_seen FROM bid ORDER BY id')]==ids
    assert conn.execute('SELECT COUNT(*) FROM city_bid_evidence').fetchone()[0]==1


def test_normal_resolution_replays_reviews_and_assigns_proven_identity(conn,tmp_path,monkeypatch):
    from toronto_bids.linking import city_cached_bids
    from toronto_bids.linking.procurement import resolve_procurements
    proof,path=setup_review(conn,tmp_path);monkeypatch.setattr(city_cached_bids,'PATH',path)
    resolve_procurements(conn)
    rows=conn.execute('SELECT * FROM bid').fetchall()
    assert len(rows)==2
    target=conn.execute('SELECT procurement_id FROM solicitation WHERE document_number=?',(DOC,)).fetchone()[0]
    assert all(r['procurement_id']==target for r in rows)


def test_explicit_backfill_preserves_reviewed_prices_and_withheld_status(conn,tmp_path,monkeypatch):
    from toronto_bids.linking import city_cached_bids
    from toronto_bids.extraction import backfill_from_extraction
    proof,path=setup_review(conn,tmp_path);monkeypatch.setattr(city_cached_bids,'PATH',path)
    backfill_from_extraction(conn,'ba_report')
    assert [tuple(r) for r in conn.execute('SELECT bidder_name_raw,bid_price,status FROM bid ORDER BY id')]==[('Alpha Services Ltd.','$120.00*',None),('Beta Services Ltd.',None,None)]
    assert conn.execute('SELECT COUNT(*) FROM city_bid_evidence').fetchone()[0]==1


@pytest.mark.parametrize('change',['cache','text','pdf','title','reference','registry_removed','ledger'])
def test_changed_review_context_keeps_previously_verified_facts(conn,tmp_path,change):
    from toronto_bids.linking.city_cached_bids import replay_reviewed_bids
    proof,path=setup_review(conn,tmp_path);replay_reviewed_bids(conn,registry=path);conn.commit()
    before=[tuple(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')]
    if change=='cache':
        raw=json.loads(conn.execute('SELECT result_json FROM extraction_cache').fetchone()[0]);raw['contracts'][0]['bids'][0]['amount_raw']='$999'
        db.mark_extracted(conn,proof['report_sha256'],EXTRACTOR_VERSION,json.dumps(raw))
    if change=='text':conn.execute("UPDATE background_pdf SET text=text||' changed'")
    if change=='pdf':(tmp_path/'report.pdf').write_bytes(b'different PDF')
    if change=='title':conn.execute("UPDATE council_item SET title='Different procurement'")
    if change=='reference':conn.execute("UPDATE background_pdf SET reference='2023.BA2.1'")
    if change=='registry_removed':path.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[])))
    if change=='ledger':conn.execute("UPDATE city_bid_evidence SET document_number='9999999999'")
    conn.commit()
    with pytest.raises(ValueError):replay_reviewed_bids(conn,registry=path)
    assert [tuple(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')]==before


@pytest.mark.parametrize('change',['ancestor','table_doc_conflict','wrong_price','crossed_rows','invented_tax','no_bid','truncated_price'])
def test_unproven_source_roles_and_relations_are_refused_before_any_write(conn,tmp_path,change):
    from toronto_bids.linking.city_cached_bids import replay_reviewed_bids
    from toronto_bids.linking.city_crosswalk import digest
    proof,path=setup_review(conn,tmp_path)
    raw=json.loads(conn.execute('SELECT result_json FROM extraction_cache').fetchone()[0])
    source=TEXT
    if change=='ancestor':raw['contracts'][0]['reference']='RFSQ Doc9999999999'
    if change=='table_doc_conflict':
        source=TEXT.replace('Number of Bids: Two','Table 2: Bids Received for Doc9999999999\nNumber of Bids: Two')
        proof['table_quotation']=TABLE.replace('Number of Bids: Two','Table 2: Bids Received for Doc9999999999 Number of Bids: Two')
    if change=='wrong_price':proof['bids'][0]['bid_price']='$999'
    if change=='truncated_price':proof['bids'][0]['bid_price']='$120'
    if change=='crossed_rows':
        source=TEXT.replace('Beta Services Ltd. Pricing not submitted','Beta Services Ltd. $150.00')
        proof['table_quotation']=TABLE.replace('Beta Services Ltd. Pricing not submitted','Beta Services Ltd. $150.00')
        proof['bids'][0].update(row_quotation='Alpha Services Ltd. $120.00* Beta Services Ltd. $150.00',bid_price='$150.00')
        proof['bids'][1]['row_quotation']='Beta Services Ltd. $150.00'
    if change=='invented_tax':proof['bids'][0]['hst_basis']='excluding'
    if change=='no_bid':raw['contracts'][0]['bids'][0]['status']='no_bid'
    encoded=json.dumps(raw);db.mark_extracted(conn,proof['report_sha256'],EXTRACTOR_VERSION,encoded)
    conn.execute('UPDATE background_pdf SET text=?',(source,))
    proof['text_sha256']=hashlib.sha256(source.encode()).hexdigest();proof['result_sha256']=hashlib.sha256(encoded.encode()).hexdigest();proof['contract_sha256']=digest(raw['contracts'][0])
    path.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[proof])))
    conn.commit()
    with pytest.raises(ValueError):replay_reviewed_bids(conn,registry=path)
    assert conn.execute('SELECT COUNT(*) FROM bid').fetchone()[0]==0
    assert conn.execute('SELECT COUNT(*) FROM city_bid_evidence').fetchone()[0]==0


def test_reviewed_evidence_travels_with_release_when_local_pdf_is_absent(conn,tmp_path):
    from toronto_bids.linking.city_cached_bids import replay_reviewed_bids
    proof,path=setup_review(conn,tmp_path);replay_reviewed_bids(conn,registry=path)
    (tmp_path/'report.pdf').unlink()
    assert replay_reviewed_bids(conn,registry=path)==2
    assert conn.execute('SELECT COUNT(*) FROM bid').fetchone()[0]==2


def test_late_canonical_conflict_rolls_back_new_projections_and_evidence(conn,tmp_path,monkeypatch):
    from toronto_bids.linking import city_cached_bids
    from toronto_bids.linking.procurement import resolve_procurements
    from toronto_bids.models import Bid
    proof,path=setup_review(conn,tmp_path)
    empty=tmp_path/'empty.json';empty.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[])))
    monkeypatch.setattr(city_cached_bids,'PATH',empty)
    for doc in ['3333333333','4444444444']:db.upsert_row(conn,Solicitation(doc),overwrite=True)
    db.upsert_row(conn,Bid('Existing',reference='2024.BA999.1',document_number='3333333333',source='legacy'),overwrite=True)
    resolve_procurements(conn);conn.commit()
    db.upsert_row(conn,Bid('Conflicting',reference='2024.BA999.1',document_number='4444444444',source='legacy'),overwrite=True);conn.commit()
    before=[tuple(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')]
    monkeypatch.setattr(city_cached_bids,'PATH',path)
    with pytest.raises(ValueError,match='ambiguous council reference'):resolve_procurements(conn)
    assert [tuple(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')]==before
    assert conn.execute('SELECT COUNT(*) FROM city_bid_evidence').fetchone()[0]==0


def test_committed_review_accounts_for_the_entire_frozen_inventory():
    from pathlib import Path
    from toronto_bids.linking.city_cached_bids import PATH
    registry=json.loads(PATH.read_text())
    report=json.loads((Path(__file__).resolve().parents[2]/'docs/research/city-cached-bid-review-2026-11-05.json').read_text())
    inventory=report['frozen_inventory']
    assert hashlib.sha256((json.dumps(inventory,indent=2,ensure_ascii=False)+'\n').encode()).hexdigest()==registry['inventory_sha256']
    assert len(inventory['cases'])==len(report['cases'])==106
    assert report['summary']['outcomes']=={'recovered':50,'identity_unresolved':43,'ancestor_event_only':2,'scope_or_source_hold':11}
    recovered={c['document_number'] for c in report['cases'] if c['outcome']=='recovered'}
    # This frozen report is historical: later reviews may append recoveries.
    original=[p for p in registry['reviews'] if p['document_number'] in recovered]
    assert recovered=={p['document_number'] for p in original}
    for p in original:
        case=next(c for c in inventory['cases'] if c['document_number']==p['document_number'])
        assert any(r['url']==p['report_url'] and r['sha256']==p['report_sha256'] and r['result_sha256']==p['result_sha256'] for r in case['reports'])
    assert sum(len(p['bids']) for p in original)==300
    # Ancestor qualifications, conflicting table identity and unsupported lot/submission scopes stay held.
    assert not recovered & {'3512316731','4906085019','4566099607','1205175043','4815572097','3782552477'}
    roof=next(p for p in registry['reviews'] if p['document_number']=='3122348800')
    assert next(b for b in roof['bids'] if 'Trio' in ' '.join(b['name_parts']))['bid_price']=='$ 82,820,035,000.00'
    assert all(b['hst_basis'] is None for b in roof['bids'])  # No HST basis is stated by its bid table.


def test_moved_archive_uses_hash_checked_conventional_pdf_without_rewriting_source_path(conn,tmp_path,monkeypatch):
    from toronto_bids import config
    from toronto_bids.linking.city_cached_bids import replay_reviewed_bids
    proof,path=setup_review(conn,tmp_path)
    data=tmp_path/'data';held=data/'documents'/'council'/'backgroundfile-1.pdf'
    held.parent.mkdir(parents=True)
    (tmp_path/'report.pdf').rename(held)
    conn.execute("UPDATE background_pdf SET local_path='/old/workstation/report.pdf'");conn.commit()
    monkeypatch.setattr(config,'DATA_DIR',data)
    assert replay_reviewed_bids(conn,registry=path)==2
    assert conn.execute('SELECT local_path FROM background_pdf').fetchone()[0]=='/old/workstation/report.pdf'
    held.write_bytes(b'wrong source')
    with pytest.raises(ValueError,match='PDF bytes changed'):replay_reviewed_bids(conn,registry=path)


def test_json_export_includes_portable_review_and_source_quotation(conn,tmp_path,monkeypatch):
    from toronto_bids.linking import city_cached_bids
    from toronto_bids.export.json_export import export_json
    proof,path=setup_review(conn,tmp_path);monkeypatch.setattr(city_cached_bids,'PATH',path)
    target=tmp_path/'bids.json';export_json(conn,target)
    evidence=json.loads(target.read_text())['city_bid_evidence']
    assert len(evidence)==1 and evidence[0]['bid_count']==2
    assert evidence[0]['proof_json']==proof


@pytest.mark.parametrize('keep_unit',[True,False])
def test_reviewed_hourly_rate_retains_its_unit_instead_of_becoming_a_total(conn,tmp_path,keep_unit):
    from toronto_bids.linking.city_cached_bids import replay_reviewed_bids
    proof,path=setup_review(conn,tmp_path)
    source=TEXT.replace('$120.00*','$120.00/Hour')
    conn.execute('UPDATE background_pdf SET text=?',(source,));conn.commit()
    proof['text_sha256']=hashlib.sha256(source.encode()).hexdigest()
    proof['table_quotation']=TABLE.replace('$120.00*','$120.00/Hour')
    proof['bids'][0]['row_quotation']='Alpha Services Ltd. $120.00/Hour'
    proof['bids'][0]['bid_price']='$120.00/Hour' if keep_unit else '$120.00'
    path.write_text(json.dumps(dict(version=1,extractor_version=EXTRACTOR_VERSION,reviews=[proof])))
    if keep_unit:
        replay_reviewed_bids(conn,registry=path)
        row=conn.execute('SELECT bid_price,bid_price_numeric FROM bid WHERE bidder_name_raw=?',('Alpha Services Ltd.',)).fetchone()
        assert tuple(row)==('$120.00/Hour',None)
    else:
        with pytest.raises(ValueError,match='price absent'):replay_reviewed_bids(conn,registry=path)


def test_followup_review_accounts_for_all_43_identity_holds_and_source_completeness():
    from pathlib import Path
    from toronto_bids.linking.city_cached_bids import PATH
    from toronto_bids.linking.city_crosswalk import PROOFS,digest
    report=json.loads((Path(__file__).resolve().parents[2]/'docs/research/city-identity-holds-review-2026-11-05.json').read_text())
    registry=json.loads(PATH.read_text());crosswalk=json.loads(PROOFS.read_text())
    assert hashlib.sha256((json.dumps(report['frozen_inventory'],indent=2)+'\n').encode()).hexdigest()==report['frozen_inventory_sha256']
    assert registry['followup_review']['inventory_sha256']==report['frozen_inventory_sha256']
    assert len(report['cases'])==43
    assert report['summary']['outcomes']=={'held':33,'identity_proven_bid_scope_hold':1,'recovered':9}
    assert {r['document_number'] for r in report['cases']}=={r['document_number'] for r in report['frozen_inventory']}
    new={r['document_number']:r for r in report['cases'] if r['outcome']=='recovered'}
    reviews={p['document_number']:p for p in registry['reviews'] if p['document_number'] in new}
    assert len(reviews)==9 and sum(len(p['bids']) for p in reviews.values())==39
    assert sum(b['bid_price'] is not None for p in reviews.values() for b in p['bids'])==23
    for d,p in reviews.items():assert digest(p)==new[d]['bid_review_sha256']
    asco=reviews['1201155002']
    assert asco['declared_coverage']=={'declared_submissions':8,'printed_named_submissions':4,'unidentified_declared_submissions':4}
    assert len(asco['bids'])==4  # No invented bidders fill the source's declared-count gap.
    assert all(b['bid_price'] is None for b in reviews['9117167066']['bids'])
    hourly=[b['bid_price'] for b in reviews['3701150197']['bids'] if b['bid_price']]
    assert len(hourly)==4 and all(p.endswith('/Hour') for p in hourly)
    original_proof_hashes={r['identity_proof_sha256'] for r in report['cases'] if r.get('identity_proof_sha256')}
    type_proofs=[p for p in crosswalk['proofs'] if digest(p) in original_proof_hashes and p['proof_class']=='reviewed_type_discrepancy']
    assert len(type_proofs)==7 and all(p['review']['source_type']!=p['review']['publisher_type'] for p in type_proofs)
