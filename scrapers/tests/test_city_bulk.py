import hashlib
import json
import sqlite3

import pytest

from toronto_bids import city_bulk
from toronto_bids.batch_extraction import ExtractionPending
from toronto_bids.sources import city_agendas
from toronto_bids.extract import EXTRACTOR_VERSION


def archive():
    conn=sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE solicitation(document_number TEXT,procurement_id INTEGER);'
        'CREATE TABLE award(document_number TEXT,source TEXT);'
        'CREATE TABLE bid(document_number TEXT,procurement_id INTEGER);'
        'CREATE TABLE extraction_cache(sha256 TEXT,extractor_version TEXT,result_json TEXT);'
        "INSERT INTO solicitation VALUES('3987477359',1);")
    return conn


def report():
    return dict(url='https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-1.pdf',
        sha256='a'*64,text='Award of Doc3987477359\nThe RFP closed and proposals were received\nfrom the following firms:\nAlberici Constructors Ltd.\nMaple Reinders Limited\n',
        items=[dict(reference='2024.GG14.9',meeting='2024.GG14',title='Award of Doc3987477359',source_sha256='b'*64)],
        result=dict(contracts=[dict(reference='Doc3987477359',stage='single_stage',prequalified=[],invited_to_tender=[],
            bids=[dict(supplier_name='Alberici Constructors Ltd.',amount_raw='$10',status='compliant'),
                  dict(supplier_name='Maple Reinders Limited',amount_raw='$20',status='not_stated')])]))


def test_bulk_participation_requires_literal_current_event_and_submitted_firm_list():
    accepted,held=city_bulk.propose(archive(),report())
    assert not held and accepted[0]['names']==['Alberici Constructors Ltd.','Maple Reinders Limited']
    assert accepted[0]['document_number']=='3987477359'
    assert accepted[0]['prices_withheld'] and accepted[0]['statuses_withheld']
    assert 'Alberici' in accepted[0]['witness']['list_quotation']


@pytest.mark.parametrize('heading', [
    'Table 2: Summary of two (2) Bids Received for Doc3987477359',
    'Table 2: Summary of 2 Bids Received for Doc3987477359',
])
def test_counted_received_table_keeps_unpriced_named_participation(heading):
    row=report();row['text']='Award of Doc3987477359\n'+heading+'\nSupplier Name\nAlberici Constructors Ltd.\n$10\nMaple Reinders Limited\n$20\n'
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and accepted[0]['names']==['Alberici Constructors Ltd.','Maple Reinders Limited']
    assert accepted[0]['prices_withheld'] and accepted[0]['statuses_withheld']


@pytest.mark.parametrize('count',['zero (0)','1'])
def test_original_received_count_limits_model_names_even_without_model_count(count):
    row=report();row['text']='Award of Doc3987477359\nTable 2: Summary of '+count+' Bids Received for Doc3987477359\nAlberici Constructors Ltd.\nMaple Reinders Limited\n'
    assert not city_bulk.propose(archive(),row)[0]


@pytest.mark.parametrize('change', [None,'missing_receipt','wrong_table','wrong_doc'])
def test_fixed_price_table_requires_its_explicit_local_responded_statement(change):
    row=report();lead='A total of two (2) bidders responded as listed in Table 3 below:'
    heading='Table 3: Suppliers and Fixed Bid Prices for Doc3987477359'
    if change=='missing_receipt':lead='The recommended suppliers are listed in Table 3 below:'
    elif change=='wrong_table':lead=lead.replace('Table 3','Table 2')
    elif change=='wrong_doc':heading=heading.replace('3987477359','9999999999')
    row['text']='Award of Doc3987477359\nSUMMARY\n'+lead+'\n'+heading+'\nSupplier Name\nAlberici Constructors Ltd.\n$10\nMaple Reinders Limited\n$20\n'
    accepted,held=city_bulk.propose(archive(),row)
    if change is None:assert not held and len(accepted[0]['names'])==2
    else:assert not accepted


