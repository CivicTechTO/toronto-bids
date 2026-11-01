import gzip
import hashlib
import json
from pathlib import Path

import pytest

from toronto_bids.linking import city_crosswalk as cw
from toronto_bids.linking.procurement import resolve_procurements
from toronto_bids.models import Award,Bid,CouncilItem,Solicitation
from toronto_bids.store import db

TITLE='Award of Request for Quotation No. 3905-14-0097 to Alpha Construction Ltd. for Works'
REF='2014.BD1.1'
# Deliberately unrelated to the printed Call Number: independent facts approve
# this fixture; stripping/padding the call could never discover this identifier.
DOC='1234567890'
HTML=f'''<html><h2>BD1.1 - {TITLE}</h2>
<h3>Recommendations</h3><p>Call Number: Request for Quotation 3905-14-0097</p>
<p>Description: Works</p><p>Recommended Bidder: Alpha Construction Ltd.</p>
<p>Contract Award Value: $100,000.55 net of all applicable taxes and charges</p>
<h3>Summary</h3><p>Call Issued: March 1, 2014 Call Closed: March 20, 2014</p>
<h3>Financial Impact</h3><p>Budget</p></html>'''.encode()


def record(**changes):
    row=dict(id='original-row',Solicitation_Document_Number=DOC,Issue_Date='2014-03-01',Closing_Date='2014-03-20',
             Solicitation_Document_Type='RFQ',Posting_Title='Doc-'+DOC,Solicitation_Award_Details='',
             Awarded_Suppliers=[dict(Successful_Bidder='Alpha Construction Limited',Award_Amount='100000.55')])
    row.update(changes)
    return row


def proposal(row=None,source=HTML,extra=()):
    row=row or record()
    return cw.propose(reference=REF,title=TITLE,source=source,publisher=row,index=cw.publisher_index([row,*extra]),
                      source_sha256=hashlib.sha256(source).hexdigest(),snapshot_sha256='snapshot')


def setup(conn,tmp_path,monkeypatch,*,direct=False):
    row=record(Solicitation_Award_Details='Call number: 3905-14-0097' if direct else '')
    proof=proposal(row)
    population=[row]
    bundle=dict(snapshot_sha256='snapshot',publisher_population_file='population.gz',
                publisher_population_sha256=cw.digest(population),publisher_population_count=1,
                publisher_records=[row],proofs=[proof])
    registry=tmp_path/'proofs.json';registry.write_text(json.dumps(bundle))
    (tmp_path/'population.gz').write_bytes(gzip.compress(cw.canonical(population).encode()))
    (tmp_path/'2014.BD1.html').write_bytes(HTML)
    monkeypatch.setattr(cw,'PROOFS',registry)
    monkeypatch.setattr('toronto_bids.config.COUNCIL_AGENDAS_DIR',tmp_path)
    def put(row):db.upsert_row(conn,row,overwrite=True)
    snapshot=dict(row);snapshot.pop('Posting_Title')
    put(Solicitation(DOC,publisher_identity_json=cw.canonical(snapshot),issue_date='2014-03-01',submission_deadline='2014-03-20',rfx_type='RFQ',odata_id='original-row'))
    put(Award(DOC,supplier_name_raw='Alpha Construction Limited',award_amount='100000.55',source='odata'))
    put(CouncilItem(REF,title=TITLE))
    put(Bid('Losing bidder',reference=REF,source='original'))
    conn.commit()
    return proof,registry


def test_independent_compound_facts_do_not_require_encoding():
    p=proposal()
    assert p['proof_class']=='corroborated_compound'
    assert p['document_number']==DOC and p['call_number']=='3905-14-0097'
    assert p['witnesses']['net_award'].endswith('net of all applicable taxes and charges')


def test_full_supplier_identity_preserves_jv_spelling_and_geography():
    assert cw.supplier_identity('The Alpha & Sons Ltd.')==cw.supplier_identity('Alpha and Sons Limited')
    assert cw.supplier_identity('Grascan / Torbridge')!=cw.supplier_identity('Grayscan')
    assert cw.supplier_identity('Lima’s Garden')!=cw.supplier_identity('Limas Gardens')
    assert cw.supplier_identity('Alpha Canada')!=cw.supplier_identity('Alpha Ontario')


@pytest.mark.parametrize('changes',[
    {'Issue_Date':'2014-03-02'}, {'Closing_Date':'2014-03-21'}, {'Closing_Date':'2013-01-01'},
    {'Solicitation_Document_Type':'RFT'},
    {'Awarded_Suppliers':[dict(Successful_Bidder='Beta Construction',Award_Amount='100000.55')]},
    {'Awarded_Suppliers':[dict(Successful_Bidder='Alpha Construction Limited',Award_Amount='100000.56')]},
])
def test_each_independent_contradiction_refuses(changes):
    with pytest.raises(ValueError):proposal(record(**changes))


