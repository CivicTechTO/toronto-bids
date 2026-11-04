"""Carried claims and participation roles derive without rewriting model evidence."""
import json
import re
from pathlib import Path

import pytest

from toronto_bids.buyers import seed_buyers
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.store import db

DOCUMENTS=json.loads((Path(__file__).parent/'fixtures/agencies/trca_stage_decisions_2026-11-04.json').read_text())


def hold(conn):
    seed_buyers(conn)
    for d in DOCUMENTS:
        context=json.dumps({'meeting':{'ID':'november-6','MeetingPassed':False}}) if 'result_json' in d else None
        conn.execute('INSERT INTO background_pdf(url,kind,sha256,text,award_review_context_json) VALUES (?,?,?,?,?)',
                     (d['url'],'agency_board',d['sha256'],d['text'],context))
        if 'result_json' in d:
            db.mark_extracted(conn,d['sha256'],d['extractor_version'],d['result_json'])
    conn.commit()


def test_only_reviewed_carried_awards_derive_and_shared_caps_are_not_individual_prices(conn):
    hold(conn)
    cache=[tuple(r) for r in conn.execute('SELECT * FROM extraction_cache ORDER BY sha256')]
    result=backfill_from_extraction(conn,'trca')
    awards=[dict(r) for r in conn.execute('SELECT * FROM agency_award')]
    assert len(awards)==5
    shared=[r for r in awards if r['native_ref']=='10042138']
    assert len(shared)==3
    assert all(r['award_amount'] is None and r['award_amount_numeric'] is None and r['value_confidential']==0 and r['award_date']=='2026-10-02' for r in shared)
    priced={r['native_ref']:(r['award_amount'],r['award_date']) for r in awards if r['award_amount']}
    assert priced=={'10045025':('$279,451.00','2026-09-18'),'10045514':('$234,386.00','2026-09-18')}
    assert [tuple(r) for r in conn.execute('SELECT * FROM extraction_cache ORDER BY sha256')]==cache
    assert result['coverage']['withheld_award_amounts']==3


def test_eighteen_applicants_five_finalists_and_three_tenders_stay_distinct(conn):
    hold(conn)
    result=backfill_from_extraction(conn,'trca')
    bids=[dict(r) for r in conn.execute("SELECT * FROM agency_bid WHERE native_ref IN ('10044924','10045110')")]
    assert len(bids)==9 and {b['result_stage'] for b in bids}=={'Tender submissions'}
    assert sum(b['native_ref']=='10045110' for b in bids)==3
    d=conn.execute("SELECT * FROM agency_derivation WHERE native_ref='10045110' AND active=1").fetchone()
    raw=json.loads(d['contract_json']);proof=json.loads(d['identity_evidence_json']);roles=proof['participation_review']
    assert len(raw['prequalified'])==13  # Legacy prompt means first-stage-only, unsuccessful applicants.
    assert len(roles['prequalification_submissions'])==18
    assert len(roles['qualified_for_tender'])==len(roles['invited_to_tender'])==5
    assert len(roles['tender_submissions'])==3
    assert 'Cambridge Landscape & Construction Ltd.' not in roles['qualified_for_tender']
    assert 'Cambridge Landscape & Construction Ltd.' in roles['prequalification_submissions']
    assert result['coverage']['withheld_award_facts']==9
    assert result['coverage']['accepted_award_facts']==5
    assert result['coverage']['accepted_bid_facts']==11  # Nine tenders plus two audit-service proposals.


def test_reviewed_future_recommendation_clears_stale_derived_awarded_status(conn):
    from toronto_bids.models import AgencySolicitation
    hold(conn)
    buyer=conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    db.upsert_row(conn,AgencySolicitation(buyer_id=buyer,native_ref='10045642',status='awarded',source='trca_board'),overwrite=True)
    backfill_from_extraction(conn,'trca')
    assert conn.execute("SELECT status FROM agency_solicitation WHERE native_ref='10045642'").fetchone()[0] is None


