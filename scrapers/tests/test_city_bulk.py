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


@pytest.mark.parametrize("change", [None, "report_hash", "odata_id", "receipt"])
def test_reviewed_receipt_requires_held_report_identity_and_literal_receipt(conn, monkeypatch, change):
    row = report()
    row["text"] = (
        "Award of Doc9999999999\fProcurement Process for Doc3987477359\n"
        "Two firms submitted: Alberici Constructors Ltd. and Maple Reinders Limited"
    )
    row["result"] = None
    review = dict(
        document_number="3987477359", odata_id="publisher-id", report_sha256=row["sha256"],
        identity_page=2, identity_quotation="Procurement Process for Doc3987477359",
        receipt_page=2, receipt_quotation="Two firms submitted: Alberici Constructors Ltd. and Maple Reinders Limited",
        names=["Alberici Constructors Ltd.", "Maple Reinders Limited"],
    )
    monkeypatch.setattr(city_bulk, "_RECEIVED_REVIEWS", {row["url"]: review})
    conn.execute("INSERT INTO solicitation (document_number, source, odata_id) VALUES ('3987477359', 'odata', ?)",
                 ("changed" if change == "odata_id" else "publisher-id",))
    if change == "report_hash":
        row["sha256"] = "b" * 64
    if change == "receipt":
        row["text"] = row["text"].replace("submitted", "were invited")
    accepted, held = city_bulk.propose(conn, row)
    if change:
        assert accepted == [] and len(held) == 1
    else:
        assert held == []
        assert accepted[0]["names"] == review["names"]
        assert accepted[0]["prices_withheld"] and accepted[0]["statuses_withheld"]


def test_reviewed_native_receipt_requires_unique_exact_publisher_match(conn, monkeypatch):
    from toronto_bids.models import Award, Solicitation
    from toronto_bids.store import db

    row = report()
    row["text"] = "Tender 5-2024. Issued in June, closed in July. Acme awarded $100.\nAcme submitted."
    review = dict(
        document_number="3987477359", odata_id="primary", report_sha256=row["sha256"],
        identity_page=1, identity_quotation="Tender 5-2024", receipt_page=1,
        receipt_quotation="Acme submitted.", names=["Acme"], native_call="5-2024",
        publisher_match=dict(dates_page=1, dates_quotation="Issued in June, closed in July.",
                             award_page=1, award_quotation="Acme awarded $100.",
                             issue_date="2024-06-01", close_date="2024-07-01", rfx_type="RFT",
                             supplier="Acme", amount="100"),
    )
    monkeypatch.setattr(city_bulk, "_RECEIVED_REVIEWS", {row["url"]: review})
    for doc, oid in (("3987477359", "primary"), ("9999999999", "competing")):
        db.upsert_row(conn, Solicitation(doc, odata_id=oid, source="odata", rfx_type="RFT",
                                       issue_date="2024-06-01", submission_deadline="2024-07-01"),
                      overwrite=True)
        db.upsert_row(conn, Award(doc, "Acme", award_amount="100", source="odata"), overwrite=True)
        accepted, held = city_bulk.propose(conn, row)
        if oid == "primary":
            assert accepted and not held
        else:
            assert not accepted and held[0]["reason"] == "reviewed_publisher_match_absent_or_ambiguous"


def test_reviewed_independent_identity_binds_the_archived_city_row(tmp_path):
    data = b"<table><tr><td>Doc3987477359</td><td>project RFP-1</td></tr></table>"
    sha = hashlib.sha256(data).hexdigest()
    source = tmp_path / f"{sha}.html"
    source.write_bytes(data)
    review = dict(document_number="3987477359",
                  independent_identity=dict(sha256=sha, contract="RFP-1", row=["Doc3987477359", "project RFP-1"]))
    row = {"local_path": str(tmp_path / "report.pdf")}
    city_bulk._reviewed_identity_source(row, review, "Bid results for RFP-1")
    with pytest.raises(ValueError, match="reviewed_independent_identity_not_literal"):
        city_bulk._reviewed_identity_source(row, review, "Bid results for RFP-2")
    source.write_bytes(data.replace(b"3987477359", b"9999999999"))
    with pytest.raises(ValueError, match="reviewed_independent_identity_changed"):
        city_bulk._reviewed_identity_source(row, review, "Bid results for RFP-1")