def test_global_ambiguity_is_not_resolved_by_candidate_preference():
    other=record(id='another-record',Solicitation_Document_Number='9999999999')
    with pytest.raises(ValueError,match='global publisher'):proposal(extra=[other])


def test_later_option_value_cannot_replace_initial_award_scope():
    source=HTML.replace(b'$100,000.55 net',b'$90,000.00 net')+b'<p>Option Year 1: $100,000.55 net of all applicable taxes</p>'
    with pytest.raises(ValueError):proposal(source=source)


def test_native_call_and_related_prequalification_call_are_separate_roles():
    assert proposal(record(Solicitation_Award_Details='Call number: 3905-14-0097'))['proof_class']=='publisher_native_call'
    # Description mention is not a publisher identity assertion.
    assert proposal(record(Solicitation_Document_Description='Prequalification call: 3905-14-0097'))['proof_class']=='corroborated_compound'
    with pytest.raises(ValueError,match='contradicts'):proposal(record(Solicitation_Award_Details='Call number: 3905-14-0098'))
    with pytest.raises(ValueError,match='issued/closed'):proposal(record(Solicitation_Award_Details='Call number: 3905-14-0097',Closing_Date='2014-03-21'))


def test_ledger_and_canonical_merge_preserve_source_facts_and_history(conn,tmp_path,monkeypatch):
    # Establish independent IDs before admitting the late reviewed equivalence.
    proof,registry=setup(conn,tmp_path,monkeypatch)
    bundle=json.loads(registry.read_text());registry.write_text(json.dumps(dict(bundle,proofs=[])))
    resolve_procurements(conn)
    before=tuple(conn.execute('select id,reference,document_number,bidder_name_raw,bid_price,source,first_seen from bid').fetchone())
    city_id=conn.execute('select procurement_id from solicitation').fetchone()[0]
    old_bid_id=conn.execute('select procurement_id from bid').fetchone()[0]
    registry.write_text(json.dumps(bundle))
    resolve_procurements(conn)
    assert conn.execute('select procurement_id from bid').fetchone()[0]==city_id
    assert tuple(conn.execute('select id,reference,document_number,bidder_name_raw,bid_price,source,first_seen from bid').fetchone())==before
    alias=conn.execute('select * from procurement where id=?',(old_bid_id,)).fetchone()
    assert alias['merged_into']==city_id
    assert json.loads(alias['merge_evidence'])['proof_sha256']==cw.digest(proof)
    first=tuple(conn.execute('select * from city_crosswalk_evidence').fetchone())
    resolve_procurements(conn)
    assert tuple(conn.execute('select * from city_crosswalk_evidence').fetchone())==first
    # Released archives carry accepted evidence even when raw agenda files are absent.
    (tmp_path/'2014.BD1.html').unlink()
    resolve_procurements(conn)
    assert conn.execute('select count(*) from city_crosswalk_evidence').fetchone()[0]==1


@pytest.mark.parametrize('change',['title','date','source','competitor','link','population'])
def test_changed_review_context_fails_before_writing_links(conn,tmp_path,monkeypatch,change):
    setup(conn,tmp_path,monkeypatch)
    if change=='title':conn.execute("update council_item set title='Changed'")
    if change=='date':conn.execute("update solicitation set issue_date='2014-03-02'")
    if change=='source':(tmp_path/'2014.BD1.html').write_bytes(HTML+b'changed')
    if change=='competitor':
        db.upsert_row(conn,Solicitation('9999999999',issue_date='2014-03-01',submission_deadline='2014-03-20',rfx_type='RFQ'),overwrite=True)
        db.upsert_row(conn,Award('9999999999',supplier_name_raw='Alpha Construction Ltd.',award_amount='100000.55',source='odata'),overwrite=True)
    if change=='link':conn.execute("insert into solicitation_link values (?,?,'other')",(REF,'9999999999'))
    if change=='population':(tmp_path/'population.gz').write_bytes(gzip.compress(b'[]'))
    conn.commit()
    prior=[tuple(r) for r in conn.execute('select * from solicitation_link')]
    with pytest.raises(ValueError):resolve_procurements(conn)
    assert [tuple(r) for r in conn.execute('select * from solicitation_link')]==prior
    assert conn.execute('select count(*) from city_crosswalk_evidence').fetchone()[0]==0


def test_legacy_matcher_keeps_reviewed_crosswalk(conn,tmp_path,monkeypatch):
    from toronto_bids.sources.bid_award_panel import match_pre_ariba_solicitations
    setup(conn,tmp_path,monkeypatch);resolve_procurements(conn)
    match_pre_ariba_solicitations(conn,{})
    assert tuple(conn.execute('select document_number,method from solicitation_link').fetchone())==(DOC,'city_call_crosswalk')