def test_export_attaches_bulk_provenance_to_only_the_proven_city_document(conn):
    from toronto_bids.models import Solicitation,BackgroundPdf
    from toronto_bids.store import db
    from toronto_bids.export.document import build_export_document
    for doc in ('3987477359','9999999999'):
        db.upsert_row(conn,Solicitation(doc,source='odata'),overwrite=True)
    url='https://www.toronto.ca/legdocs/mmis/2024/gg/bgrd/backgroundfile-1.pdf'
    db.upsert_row(conn,BackgroundPdf(url,kind='city_bulk',document_number='3987477359'),overwrite=True)
    conn.execute('INSERT INTO city_bulk_bid_evidence(proof_sha256,document_number,report_url,report_sha256,proof_json) VALUES (?,?,?,?,?)',
                 ('a'*64,'3987477359',url,'b'*64,json.dumps(dict(document_number='3987477359',report_url=url))))
    sols={s['document_number']:s for s in build_export_document(conn)['solicitations']}
    assert [d['url'] for d in sols['3987477359']['documents']]==[url]
    assert sols['9999999999']['documents']==[]


def test_multiple_lot_observations_collapse_only_to_unpriced_event_participation():
    row=report();raw=row['result']['contracts'][0]['bids']
    row['result']['contracts'][0]['declared_submissions']=2
    raw.append(dict(raw[0],amount_raw='$30'))
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and len(accepted[0]['names'])==2
    assert accepted[0]['raw_bid_observations']==3
    assert accepted[0]['prices_withheld']


def test_named_count_cannot_exceed_declared_submissions_but_partial_disclosure_survives():
    row=report();row['result']['contracts'][0]['declared_submissions']=1
    accepted,held=city_bulk.propose(archive(),row)
    assert not accepted and held[0]['reason']=='named_participation_exceeds_declared_submissions'
    row['result']['contracts'][0]['declared_submissions']=3
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and accepted[0]['declared_submissions']==3 and len(accepted[0]['names'])==2


def test_bare_model_document_key_still_requires_independent_published_doc_roles():
    row=report();row['result']['contracts'][0]['reference']='3987477359'
    assert city_bulk.propose(archive(),row)[0][0]['document_number']=='3987477359'
    row['text']=row['text'].replace('Doc3987477359','RFQ 3987-47-7359')
    accepted,held=city_bulk.propose(archive(),row)
    assert not accepted and held[0]['reason']=='conflicting_source_document_roles'


def test_native_call_only_agenda_title_can_use_a_printed_primary_report_doc_subject():
    row=report();row['items'][0]['title']='Award of Tender 24ECS-01'
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and accepted[0]['identity_anchor']=='report_subject'
    row['text']='Staff Report: Award of Tender 24ECS-01\nSUMMARY\n'+row['text']
    accepted,held=city_bulk.propose(archive(),row)
    assert not accepted and held[0]['reason']=='primary_document_absent_from_agenda_title_or_report_subject'


def test_wrapped_submission_heading_and_page_break_do_not_require_repeated_doc_labels():
    row=report()
    row['text']=row['text'].replace('The RFP','Unrelated introductory description. '*80+'\nThe RFP')
    row['text']=row['text'].replace('following firms:','following four supplier submissions proceeded through\nthe evaluation process:')
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and len(accepted[0]['names'])==2


def test_following_evaluation_stages_and_later_consultants_are_not_a_bidder_list():
    row=report()
    row['text']=row['text'].replace('following firms:','following evaluation stages:')
    assert not city_bulk.propose(archive(),row)[0]
    row=report();row['text']+='\nA consultant from Other Company reviewed the bids.\n'
    row['result']['contracts'][0]['bids'][0]['supplier_name']='Other Company'
    assert not city_bulk.propose(archive(),row)[0]


def test_historical_doc_mentions_need_distinct_primary_and_local_roles_and_consistent_footers():
    row=report()
    row['text']='Subject: Award of Doc3987477359\nSUMMARY\nDecision History: Doc1234567890.\n'+('Other background. '*100)+'\n'+row['text']
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and accepted[0]['identity_role']=='primary_subject_and_local_document'
    row['text']+='\nAward of Doc4476450575\nPage 5 of 8\n'
    assert not city_bulk.propose(archive(),row)[0]