@pytest.mark.parametrize("alias", [None, "9876543210"], ids=["reference-only", "alternate-doc"])
def test_reviewed_receipt_links_held_bid_without_copying_it(conn, alias):
    from toronto_bids.bid_coverage import audit
    from toronto_bids.linking.city_crosswalk import canonical, digest
    from toronto_bids.linking.procurement import resolve_procurements
    from toronto_bids.models import Award, Bid, Solicitation
    from toronto_bids.store import db

    doc, ref = "3987477359", "2024.BA1.1"
    for row in (Solicitation(doc, source="odata", odata_id="publisher", publisher_award_identity_json="{}"),
                Award(doc, "Winner", source="odata"),
                Bid("Winner", reference=ref, document_number=alias, source="bid_award_panel")):
        db.upsert_row(conn, row, overwrite=True)
    resolve_procurements(conn)
    original_bid = tuple(conn.execute("SELECT id, document_number FROM bid").fetchone())
    proof = dict(admission_version="city-reviewed-receipt-v1", document_number=doc,
                 report_url="https://example.org/report.pdf", report_sha256="1" * 64, names=["Winner"],
                 receipt_review=dict(council_reference=ref, odata_id="publisher", reuse_reference_bids=alias is None))
    if alias is not None:
        proof.update(alias_document_number=alias, call_number="1-2024",
                     native_call_identity={"publisher_record_sha256": digest({})})

    city_bulk._reuse_reviewed_bids(conn, proof)
    conn.execute(
        "INSERT INTO city_bulk_bid_evidence (proof_sha256,document_number,report_url,report_sha256,proof_json) "
        "VALUES (?,?,?,?,?)", (digest(proof), doc, proof["report_url"], proof["report_sha256"], canonical(proof))
    )
    resolve_procurements(conn)
    result = audit(conn)
    assert result["missing"] == []
    assert result["summary"]["canonical_bid_attribution_mismatches"] == []
    assert [tuple(row) for row in conn.execute("SELECT id, document_number FROM bid")] == [original_bid]


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


def test_native_tender_cannot_borrow_an_omitted_partys_price(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch);row=tender_report()
    row['text']=row['text'].replace('Alberici Constructors Ltd.\n$10','Alberici Constructors Ltd.\nResponse pending\nOmitted Firm\n$10')
    assert not city_bulk.propose(conn,row)[0]


def test_native_tender_cannot_borrow_an_omitted_partys_no_bid(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch);row=tender_report()
    row['text']=row['text'].replace('Other Firm\nDid not bid','Other Firm\nResponse pending\nOmitted Firm\nDid not bid')
    assert not city_bulk.propose(conn,row)[0]


@pytest.mark.parametrize('receipt',['One (1) proposal was received','A total of 1 submission was received','This resulted in receipt of one (1) submission'])
def test_original_native_list_receipt_count_limits_names_without_model_count(conn,tmp_path,monkeypatch,receipt):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch);row=native_report()
    row['text']='Award of RFP 3905-14-0097\n'+receipt+' from the following firms:\nAlberici Constructors Ltd.\nMaple Reinders Limited\n'
    assert not city_bulk.propose(conn,row)[0]