def test_published_evidence_cannot_be_silently_changed_or_unregistered(conn,tmp_path,monkeypatch):
    proof,registry=setup(conn,tmp_path,monkeypatch)
    resolve_procurements(conn);conn.commit()
    conn.execute("update city_crosswalk_evidence set call_number='wrong'")
    with pytest.raises(ValueError,match='not intact'):resolve_procurements(conn)
    conn.rollback()
    data=json.loads(registry.read_text());data['proofs']=[];registry.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='absent from registry'):resolve_procurements(conn)
    assert conn.execute('select count(*) from solicitation_link').fetchone()[0]==1


def test_option_price_cannot_supply_an_unpublished_initial_net_value():
    source=HTML.replace(b'$100,000.55 net of all applicable taxes and charges',
                        b'$90,000.00 including HST. Option Year 1: $100,000.55 net of all applicable taxes and charges')
    with pytest.raises(ValueError,match='scope'):proposal(source=source)


SOURCE_REVIEW=json.loads((Path(__file__).parent/'fixtures/city_crosswalk_source_review.json').read_text())['cases']


@pytest.mark.parametrize('case',SOURCE_REVIEW,ids=lambda c:c['reference']+'-'+c['document_number'])
def test_independent_primary_source_review(case):
    from html import escape
    registry=json.loads((Path(cw.__file__).resolve().parents[1]/'data/city_call_crosswalk.json').read_text())
    population=json.loads(gzip.decompress((Path(cw.__file__).resolve().parents[1]/'data'/registry['publisher_population_file']).read_bytes()))
    # Re-encode the retained original item excerpt as a standalone HTML fragment.
    # The expected relationship comes from the reviewed primary sources, not the
    # old supplier/value matcher or a copied enriched solicitation title.
    token=case['reference'].split('.',1)[1]
    source=('<div>'+escape(token+' - '+case['source_text'])+'</div>').encode()
    kwargs=dict(reference=case['reference'],title=case['source_title'],source=source,
                publisher=case['publisher_record'],index=cw.publisher_index(population),
                source_sha256=hashlib.sha256(source).hexdigest(),snapshot_sha256=registry['snapshot_sha256'])
    if case['expected_proof_class'] is None:
        with pytest.raises(ValueError):cw.propose(**kwargs)
    else:
        p=cw.propose(**kwargs)
        assert p['proof_class']==case['expected_proof_class']
        assert p['document_number']==case['document_number']


def test_numeric_discovery_is_read_only_and_separate_from_acceptance(conn,tmp_path,monkeypatch):
    from toronto_bids.city_crosswalk import discover
    setup(conn,tmp_path,monkeypatch)
    db.upsert_row(conn,Solicitation('3905140097',issue_date='2014-03-01'),overwrite=True)
    db.upsert_row(conn,Award('3905140097',supplier_name_raw='Other',source='odata'),overwrite=True)
    conn.commit()
    rows=discover(conn)
    assert rows==[dict(document_number='3905140097',encoded_call_refs=[REF],bids_on_candidate_refs=1)]
    assert conn.execute('select count(*) from solicitation_link').fetchone()[0]==0
    assert conn.execute('select count(*) from city_crosswalk_evidence').fetchone()[0]==0


def test_latest_publisher_change_is_not_hidden_by_archived_award(conn,tmp_path,monkeypatch):
    setup(conn,tmp_path,monkeypatch)
    if 'publisher_identity_json' not in {r[1] for r in conn.execute('pragma table_info(solicitation)')}:
        conn.execute('alter table solicitation add column publisher_identity_json TEXT')
    snapshot=record();snapshot.pop('Posting_Title')
    conn.execute('update solicitation set publisher_identity_json=?',(cw.canonical(snapshot),))
    resolve_procurements(conn);conn.commit()
    snapshot['Awarded_Suppliers'][0]['Award_Amount']='200000.55'
    conn.execute('update solicitation set publisher_identity_json=?',(cw.canonical(snapshot),))
    db.upsert_row(conn,Award(DOC,supplier_name_raw='Alpha Construction Limited',award_amount='200000.55',source='odata'),overwrite=True)
    conn.commit()
    # The old award line is correctly retained by archive semantics. It must not
    # conceal that the publisher's current identity evidence changed.
    assert conn.execute('select count(*) from award').fetchone()[0]==2
    with pytest.raises(ValueError,match='publisher identity snapshot changed'):
        resolve_procurements(conn)


def test_second_labelled_call_cannot_hide_behind_the_first_match():
    source=HTML.replace(b'<h3>Financial Impact</h3>',b'<p>Call Number: RFQ 3905-14-0098</p><p>Description: Another procurement</p><h3>Financial Impact</h3>')
    with pytest.raises(ValueError,match='not unambiguous'):proposal(source=source)
    with pytest.raises(ValueError,match='multiple publisher'):
        proposal(record(Solicitation_Award_Details='Call number: 3905-14-0097\nCall number: 3905-14-0098'))