def test_explicit_received_bid_table_admits_names_with_wrapping_and_footnotes_but_no_prices():
    row=report()
    row['text']='Award of Doc3987477359\nTable 2: Summary of Bids Received including Bid Price\nSupplier Name\nBid Price excluding HST\nAlberici Constructors\nLtd.*\n$10\nMaple Reinders Limited\n$20\nContract Award\nOther Company\n'
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and accepted[0]['witness']['kind']=='submitted_bid_table'
    assert accepted[0]['prices_withheld'] and len(accepted[0]['names'])==2
    original=row['text']
    row['text']=row['text'].replace('$10','No Bid')
    assert not city_bulk.propose(archive(),row)[0]
    row['text']=original
    row['result']['contracts'][0]['bids'][0]['supplier_name']='Other Company'
    assert not city_bulk.propose(archive(),row)[0]
    row=report();row['text']=row['text'].replace('following firms:','prequalified suppliers:')
    assert not city_bulk.propose(archive(),row)[0]


@pytest.mark.parametrize('change,reason',[
    ('copied_footer','conflicting_source_document_roles'),
    ('historical','native_call_not_unique_primary_report_subject'),
    ('wrong_title','primary_document_absent_from_agenda_title_or_report_subject'),
    ('prequalified','participation_stage_requires_review'),
    ('qualification_method','participation_stage_requires_review'),
    ('invited','participation_stage_requires_review'),
    ('no_bid','no_bid_outcome_requires_review'),
    ('invented','literal_submitted_firm_list_not_established'),
    ('historical_list','literal_submitted_firm_list_not_established'),
    ('prequalification_list','literal_submitted_firm_list_not_established'),
])
def test_bulk_exceptions_never_admit_incidental_identity_or_wrong_participation(change,reason):
    row=report();c=row['result']['contracts'][0]
    if change=='copied_footer':row['text']+='Footer Doc4476450575'
    if change=='historical':c['reference']='RFP 9117-18-7185'
    if change=='wrong_title':row['items'][0]['title']='Award of Doc1234567890'
    if change=='prequalified':c['prequalified']=['Alberici Constructors Ltd.']
    if change=='qualification_method':c['solicitation_type']='RFSQ'
    if change=='invited':c['invited_to_tender']=['Alberici Constructors Ltd.']
    if change=='no_bid':c['bids'][0]['status']='no_bid'
    if change=='invented':c['bids'][0]['supplier_name']='Invented Construction'
    if change=='historical_list':row['text']=row['text'].replace('The RFP','In RFP 9117-12-7214, the RFP')
    if change=='prequalification_list':row['text']=row['text'].replace('The RFP','Prequalification: The RFP')
    accepted,held=city_bulk.propose(archive(),row)
    assert not accepted and held[0]['reason']==reason


def test_queue_reuses_cache_deduplicates_bytes_and_never_writes_archive(tmp_path):
    conn=city_agendas.connect(tmp_path)
    data=b'%PDF-held';sha=hashlib.sha256(data).hexdigest()
    path=tmp_path/'source.pdf';path.write_bytes(data)
    with conn:
        for suffix in ('1','2'):
            conn.execute("INSERT INTO report(url,state,sha256,local_path,text) VALUES (?,'held',?,?,?)",
                ('https://www.toronto.ca/'+suffix+'.pdf',sha,str(path),'Two proposals were received from firms.'))
    conn.close()
    class Client:
        calls=0
        def extract_document(self,*args,**kwargs):
            self.calls+=1
            raise ExtractionPending('queued')
    client=Client();a=archive()
    assert city_bulk.queue(tmp_path,a,client=client)['identity_discovery_deferred']==1
    assert client.calls==0
    assert city_bulk.queue(tmp_path,a,client=client,include_unlocated=True)['pending']==1
    assert client.calls==1 and a.execute('SELECT count(*) FROM extraction_cache').fetchone()[0]==0
    a.execute('INSERT INTO extraction_cache VALUES (?,?,?)',(sha,EXTRACTOR_VERSION,json.dumps(dict(contracts=[]))))
    assert city_bulk.queue(tmp_path,a,client=client,include_unlocated=True)['cached']==1
    assert client.calls==1


def test_changed_pdf_cannot_be_sent_to_a_paid_model(tmp_path):
    conn=city_agendas.connect(tmp_path);path=tmp_path/'source.pdf';path.write_bytes(b'changed')
    with conn:conn.execute("INSERT INTO report(url,state,sha256,local_path,text) VALUES ('test','held',?,?,?)",('a'*64,str(path),'Two proposals were received.'))
    conn.close()
    class NoClient:
        def extract_document(self,*a,**k):pytest.fail('changed source sent')
    assert city_bulk.queue(tmp_path,archive(),client=NoClient(),include_unlocated=True)['changed_source']==1