def test_changed_independent_decision_source_preserves_all_previously_verified_rows(conn):
    import pytest
    hold(conn);backfill_from_extraction(conn,'trca')
    before=[tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]
    independent=next(d for d in DOCUMENTS if d['document_id']=='20908')
    conn.execute("UPDATE background_pdf SET sha256=? WHERE url=?",('f'*64,independent['url']));conn.commit()
    with pytest.raises(RuntimeError,match='source missing or changed.*existing rows kept'):
        backfill_from_extraction(conn,'trca')
    assert [tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]==before


def test_reconciliation_classifies_shared_prices_and_proposals_as_source_withholding(conn,tmp_path):
    from toronto_bids.agency_coverage import report
    from toronto_bids.models import AgencyAward
    hold(conn)
    buyer=conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    for d in DOCUMENTS:
        for contract in json.loads(d.get('result_json','{"contracts":[]}'))['contracts']:
            ref=re.search(r'\d{8}',contract['reference'])[0]
            for a in contract['awards']:
                db.upsert_row(conn,AgencyAward(buyer_id=buyer,native_ref=ref,supplier_name_raw=a['supplier_name'],award_amount=a['amount_raw'],report_url=d['url'],source='trca_board'),overwrite=True)
    conn.commit();baseline=db.connect(tmp_path/'before.sqlite');conn.backup(baseline)
    backfill_from_extraction(conn,'trca')
    reconciliation=report(conn,only='trca',baseline=baseline)['reconciliation']['agency_award']
    assert reconciliation['counts']['refused_source_facts']==12  # Nine withheld awards and three unsupported individual prices.
    assert not reconciliation['counts'].get('refused_identity')
    baseline.close()