def test_queue_targets_proven_native_calls_and_skips_covered_or_unproved(tmp_path,monkeypatch):
    a=archive()
    a.execute("INSERT INTO solicitation VALUES('1111111111',2)")
    a.execute("INSERT INTO award VALUES('1111111111','odata')")
    a.execute("INSERT INTO solicitation VALUES('2222222222',3)")
    a.execute("INSERT INTO award VALUES('2222222222','odata')")
    a.execute("INSERT INTO bid VALUES('2222222222',3)")

    class FakeNative:
        def resolve(self,call):
            if call=='235-2017':return '1111111111',{'call_number':call}
            if call=='48-2016':return '2222222222',{'call_number':call}
            raise ValueError('native_call_has_no_unique_original_award_identity')

    specs=[
        ('missing.pdf','Award of Tender Call 235-2017\nSUMMARY\nTwo bids were received.','Award of Tender Call 235-2017'),
        ('covered.pdf','Award of Tender Call 48-2016\nSUMMARY\nTwo bids were received.','Award of Tender Call 48-2016'),
        ('unproved.pdf','Award of Tender Call 97-2016\nSUMMARY\nTwo bids were received.','Award of Tender Call 97-2016'),
        ('footer.pdf','Award of Tender Call 235-2017\nSUMMARY\nTwo bids were received.\fOther Tender 99-2017\nPage 2 of 2','Award of Tender Call 235-2017'),
    ]
    rows=[]
    for name,text,title in specs:
        p=tmp_path/name;p.write_bytes(text.encode())
        rows.append(dict(url='https://www.toronto.ca/'+name,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
            local_path=str(p),text=text,screen='eligible',items=[dict(title=title)]))
    monkeypatch.setattr(city_bulk,'inventory',lambda *a:rows)

    class Client:
        calls=[]
        def extract_document(self,sha,*a,**k):
            self.calls.append(sha);raise ExtractionPending('queued')

    client=Client()
    stats=city_bulk.queue(tmp_path,a,client=client,native_identities=FakeNative())
    assert stats['pending']==1
    assert stats['outside_missing_population']==1
    assert stats['identity_discovery_deferred']==2
    assert client.calls==[rows[0]['sha256']]


def test_responded_to_rfp_as_follows_list_admits_clean_native_proponents(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch)
    row=native_report()
    row['text']=(
        'Award of RFP 3905-14-0097\nSUMMARY\n'
        'Evaluation of the Proposal Submissions\n'
        'A total of two (2) proponents responded to the RFP as follows:\n'
        '1.\n2.\n\nAlberici Constructors Ltd.\nMaple Reinders Limited\n'
        'The evaluation process consisted of four distinct steps.\n'
    )
    accepted,held=city_bulk.propose(conn,row)
    assert not held and accepted[0]['names']==['Alberici Constructors Ltd.','Maple Reinders Limited']
    assert accepted[0]['witness']['kind']=='submitted_firm_list'
    # Count bound still rejects when observed names exceed stated count.
    row['text']=row['text'].replace('two (2)','one (1)')
    assert not city_bulk.propose(conn,row)[0]


def test_native_tender_opened_following_tenders_admits_priced_rows_and_excludes_no_bid(conn,tmp_path,monkeypatch):
    from tests.test_city_archived_awards import setup_archive
    setup_archive(conn,tmp_path,monkeypatch)
    row=tender_report()
    row['text']=row['text'].replace('opened the following Tender submissions:','opened the following Tenders:')
    accepted,held=city_bulk.propose(conn,row)
    assert not held and accepted[0]['names']==['Alberici Constructors Ltd.','Maple Reinders Limited']
    assert accepted[0]['witness']['kind']=='native_tender_received_table'
    assert [e['name'] for e in accepted[0]['witness']['excluded_no_bid']]==['Other Firm']


def test_modern_doc_prequalification_then_tender_rft_uses_tender_witness():
    row=report()
    row['result']['contracts'][0].update(solicitation_type='RFT',stage='prequalification_then_tender',
        prequalified=['Other Prequalified Co.'],invited_to_tender=['Alberici Constructors Ltd.','Maple Reinders Limited'])
    row['text']=(
        'Award of Doc3987477359\nSUMMARY\n'
        'Pre-qualified firms:\n1. Other Prequalified Co.\n2. Alberici Constructors Ltd.\n3. Maple Reinders Limited\n'
        'Request for Tender Number Doc3987477359 closed.\n'
        'At the time of closing, PMMD received and opened the following bids:\n'
        'Table 2: Summary of Bids received for Request for Tender Number Doc3987477359:\n'
        'Supplier Name\nBid Price (excluding HST)\n'
        'Alberici Constructors Ltd.\n$10,000,000\n'
        'Maple Reinders Limited\n$20,000,000\n'
    )
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and accepted[0]['names']==['Alberici Constructors Ltd.','Maple Reinders Limited']
    assert accepted[0]['witness']['kind']=='modern_tender_received_table'
    # Competing Doc in the local tender context still refuses.
    row['text']=row['text'].replace('Doc3987477359:','Doc3987477359 / Doc9999999999:')
    assert not city_bulk.propose(archive(),row)[0]