def test_unavailable_urls_are_counted_individually_without_deduplicating_missing_hashes(tmp_path):
    conn=city_agendas.connect(tmp_path)
    with conn:
        conn.execute("INSERT INTO report(url,state,error) VALUES ('one','failed','404'),('two','failed','404')")
    conn.close()
    class NoClient:
        def extract_document(self,*a,**k):pytest.fail('unavailable source sent')
    assert city_bulk.queue(tmp_path,archive(),client=NoClient())['unavailable_urls']==2


def test_paid_work_prioritizes_the_missing_population_and_skips_covered_primary_docs(tmp_path,monkeypatch):
    a=archive()
    a.execute("INSERT INTO solicitation VALUES('1234567890',2)")
    a.execute("INSERT INTO award VALUES('1234567890','odata')")
    a.execute("INSERT INTO bid VALUES('3987477359',1)")
    rows=[]
    for doc in ('3987477359','1234567890'):
        path=tmp_path/(doc+'.pdf');path.write_bytes(doc.encode())
        rows.append(dict(url='https://www.toronto.ca/'+doc+'.pdf',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            local_path=str(path),text='Doc'+doc+' Two proposals were received.',screen='eligible',items=[dict(title='Award of Doc'+doc)]))
    monkeypatch.setattr(city_bulk,'inventory',lambda *a:rows)
    class Client:
        calls=[]
        def extract_document(self,sha,*a,**k):
            self.calls.append(sha);raise ExtractionPending('queued')
    client=Client();stats=city_bulk.queue(tmp_path,a,client=client)
    assert stats['outside_missing_population']==1 and stats['pending']==1
    assert client.calls==[rows[0]['sha256']]


def test_backfill_is_atomic_preserves_ids_and_rejects_missing_prior_context(conn,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from toronto_bids.store import db
    from toronto_bids.models import Solicitation
    row=report();pdf=tmp_path/'held.pdf';pdf.write_bytes(b'%PDF-held')
    row['text']=row['text'].strip()
    row['sha256']=hashlib.sha256(pdf.read_bytes()).hexdigest();row['local_path']=str(pdf)
    row['items']=[]
    # Admission has its own source-role tests above; exercise storage at its
    # interface without invoking a paid model or depending on page typography.
    proof=dict(document_number='3987477359',names=['Alberici Constructors Ltd.'],
        report_url=row['url'],report_sha256=row['sha256'],contract_index=0,items=[dict(agenda_sha256='first')])
    monkeypatch.setattr(city_bulk,'inventory',lambda *a:[row])
    monkeypatch.setattr(city_bulk,'propose',lambda *a:([proof],[]))
    monkeypatch.setattr(city_bulk.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=row['text']))
    db.upsert_row(conn,Solicitation('3987477359',source='odata'),overwrite=True);conn.commit()
    assert city_bulk.backfill(tmp_path,conn)['documents']==1
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM bid WHERE source='city_bulk'").fetchone()[0]==0
    city_bulk.backfill(tmp_path,conn);conn.commit()
    before=tuple(conn.execute("SELECT id,first_seen,bid_price,status FROM bid WHERE source='city_bulk'").fetchone())
    city_bulk.backfill(tmp_path,conn);conn.commit()
    assert before==tuple(conn.execute("SELECT id,first_seen,bid_price,status FROM bid WHERE source='city_bulk'").fetchone())
    assert before[2:]==(None,None)
    proof['items'].append(dict(agenda_sha256='later Council consideration'))
    city_bulk.backfill(tmp_path,conn);conn.commit()
    assert conn.execute('SELECT count(*) FROM city_bulk_bid_evidence').fetchone()[0]==2
    assert before==tuple(conn.execute("SELECT id,first_seen,bid_price,status FROM bid WHERE source='city_bulk'").fetchone())
    monkeypatch.setattr(city_bulk,'propose',lambda *a:([],[dict(reason='source_changed')]))
    with pytest.raises(ValueError,match='Previously admitted'):city_bulk.backfill(tmp_path,conn)
    assert conn.execute("SELECT count(*) FROM bid WHERE source='city_bulk'").fetchone()[0]==1
    monkeypatch.setattr(city_bulk,'inventory',lambda *a:[])
    assert city_bulk.backfill(tmp_path,conn)['documents']==0
    assert conn.execute("SELECT count(*) FROM bid WHERE source='city_bulk'").fetchone()[0]==1