@pytest.mark.parametrize('change',['missing_minutes','changed_minutes_hash','changed_minutes_passage','changed_contract','changed_other_contract','changed_version'])
def test_changed_review_evidence_aborts_before_swapping_facts(conn,monkeypatch,change):
    from toronto_bids.linking import agency_identity
    hold(conn);backfill_from_extraction(conn,'trca')
    before={t:[tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')] for t in ['agency_bid','agency_award','agency_derivation']}
    minutes=next(d for d in DOCUMENTS if d['document_id']=='20908')
    if change=='missing_minutes':conn.execute('DELETE FROM background_pdf WHERE url=?',(minutes['url'],))
    if change=='changed_minutes_hash':conn.execute("UPDATE background_pdf SET sha256=? WHERE url=?",('e'*64,minutes['url']))
    if change=='changed_minutes_passage':conn.execute("UPDATE background_pdf SET text='No reviewed decision here' WHERE url=?",(minutes['url'],))
    if change in ['changed_contract','changed_other_contract']:
        d=next(d for d in DOCUMENTS if d['document_id']=='21022');raw=json.loads(d['result_json'])
        raw['contracts'][0 if change=='changed_contract' else 2]['title']+=' changed'
        db.mark_extracted(conn,d['sha256'],d['extractor_version'],json.dumps(raw))
    if change=='changed_version':monkeypatch.setattr(agency_identity,'EXTRACTOR_VERSION','changed-review-version')
    conn.commit()
    with pytest.raises(RuntimeError,match='existing rows kept'):backfill_from_extraction(conn,'trca')
    assert {t:[tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')] for t in before}==before


@pytest.mark.parametrize('change',['missing_decision_role','wrong_decision_date','uncovered_award','extra_price_override','changed_participation','unrelated_decision'])
def test_registry_cannot_release_proposals_or_invent_facts(conn,tmp_path,monkeypatch,change):
    from toronto_bids.linking import agency_identity
    hold(conn);backfill_from_extraction(conn,'trca')
    before=[tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]
    path=Path(__file__).resolve().parents[1]/'toronto_bids/data/agency_identity_evidence.json';data=json.loads(path.read_text())
    proof=next(p for p in data['decisions'] if p.get('award_review') and p['native_ref']=='10042138')
    if change=='missing_decision_role':del proof['sources'][0]['role']
    if change=='wrong_decision_date':proof['award_review'][0]['decision_date']='2026-11-06'
    if change=='uncovered_award':proof['award_review'].pop()
    if change=='extra_price_override':proof['award_review'][0]['amount_raw']='$999'
    if change=='unrelated_decision':proof['sources'][0]['passages']=proof['sources'][0]['passages'][:1]
    if change=='changed_participation':
        proof=next(p for p in data['decisions'] if p.get('participation_review') and p['native_ref']=='10045110')
        proof['participation_review']['tender_submissions'].append('Unproven bidder')
    (tmp_path/'agency_identity_evidence.json').write_text(json.dumps(data))
    agency_identity._evidence.cache_clear();monkeypatch.setattr(agency_identity.resources,'files',lambda _:tmp_path)
    try:
        with pytest.raises(RuntimeError,match='existing rows kept'):backfill_from_extraction(conn,'trca')
        assert [tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]==before
    finally:agency_identity._evidence.cache_clear()


def test_independently_supported_award_and_portal_status_survive(conn):
    from tests.test_agency_extraction_fields import cache,contract
    from toronto_bids.models import AgencySolicitation
    hold(conn);cache(conn,'trca',[contract('10045642')],tag='independent')
    buyer=conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    db.upsert_row(conn,AgencySolicitation(buyer_id=buyer,native_ref='10045642',status='awarded',source='trca_board'),overwrite=True)
    db.upsert_row(conn,AgencySolicitation(buyer_id=buyer,native_ref='10045110',status='Awarded',source='portal'),overwrite=True)
    backfill_from_extraction(conn,'trca')
    assert conn.execute("SELECT status FROM agency_solicitation WHERE native_ref='10045642'").fetchone()[0]=='awarded'
    assert conn.execute("SELECT status FROM agency_solicitation WHERE native_ref='10045110'").fetchone()[0]=='Awarded'


def test_explicitly_reviewed_proposal_is_withdrawn_even_when_no_other_awards_derive(conn):
    from toronto_bids.models import AgencyAward
    seed_buyers(conn)
    d=next(d for d in DOCUMENTS if d['document_id']=='21044')
    conn.execute('INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)',(d['url'],'agency_board',d['sha256'],d['text']))
    db.mark_extracted(conn,d['sha256'],d['extractor_version'],d['result_json'])
    buyer=conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    db.upsert_row(conn,AgencyAward(buyer_id=buyer,native_ref='10045642',supplier_name_raw='Doane Grant Thornton LLP',award_amount='$171,595',report_url=d['url'],source='trca_board'),overwrite=True)
    db.upsert_row(conn,AgencyAward(buyer_id=buyer,native_ref='UNREVIEWED-HISTORY',supplier_name_raw='Archived recipient',award_amount='$50',report_url='https://example.com/held.pdf',source='trca_board'),overwrite=True)
    backfill_from_extraction(conn,'trca')
    remaining=conn.execute('SELECT * FROM agency_award').fetchall()
    assert len(remaining)==1 and remaining[0]['native_ref']=='UNREVIEWED-HISTORY'
    assert conn.execute('SELECT count(*) FROM agency_bid').fetchone()[0]==2
    assert json.loads(conn.execute('SELECT contract_json FROM agency_derivation').fetchone()[0])['awards'][0]['amount_raw']=='$171,595'


def test_replaying_a_review_preserves_fact_and_evidence_history(conn):
    hold(conn);backfill_from_extraction(conn,'trca')
    tables=['agency_award','agency_bid','agency_derivation']
    before={t:[tuple(r) for r in conn.execute(f'SELECT id,first_seen FROM {t} ORDER BY id')] for t in tables}
    backfill_from_extraction(conn,'trca')
    assert {t:[tuple(r) for r in conn.execute(f'SELECT id,first_seen FROM {t} ORDER BY id')] for t in tables}==before
