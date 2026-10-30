"""Historical TRCA awards bind to carried resolutions, without invented prices."""
import copy,hashlib,json
from pathlib import Path

import pytest
from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking import agency_identity
from toronto_bids.store import db

DOC=json.loads((Path(__file__).parent/'fixtures/agencies/trca_identity_recovery_2026-10-29.json').read_text())[0]


def hold(conn):
    seed_buyers(conn)
    conn.execute('INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)',
        (DOC['url'],'agency_board',DOC['sha256'],'held source'))
    db.mark_extracted(conn,DOC['sha256'],EXTRACTOR_VERSION,DOC['result_json']);conn.commit()


def test_historical_source_recovers_three_unpriced_awards_with_reviewed_dates(conn):
    hold(conn);before=db.get_extraction(conn,DOC['sha256'],EXTRACTOR_VERSION)
    backfill_from_extraction(conn,'trca')
    rows={r['native_ref']:dict(r) for r in conn.execute('SELECT * FROM agency_award') if r['native_ref'] in DOC['new_refs']}
    assert len(rows)==3
    for ref,date in DOC['expected_dates'].items():
        assert rows[ref]['award_date']==date
        assert rows[ref]['award_amount'] is None and rows[ref]['award_amount_numeric'] is None
        assert rows[ref]['report_url']==DOC['url']
    assert db.get_extraction(conn,DOC['sha256'],EXTRACTOR_VERSION)==before
    ledger=[dict(r) for r in conn.execute('SELECT * FROM agency_derivation WHERE active=1')]
    reviewed=[r for r in ledger if r['native_ref'] in DOC['new_refs']]
    assert len(reviewed)==3
    assert {json.loads(r['contract_json'])['decision_date'] for r in reviewed}=={'2017-05-17','2017-08-30','2017-11-03'}
    assert all(json.loads(r['identity_evidence_json'])['decision_date_review'] for r in reviewed)
    first=[tuple(r) for r in conn.execute('SELECT id,native_ref,award_date,first_seen FROM agency_award ORDER BY id')]
    backfill_from_extraction(conn,'trca')
    assert [tuple(r) for r in conn.execute('SELECT id,native_ref,award_date,first_seen FROM agency_award ORDER BY id')]==first


@pytest.mark.parametrize('change',['reviewed_payload','other_payload','url','version','document'])
def test_changed_review_context_keeps_previously_verified_awards(conn,monkeypatch,change):
    hold(conn);backfill_from_extraction(conn,'trca')
    before=[tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]
    result=json.loads(DOC['result_json'])
    if change in ('reviewed_payload','other_payload'):
        result['contracts'][2 if change=='reviewed_payload' else 0]['title']+=' changed'
        db.mark_extracted(conn,DOC['sha256'],EXTRACTOR_VERSION,json.dumps(result))
    if change=='url':conn.execute('UPDATE background_pdf SET url=?',('https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=unreviewed',))
    if change=='document':
        conn.execute("UPDATE background_pdf SET sha256=?",('e'*64,))
        db.mark_extracted(conn,'e'*64,EXTRACTOR_VERSION,DOC['result_json'])
    if change=='version':monkeypatch.setattr(agency_identity,'EXTRACTOR_VERSION','changed')
    conn.commit()
    with pytest.raises(RuntimeError,match='existing rows kept'):backfill_from_extraction(conn,'trca')
    assert [tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]==before


def test_classification_and_refusal_baseline_reconcile_every_reviewed_key():
    root=Path(__file__).resolve().parents[2]
    review=json.loads((root/'docs/trca-identity-review-2026-10-29.json').read_text())
    all_keys=set();eligible=set()
    for doc in review['documents']:
        for contract in doc['contracts']:
            key=(doc['sha256'],doc['extractor_version'],doc['result_sha256'],contract['contract_index'])
            assert key not in all_keys;all_keys.add(key)
            if contract['eligible']:eligible.add(key)
    assert len(all_keys)==1023 and len(eligible)==3
    baseline=json.loads((root/'scrapers/toronto_bids/data/known_agency_refusals.json').read_text())
    remaining={(r['sha256'],r['extractor_version'],r['result_sha256'],r['contract_index']) for r in baseline['corpora']['trca']}
    assert remaining==all_keys-eligible and len(remaining)==1020


@pytest.mark.parametrize('change',['extra_field','wrong_expected_date'])
def test_date_review_cannot_patch_other_facts_or_ignore_value_guard(conn,tmp_path,monkeypatch,change):
    hold(conn);backfill_from_extraction(conn,'trca')
    before=[tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]
    path=Path(__file__).resolve().parents[1]/'toronto_bids/data/agency_identity_evidence.json'
    data=json.loads(path.read_text());proof=next(p for p in data['decisions'] if p.get('decision_date_review'))
    if change=='extra_field':proof['decision_date_review']['award_amount']='$999'
    else:proof['decision_date_review']['expected_value']='2000-01-01'
    (tmp_path/'agency_identity_evidence.json').write_text(json.dumps(data))
    agency_identity._evidence.cache_clear()
    monkeypatch.setattr(agency_identity.resources,'files',lambda _:tmp_path)
    try:
        with pytest.raises(RuntimeError,match='existing rows kept'):backfill_from_extraction(conn,'trca')
        assert [tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]==before
    finally:agency_identity._evidence.cache_clear()