def test_run_keeps_held_work_processable_when_another_lane_is_busy_or_deferred(tmp_path,monkeypatch):
    monkeypatch.setattr(city_agendas,'seed_schedules',lambda *a,**k:None)
    def busy(*a,**k):raise city_agendas.InventoryBusy('already capturing agendas')
    def deferred(*a,**k):raise city_agendas.CaptureDeferred('server Retry-After')
    monkeypatch.setattr(city_agendas,'capture',busy)
    monkeypatch.setattr(city_agendas,'capture_reports',deferred)
    result=city_bulk.run(tmp_path,archive(),http=None,browser=None)
    assert result['agendas']['lane_state']=='busy'
    assert result['reports']['lane_state']=='deferred'
    assert (tmp_path/'extraction-inventory.json').is_file()
    assert json.loads((tmp_path/'run.json').read_text())==result


def test_backfill_uses_one_coherent_list_and_keeps_it_when_more_supplements_arrive(conn,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from toronto_bids.models import Solicitation
    from toronto_bids.store import db
    row=report();row['text']=row['text'].strip();row['items']=[]
    path=tmp_path/'source.pdf';path.write_bytes(b'%PDF-held')
    row.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),local_path=str(path))
    rows=[dict(row,url='https://www.toronto.ca/first.pdf',names=['Short Name']),
          dict(row,url='https://www.toronto.ca/second.pdf',names=['Full Legal Name','Other Bidder'])]
    monkeypatch.setattr(city_bulk,'inventory',lambda *a:rows)
    def proposals(_archive,r,*context):return [dict(document_number='3987477359',contract_index=0,names=r['names'],report_url=r['url'],report_sha256=r['sha256'],items=[])],[]
    monkeypatch.setattr(city_bulk,'propose',proposals)
    monkeypatch.setattr(city_bulk.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=row['text']))
    db.upsert_row(conn,Solicitation('3987477359',source='odata'),overwrite=True);conn.commit()
    result=city_bulk.backfill(tmp_path,conn);conn.commit()
    assert result['accepted_reports']==1
    assert {r[0] for r in conn.execute("SELECT bidder_name_raw FROM bid WHERE source='city_bulk'")}=={'Full Legal Name','Other Bidder'}
    rows.append(dict(row,url='https://www.toronto.ca/new-supplement.pdf',names=['Another Name','Another Bidder','Third Bidder']))
    result=city_bulk.backfill(tmp_path,conn);conn.commit()
    assert result['accepted_reports']==1 and len(result['exceptions'])==2
    assert {r[0] for r in conn.execute("SELECT bidder_name_raw FROM bid WHERE source='city_bulk'")}=={'Full Legal Name','Other Bidder'}


def native_report():
    row=report()
    row['text']=row['text'].replace('Doc3987477359','RFP 3905-14-0097')
    row['items'][0]['title']='Award of RFP 3905-14-0097'
    row['result']['contracts'][0]['reference']='RFP 3905-14-0097'
    return row


def test_native_bulk_uses_exact_archived_award_not_call_digits(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch)
    accepted,held=city_bulk.propose(conn,native_report())
    assert not held
    assert accepted[0]['document_number']=='9000000042'
    proof=accepted[0]['native_call_identity']
    assert proof['call_number']=='3905-14-0097'
    assert proof['archived_award']['printed_award_date']=='2014-04-10'
    assert proof['publisher_record_sha256']


@pytest.mark.parametrize('change', ['current_award','agenda_call','report_call','local_call','unmapped'])
def test_native_bulk_refuses_unproven_or_conflicting_source_roles(conn,tmp_path,monkeypatch,change):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch);row=native_report()
    if change=='current_award':conn.execute('UPDATE solicitation SET publisher_award_identity_json=NULL')
    elif change=='agenda_call':row['items'][0]['title']='Award of RFP 3905-14-0098'
    elif change=='report_call':row['text']=row['text'].replace('3905-14-0097','3905-14-0098')
    elif change=='local_call':row['text']=row['text'].replace('The RFP','SUMMARY\nEarlier call 3905-14-0098. The RFP')
    elif change=='unmapped':row['result']['contracts'][0]['reference']='RFP 3905-14-0098'
    assert not city_bulk.propose(conn,row)[0]