@pytest.mark.parametrize('lead,heading', [
    ('A total of two (2) suppliers submitted their fixed priced bid as listed in Table 2 below.',
     'Table 2: Suppliers and Bid Prices for Request for Tender, Ariba Document\nNumber 3987477359'),
    ('A total of two (2) bids were submitted electronically in Ariba, from the Suppliers listed in Table 2 below.',
     'Table 2. Suppliers and Bid Prices for Request for Tender, Ariba Document Number\n3987477359'),
])
def test_wrapped_suppliers_and_bid_prices_table_with_page_break(lead,heading):
    row=report()
    row['text']=(
        'Award of Doc3987477359\nSUMMARY\n' + lead +
        '\nAward of Doc3987477359\nPage 3 of 5\n\f' + heading +
        '\nSupplier Name\nAlberici Constructors Ltd.\n$10\nMaple Reinders Limited\n$20\n'
    )
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and accepted[0]['names']==['Alberici Constructors Ltd.','Maple Reinders Limited']


@pytest.mark.parametrize('heading', [
    'Table 2: Summary of one (1) Bid Received for Request for Quotation Doc3987477359',
    'Table 5: List of the Suppliers Who Submitted a Proposal',
])
def test_singular_bid_received_and_submitted_suppliers_list_tables(heading):
    row=report()
    row['result']['contracts'][0]['bids']=[row['result']['contracts'][0]['bids'][0]]
    row['text']=(
        'Award of Doc3987477359\nSUMMARY\nOne proposal was submitted.\n'
        + heading + '\nSupplier Name\n1. Alberici Constructors Ltd.\n$10\n'
    )
    accepted,held=city_bulk.propose(archive(),row)
    assert not held and accepted[0]['names']==['Alberici Constructors Ltd.']


def test_missing_awarded_documents_matches_legacy_semantics_without_correlated_bid_scan(conn):
    # Seed edge cases: doc-number match, procurement_id-only match, NULL procurement_id, unlinked bids, CKAN-only award.
    for pid in (1, 2, 3):
        conn.execute("INSERT INTO procurement (id) VALUES (?)", (pid,))
    sols = [
        ("1000000001", 1),     # excluded by bid.document_number
        ("1000000002", 2),     # excluded by bid.procurement_id only (bid.document_number is NULL)
        ("1000000003", 3),     # missing (awarded, no matching bid)
        ("1000000004", None),  # missing (awarded, NULL procurement_id; unlinked bid also has NULL procurement_id)
        ("1000000005", None),  # excluded by bid.document_number even with NULL procurement_id
        ("1000000006", None),  # CKAN-only award -> not OData-awarded
    ]
    conn.executemany(
        "INSERT INTO solicitation (document_number, procurement_id, source) VALUES (?, ?, 'odata')", sols
    )
    for doc in ("1000000001", "1000000002", "1000000003", "1000000004", "1000000005"):
        conn.execute(
            "INSERT INTO award (document_number, supplier_name_raw, source) VALUES (?, 'Winner', 'odata')",
            (doc,),
        )
    conn.execute(
        "INSERT INTO award (document_number, supplier_name_raw, source) VALUES ('1000000006', 'Winner', 'ckan_awarded')"
    )
    conn.executemany(
        "INSERT INTO bid (document_number, procurement_id, bidder_name_raw, source) VALUES (?, ?, ?, 'bap')",
        [
            ("1000000001", None, "Bidder-1"),
            (None, 2, "Bidder-2"),
            (None, None, "Bidder-unlinked"),  # unlinked bid must not exclude NULL-procurement solicitations
            ("1000000005", None, "Bidder-5"),
        ],
    )
    conn.commit()

    legacy = {
        r[0]
        for r in conn.execute(
            "SELECT s.document_number FROM solicitation s WHERE "
            "EXISTS(SELECT 1 FROM award a WHERE a.document_number=s.document_number AND a.source='odata') AND "
            "NOT EXISTS(SELECT 1 FROM bid b WHERE b.document_number=s.document_number OR b.procurement_id=s.procurement_id)"
        )
    }
    actual = city_bulk._missing_awarded_documents(conn)
    assert actual == legacy == {"1000000003", "1000000004"}

    # Scale + query-plan check: 6,000 awarded solicitations & 12,000 bids with single-pass bid read.
    conn.executemany(
        "INSERT INTO solicitation (document_number, source) VALUES (?, 'odata')",
        [(f"{2000000000 + i}",) for i in range(6000)],
    )
    conn.executemany(
        "INSERT INTO award (document_number, supplier_name_raw, source) VALUES (?, 'W', 'odata')",
        [(f"{2000000000 + i}",) for i in range(6000)],
    )
    conn.executemany(
        "INSERT INTO bid (document_number, bidder_name_raw, source) VALUES (?, ?, 'bap')",
        [(f"{2000000000 + (i // 2)}", f"B-{i % 2}") for i in range(12000)],
    )
    conn.commit()

    sql_log = []
    conn.set_trace_callback(sql_log.append)
    try:
        missing = city_bulk._missing_awarded_documents(conn)
    finally:
        conn.set_trace_callback(None)

    assert missing == {"1000000003", "1000000004"}
    assert len(sql_log) == 2
    assert not any("NOT EXISTS" in q for q in sql_log)
    for q in sql_log:
        plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + q))
        assert "CORRELATED" not in plan.upper()


