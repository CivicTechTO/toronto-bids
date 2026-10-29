"""Reviewed TTC identities cannot merge reports or escape their source context."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking import agency_identity
from toronto_bids.store import db


@pytest.fixture
def evidence(monkeypatch,tmp_path):
    contract = dict(reference='9-118-55',title='Current supply',
        bids=[dict(supplier_name='Bidder',amount_raw='$10',amount_basis='unknown')],
        awards=[dict(supplier_name='Bidder',amount_raw='$20')])
    records=[]
    for label in ['A','B']:
        url=f'https://cdn.ttc.ca/Report-{label}.pdf'
        sha=hashlib.sha256(label.encode()).hexdigest()
        records.append(dict(corpus='ttc',identity_kind='report_url_fallback',
            document_sha256=sha,extractor_version=EXTRACTOR_VERSION,
            contract_sha256=agency_identity.contract_fingerprint(contract),
            native_ref=url,report_url=url,contract_count=1,
            identifiers=[dict(role='administrative',value='9-118-55',authority='toronto-transit-commission')],
            basis='One procurement collection; footer is not a published call identifier.',
            sources=[dict(url=url,sha256=sha,passages=['Current supply; Bidder $10; footer 9-118-55'])]))
    path=tmp_path/'agency_identity_evidence.json'
    def save():
        path.write_text(json.dumps(dict(version=1,decisions=records)))
        agency_identity._evidence.cache_clear()
    save()
    monkeypatch.setattr(agency_identity.resources,'files',lambda _:tmp_path)
    yield records,contract,save
    agency_identity._evidence.cache_clear()


def hold(conn,proof,contracts):
    seed_buyers(conn)
    conn.execute('INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)',
        (proof['report_url'],'ttc_board',proof['document_sha256'],'held source text'))
    db.mark_extracted(conn,proof['document_sha256'],EXTRACTOR_VERSION,json.dumps(dict(contracts=contracts)))
    conn.commit()


def test_same_administrative_footer_keeps_two_procurements_and_awards_withheld(conn,evidence):
    records,contract,_=evidence
    for proof in records:
        hold(conn,proof,[contract])
    backfill_from_extraction(conn,'ttc')
    assert {r[0] for r in conn.execute('SELECT native_ref FROM agency_bid')} == {r['report_url'] for r in records}
    assert conn.execute('SELECT count(*) FROM agency_bid').fetchone()[0]==2
    assert not conn.execute('SELECT * FROM agency_award').fetchall()
    proofs=[json.loads(r[0]) for r in conn.execute('SELECT identity_evidence_json FROM agency_derivation WHERE active=1')]
    assert all(p['identity_kind']=='report_url_fallback' for p in proofs)
    assert all(not p.get('legacy_native_ref') for p in proofs)
    before=[tuple(r) for r in conn.execute('SELECT id,first_seen,native_ref FROM agency_bid ORDER BY id')]
    backfill_from_extraction(conn,'ttc')
    assert [tuple(r) for r in conn.execute('SELECT id,first_seen,native_ref FROM agency_bid ORDER BY id')]==before


@pytest.mark.parametrize('change',['contract','version','document','url','count'])
def test_changed_review_context_refuses_raw_footer_identity(conn,evidence,monkeypatch,change):
    records,original,_=evidence
    proof=copy.deepcopy(records[0]);contract=copy.deepcopy(original)
    if change=='contract': contract['bids'][0]['amount_raw']='$999'
    if change=='version': monkeypatch.setattr(agency_identity,'EXTRACTOR_VERSION','unreviewed-version')
    if change=='document': proof['document_sha256']='e'*64
    if change=='url': proof['report_url']='https://cdn.ttc.ca/Unreviewed-alias.pdf'
    hold(conn,proof,[contract] if change!='count' else [contract,copy.deepcopy(contract)])
    with pytest.raises(RuntimeError,match='re-review required.*existing rows kept'):
        backfill_from_extraction(conn,'ttc')
    assert not conn.execute('SELECT * FROM agency_bid').fetchall()


def test_changed_payload_cannot_sweep_previously_verified_facts(conn,evidence):
    records,original,_=evidence
    for proof in records:hold(conn,proof,[original])
    backfill_from_extraction(conn,'ttc')
    before=[tuple(r) for r in conn.execute('SELECT id,native_ref,bid_price,first_seen FROM agency_bid ORDER BY id')]
    changed=copy.deepcopy(original);changed['bids'][0]['amount_raw']='$999'
    db.mark_extracted(conn,records[0]['document_sha256'],EXTRACTOR_VERSION,
        json.dumps(dict(contracts=[changed])))
    with pytest.raises(RuntimeError,match='existing rows kept'):
        backfill_from_extraction(conn,'ttc')
    assert [tuple(r) for r in conn.execute('SELECT id,native_ref,bid_price,first_seen FROM agency_bid ORDER BY id')]==before


def test_reviewed_printed_contract_keeps_role_without_inventing_alias(conn,evidence):
    records,contract,save=evidence
    proof=records[0];proof['identity_kind']='contract';proof['native_ref']='D5-18'
    proof['identifiers'].append(dict(role='contract',value='D5-18',authority='toronto-transit-commission'))
    proof['sources'][0]['passages'].append('Current subject Contract D5-18; 9-118-55 is a footer.')
    save();hold(conn,proof,[contract]);backfill_from_extraction(conn,'ttc')
    assert conn.execute('SELECT native_ref FROM agency_bid').fetchone()[0]=='D5-18'
    assert not conn.execute('SELECT * FROM agency_award').fetchall()


def test_reviewed_ttc_decision_cannot_apply_to_another_buyer(evidence):
    records,contract,_=evidence
    assert agency_identity.reviewed_identity('zoo',records[0]['document_sha256'],contract,
        report_url=records[0]['report_url'],contract_count=1) is None


def test_all_source_reviewed_ttc_facts_replay_without_merging_reports(conn):
    documents=json.loads((Path(__file__).parent/'fixtures/agencies/ttc_identity_recovery_2026-10-29.json').read_text())
    for document in documents:
        hold(conn,dict(report_url=document['url'],document_sha256=document['sha256']),
             json.loads(document['result_json'])['contracts'])
    backfill_from_extraction(conn,'ttc')
    assert conn.execute("SELECT count(*) FROM agency_bid WHERE source='ttc_board'").fetchone()[0]==sum(d['bid_count'] for d in documents)
    for document in documents:
        contract=json.loads(document['result_json'])['contracts'][0]
        found={(r[0],r[1],r[2]) for r in conn.execute(
            'SELECT bidder_name_raw,bid_price,status FROM agency_bid WHERE native_ref=?',
            (document['native_ref'],))}
        assert found=={(b['supplier_name'],b.get('amount_raw'),b.get('status')) for b in contract['bids']}
    malformed=conn.execute("SELECT bid_price_numeric FROM agency_bid WHERE bid_price='$1,594.228.72'").fetchall()
    assert len(malformed)==1 and malformed[0][0] is None
    assert not conn.execute('SELECT * FROM agency_award').fetchall()
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('change',['other_report','multi_contract','alias','no_admin_role','wrong_primary_source'])
def test_invalid_report_identity_evidence_is_not_loaded(evidence,change):
    records,_,save=evidence;proof=records[0]
    if change=='other_report':proof['native_ref']=records[1]['report_url']
    if change=='multi_contract':proof['contract_count']=2
    if change=='alias':proof['legacy_native_ref']='9-118-55'
    if change=='no_admin_role':proof['identifiers']=[]
    if change=='wrong_primary_source':proof['sources'][0]['sha256']='e'*64
    save()
    with pytest.raises(ValueError):agency_identity._evidence()


def test_reviewed_metadata_withholding_preserves_prices_participation_and_raw_evidence(conn,evidence):
    records,contract,save=evidence
    contract['bids']=[
        dict(supplier_name='Bidder',amount_raw='$10',amount_basis='including_HST',status='compliant'),
        dict(supplier_name='Other bidder',amount_raw='$20',amount_basis='including_HST',status='compliant'),
        dict(supplier_name='Declined',amount_raw=None,amount_basis=None,status='no_bid')]
    proof=records[0]
    proof['contract_sha256']=agency_identity.contract_fingerprint(contract)
    proof['withheld_bid_fields']=[
        dict(bid_index=1,field='amount_basis',expected_value='including_HST',reason='Only winner has an explicit tax basis.'),
        dict(bid_index=1,field='status',expected_value='compliant',reason='Received submission alone does not prove commercial compliance.')]
    save();hold(conn,proof,[contract])
    cached=[tuple(r) for r in conn.execute('SELECT * FROM extraction_cache')]
    raw=copy.deepcopy(contract)
    stats=backfill_from_extraction(conn,'ttc')
    found={r['bidder_name_raw']:dict(r) for r in conn.execute('SELECT * FROM agency_bid')}
    assert (found['Bidder']['hst_basis'],found['Bidder']['status'])==('including','compliant')
    assert (found['Other bidder']['hst_basis'],found['Other bidder']['status'])==(None,None)
    assert found['Other bidder']['bid_price']=='$20'
    assert found['Other bidder']['bid_price_numeric']==20
    assert found['Declined']['status']=='no_bid'
    assert [tuple(r) for r in conn.execute('SELECT * FROM extraction_cache')]==cached
    assert contract==raw
    saved=conn.execute('SELECT * FROM agency_derivation WHERE active=1').fetchone()
    assert json.loads(saved['contract_json'])==raw
    assert json.loads(saved['identity_evidence_json'])['withheld_bid_fields']==proof['withheld_bid_fields']
    coverage=json.loads(conn.execute("SELECT summary_json FROM agency_coverage WHERE corpus='ttc'").fetchone()[0])
    assert coverage['withheld_bid_metadata_fields']==2
    assert coverage['accepted_bid_facts']==3 and coverage['accepted_no_bid_declarations']==1
    assert not conn.execute('SELECT * FROM agency_award').fetchall()
    before=[tuple(r) for r in conn.execute('SELECT id,first_seen,status,hst_basis FROM agency_bid ORDER BY id')]
    backfill_from_extraction(conn,'ttc')
    assert [tuple(r) for r in conn.execute('SELECT id,first_seen,status,hst_basis FROM agency_bid ORDER BY id')]==before


@pytest.mark.parametrize('change',['price','supplier','rank','participation','replacement','duplicate','wrong_value','wrong_index','other_buyer'])
def test_metadata_review_cannot_edit_bid_facts_or_bypass_guards(conn,evidence,change):
    records,contract,save=evidence;proof=records[0]
    hold(conn,proof,[contract]);backfill_from_extraction(conn,'ttc')
    before=[tuple(r) for r in conn.execute('SELECT * FROM agency_bid')]
    field=dict(bid_index=0,field='amount_basis',expected_value='including_HST',reason='No explicit HST evidence.')
    if change in ('price','supplier','rank','participation'):
        field['field']={'price':'amount_raw','supplier':'supplier_name','rank':'rank','participation':'status'}[change]
        field['expected_value']={'price':'$10','supplier':'Bidder','rank':1,'participation':'no_bid'}[change]
    if change=='replacement':field['replacement']='plus_HST'
    if change=='wrong_index':field['bid_index']=4
    if change=='other_buyer':proof['corpus']='zoo'
    proof['withheld_bid_fields']=[field,dict(field)] if change=='duplicate' else [field]
    save()
    with pytest.raises((ValueError,RuntimeError)):
        backfill_from_extraction(conn,'ttc')
    assert [tuple(r) for r in conn.execute('SELECT * FROM agency_bid')]==before