def tender_report():
    row=native_report();c=row['result']['contracts'][0]
    c.update(stage='prequalification_then_tender',solicitation_type='RFT',invited_to_tender=['Other Firm'])
    c['bids'].append(dict(supplier_name='Other Firm',status='no_bid',amount_raw=None))
    row['text']='Award of Tender 3905-14-0097\nSUMMARY\nTender 3905-14-0097 was issued. At its Public Opening the City opened the following two (2) Tender submissions:\nBidders\nBid Price including HST\nAlberici Constructors Ltd.\n$10\nMaple Reinders Limited\n$20\nOther Firm\nDid not bid\nCOMMENTS\n'
    return row


def test_native_tender_opening_is_distinct_from_invitation_and_excludes_no_bid(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch)
    accepted,held=city_bulk.propose(conn,tender_report())
    assert not held
    assert accepted[0]['names']==['Alberici Constructors Ltd.','Maple Reinders Limited']
    assert accepted[0]['witness']['excluded_no_bid'][0]['name']=='Other Firm'
    assert accepted[0]['prices_withheld'] and accepted[0]['statuses_withheld']


@pytest.mark.parametrize('change',['invited_only','missing_no_bid','model_no_bid_conflict','other_call','qualification_event'])
def test_native_tender_requires_actual_opened_rows_and_source_supported_no_bid(conn,tmp_path,monkeypatch,change):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch);row=tender_report()
    if change=='invited_only':row['text']=row['text'].replace('opened the following two (2) Tender submissions','invited the following contractors')
    elif change=='missing_no_bid':row['text']=row['text'].replace('Did not bid','$30')
    elif change=='model_no_bid_conflict':row['result']['contracts'][0]['bids'][0]['status']='no_bid'
    elif change=='other_call':row['text']=row['text'].replace('At its Public','Tender 3905-14-0098. At its Public')
    elif change=='qualification_event':row['result']['contracts'][0]['solicitation_type']='RFSQ'
    assert not city_bulk.propose(conn,row)[0]


@pytest.mark.parametrize('abbreviation',['ACL','Alberici'])
@pytest.mark.parametrize('phrase',['This resulted in receipt of four submissions from the following firms:', 'Responses were received from the following firms:'])
def test_native_receipt_wording_and_publisher_abbreviation_are_source_roles(conn,tmp_path,monkeypatch,phrase,abbreviation):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch);row=native_report()
    row['text']='Award of RFP 3905-14-0097\nSUMMARY\n'+phrase+'\nAlberici Constructors Ltd. ('+abbreviation+')\nMaple Reinders Limited\n'
    accepted,held=city_bulk.propose(conn,row)
    assert not held and accepted[0]['names']==['Alberici Constructors Ltd.','Maple Reinders Limited']
    row['text']=row['text'].replace('('+abbreviation+')','submitted No Bid')
    assert not city_bulk.propose(conn,row)[0]


def test_native_copied_footer_is_a_conflicting_event_even_outside_receipt_context(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch);row=native_report()
    row['text']+='\n'+('Unrelated background. '*100)+'\nAward of RFP 3905-14-0098\nPage 1 of 1\n'
    assert not city_bulk.propose(conn,row)[0]


def test_native_report_calendar_range_is_not_a_conflicting_footer_call(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch);row=native_report()
    row['text']=row['text'].replace('The RFP','SUMMARY\nThe RFP')
    row['text']+='\nFunding in the 2019-2027 Capital Budgets and Plans\nAward of RFP 3905-14-0097\nPage 1 of 1\n'
    assert city_bulk.propose(conn,row)[0]


def test_native_received_list_is_distinct_from_later_negotiation_invitation(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch);row=native_report()
    row['text']=row['text'].replace('The RFP','Proponents would later be invited to enter negotiations. The RFP')
    assert city_bulk.propose(conn,row)[0]