def test_joint_two_doc_award_report_admits_each_doc_from_its_own_table_only():
    a = archive()
    a.execute("INSERT INTO solicitation VALUES('2155284733',2),('2155392775',3)")
    row = dict(
        url='https://www.toronto.ca/legdocs/mmis/2020/ie/bgrd/backgroundfile-146571.pdf',
        sha256='a' * 64,
        text=(
            'Contract Awards for Tender Ariba Doc #2155284733 and Tender Ariba Doc #2155392775\n'
            'SUMMARY\n'
            'Table 3: Bidders and Bid Prices for Tender Ariba Doc #2155284733, Contract No. 20ECS-TI-13LR\n'
            'Bidders\nPrice\n'
            'D. Crupi & Sons Limited\n$10\nSanscon Construction Ltd.\n$20\n'
            'The submissions received from the two (2) bidders that responded to Tender Ariba Doc\n'
            '#2155284733, Contract No. 20ECS-TI-13LR, were reviewed.\n'
            'Contract Awards for Tender Ariba Doc #2155284733 and Doc #2155392775\nPage 4 of 6\n\f'
            'Table 4: Bidders and Bid Prices for Tender Ariba Doc #2155392775, Contract No. 20ECS-TI-14LR\n'
            'Bidders\nPrice\n'
            'A & F Di Carlo Construction Inc.\n$30\nCoco Paving Inc.\n$40\n'
            'The submissions received from the two (2) bidders that responded to Tender Ariba Doc\n'
            '#2155392775, Contract No. 20ECS-TI-14LR, were reviewed.\n'
            'Contract Awards for Tender Ariba Doc #2155284733 and Doc #2155392775\nPage 5 of 6\n'
        ),
        items=[dict(
            reference='2020.IE12.2', meeting='2020.IE12',
            title='Contract Awards for Tender Ariba Doc #2155284733 and Tender Ariba Doc #2155392775',
            source_sha256='b' * 64,
        )],
        result=dict(contracts=[
            dict(reference='Tender Ariba Doc #2155284733', stage='single_stage', prequalified=[], invited_to_tender=[],
                 bids=[dict(supplier_name='D. Crupi & Sons Limited'), dict(supplier_name='Sanscon Construction Ltd.')]),
            dict(reference='Tender Ariba Doc #2155392775', stage='single_stage', prequalified=[], invited_to_tender=[],
                 bids=[dict(supplier_name='A & F Di Carlo Construction Inc.'), dict(supplier_name='Coco Paving Inc.')]),
        ]),
    )
    accepted, held = city_bulk.propose(a, row)
    assert not held and len(accepted) == 2
    assert {p['document_number']: p['names'] for p in accepted} == {
        '2155284733': ['D. Crupi & Sons Limited', 'Sanscon Construction Ltd.'],
        '2155392775': ['A & F Di Carlo Construction Inc.', 'Coco Paving Inc.'],
    }
    assert all(p['identity_role'] == 'joint_primary_subject_and_local_table' for p in accepted)
    # Cross-attributed bidder or unannounced copied footer must still refuse.
    bad = json.loads(json.dumps(row))
    bad['result']['contracts'][0]['bids'].append(dict(supplier_name='Coco Paving Inc.'))
    assert len(city_bulk.propose(a, bad)[0]) == 1
    bad_footer = json.loads(json.dumps(row))
    bad_footer['text'] += '\fAward of Doc9999999999\nPage 6 of 6\n'
    assert not city_bulk.propose(a, bad_footer)[0]


