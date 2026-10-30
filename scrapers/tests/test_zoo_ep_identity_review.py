"""Real Zoo/EP holds reconcile to existing facts without duplicate award growth."""
import copy,hashlib,json
from pathlib import Path
import pytest
from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.known_gaps import refused_contracts
from toronto_bids.linking import agency_identity
from toronto_bids.store import db

DOCS=json.loads((Path(__file__).parent/'fixtures/agencies/zoo_ep_identity_holds_2026-10-30.json').read_text())


def hold(conn):
    seed_buyers(conn)
    for doc in DOCS:
        conn.execute('INSERT INTO background_pdf(url,kind,sha256,text,reference) VALUES (?,?,?,?,?)',
            (doc['url'],'agency_board',doc['sha256'],'held source',doc['reference']))
        db.mark_extracted(conn,doc['sha256'],doc['extractor_version'],doc['result_json'])
    conn.commit()


def facts(conn):
    return {t:[{k:v for k,v in dict(r).items() if k!='last_seen'} for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')]
            for t in ('agency_award','agency_bid','agency_solicitation')}


def test_identity_only_reconciliation_preserves_priced_awards_and_raw_caches(conn,monkeypatch):
    hold(conn);evidence=agency_identity._evidence()
    with monkeypatch.context() as patch:
        patch.setattr(agency_identity,'_evidence',lambda:{k:p for k,p in evidence.items() if 'representative_source' not in p})
        for corpus in ('zoo','ep'):backfill_from_extraction(conn,corpus)
    assert len(refused_contracts(conn,'zoo'))==31 and len(refused_contracts(conn,'ep'))==6
    before=facts(conn);raw=[tuple(r) for r in conn.execute('SELECT * FROM extraction_cache ORDER BY sha256')]
    for corpus in ('zoo','ep'):backfill_from_extraction(conn,corpus)
    assert facts(conn)==before
    assert [tuple(r) for r in conn.execute('SELECT * FROM extraction_cache ORDER BY sha256')]==raw
    assert len(refused_contracts(conn,'zoo'))==29 and len(refused_contracts(conn,'ep'))==6
    decisions=[dict(r) for r in conn.execute("SELECT * FROM agency_derivation WHERE corpus='zoo' AND active=1")]
    duplicate=[r for r in decisions if json.loads(r['identity_evidence_json'] or '{}').get('representative_source')]
    assert len(duplicate)==2 and all(r['native_ref'] and r['reason'] for r in duplicate)
    assert {r['native_ref'] for r in duplicate}=={'RFP 58 (2018-10)','RFP #21 (2018-04)'}
    assert all(json.loads(r['contract_json'])['awards'][0]['amount_raw'] is None for r in duplicate)
    backfill_from_extraction(conn,'zoo');assert facts(conn)==before


@pytest.mark.parametrize('change',['missing_cache','changed_payload','changed_document'])
def test_unavailable_or_changed_original_aborts_before_losing_priced_awards(conn,change):
    hold(conn);backfill_from_extraction(conn,'zoo');before=facts(conn)
    original=next(d for d in DOCS if d.get('role')=='published_representative')
    if change=='missing_cache':conn.execute('DELETE FROM extraction_cache WHERE sha256=?',(original['sha256'],))
    if change=='changed_payload':
        result=json.loads(original['result_json']);result['contracts'][0]['awards'][0]['amount_raw']='$1'
        db.mark_extracted(conn,original['sha256'],EXTRACTOR_VERSION,json.dumps(result))
    if change=='changed_document':
        conn.execute('UPDATE background_pdf SET sha256=? WHERE sha256=?',('e'*64,original['sha256']))
        db.mark_extracted(conn,'e'*64,EXTRACTOR_VERSION,original['result_json'])
    conn.commit()
    with pytest.raises(RuntimeError,match='existing rows kept'):backfill_from_extraction(conn,'zoo')
    assert facts(conn)==before


def test_all_classified_contexts_match_the_remaining_baseline():
    root=Path(__file__).resolve().parents[2]
    review=json.loads((root/'docs/zoo-ep-identity-review-2026-10-30.json').read_text())
    baseline=json.loads((root/'scrapers/toronto_bids/data/known_agency_refusals.json').read_text())
    assert len(review['contracts'])==37 and sum(r['identity_recovered'] for r in review['contracts'])==2
    for corpus,count in [('zoo',29),('ep',6)]:
        held={(r['document_sha256'],r['extractor_version'],r['result_sha256'],r['contract_index'])
              for r in review['contracts'] if r['corpus']==corpus and not r['identity_recovered']}
        recorded={(r['sha256'],r['extractor_version'],r['result_sha256'],r['contract_index']) for r in baseline['corpora'][corpus]}
        assert held==recorded and len(held)==count


def test_reviewed_identity_holds_stay_incomplete_but_changed_results_are_unknown(conn):
    from toronto_bids import agency_coverage,known_gaps
    hold(conn)
    for corpus in ('zoo','ep'):backfill_from_extraction(conn,corpus)
    for corpus in ('zoo','ep'):
        failure=(f'identity:{corpus}','incomplete coverage: reviewed holds')
        unknown,known=known_gaps.classify(conn,[failure])
        assert not unknown and len(known)==1
        assert agency_coverage.report(conn,only=corpus)['incomplete']
    doc=next(d for d in DOCS if d['corpus']=='ep')
    result=json.loads(doc['result_json']);result['contracts'][doc['refused_indices'][0]]['title']+=' changed'
    db.mark_extracted(conn,doc['sha256'],EXTRACTOR_VERSION,json.dumps(result))
    backfill_from_extraction(conn,'ep')
    failure=('identity:ep','incomplete coverage: same count with changed result')
    unknown,known=known_gaps.classify(conn,[failure]);assert unknown==[failure] and known==[]