def test_compound_bare_doc_post_table_receipt_and_body_line_above_clean_footer():
    row = report()
    row['result']['contracts'][0]['reference'] = '3987477359 / 22ECS-MI-03AB'
    row['text'] = (
        'Award of Doc3987477359\nSUMMARY\n'
        'Related prior contract Doc1111111111 was completed.\n'
        'Award of Doc3987477359\nPage 1 of 3\n\f'
        'Table 2: Suppliers and Bid Prices for Request for Tender, Ariba Document\n'
        'Number 3987477359, Contract Number 22ECS-MI-03AB\n'
        'Suppliers\nBid Price\n'
        'Alberici Constructors Ltd.\n$10\nMaple Reinders Limited\n$20\n'
        'The submissions received from the two (2) Suppliers that responded to Ariba Tender\n'
        'Document Number 3987477359, Contract Number 22ECS-MI-03AB, were reviewed.\n'
        'Award of Doc3987477359\nPage 2 of 3\n'
    )
    accepted, held = city_bulk.propose(archive(), row)
    assert not held and accepted[0]['document_number'] == '3987477359'
    assert accepted[0]['names'] == ['Alberici Constructors Ltd.', 'Maple Reinders Limited']
    # Wrong post-table count still refuses.
    row['text'] = row['text'].replace('two (2)', 'one (1)')
    assert not city_bulk.propose(archive(), row)[0]


@pytest.mark.parametrize('variant', ['as_follows', 'inline_prose', 'responses_after_negotiation_note', 'doc_headed_table_after_history_call'])
def test_strict_fallback_receipt_witnesses_and_count_guards(variant):
    row = report()
    if variant == 'as_follows':
        row['text'] = (
            'Award of Doc3987477359\nSUMMARY\n'
            'A total of two (2) bids were received by PMMD\nand submitted for review, as follows:\n'
            '1. Alberici Constructors Ltd.\n2. Maple Reinders Limited\n'
        )
    elif variant == 'inline_prose':
        row['text'] = (
            'Award of Doc3987477359\nSUMMARY\n'
            'Two (2) Suppliers submitted a proposal: Alberici Constructors Ltd. and Maple Reinders Limited.\n'
        )
    elif variant == 'responses_after_negotiation_note':
        row['result']['contracts'][0]['reference'] = '3987477359 - Service Area A'
        row['text'] = (
            'Award of Doc3987477359\nSUMMARY\n'
            'The highest scoring proponent would be invited to enter into direct contract negotiations.\n'
            'At the time of closing, responses were received from the following two (2) firms:\n'
            'Alberici Constructors Ltd.\nMaple Reinders Limited\n'
        )
    elif variant == 'doc_headed_table_after_history_call':
        row['text'] = (
            'Award of Doc3987477359\nSUMMARY\n'
            'Prior contract RFQ 6114-17-0259 expired. ' + ('Background text. ' * 25) + '\n'
            'Table 2: Summary of two (2) Bids Received for Request for Quotation Ariba Document\n'
            'Number 3987477359:\nSupplier Name\nAlberici Constructors Ltd.\n$10\nMaple Reinders Limited\n$20\n'
        )
    accepted, held = city_bulk.propose(archive(), row)
    assert not held and accepted[0]['names'] == ['Alberici Constructors Ltd.', 'Maple Reinders Limited']
    # Count bound refuses when declared receipt count is smaller than observed names.
    row['text'] = row['text'].replace('Two (2)', 'One (1)').replace('two (2)', 'one (1)')
    assert not city_bulk.propose(archive(), row)[0]
