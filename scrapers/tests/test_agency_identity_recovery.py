"""#258: source-supported recovery must run through the actual cache rebuild."""
import json
from pathlib import Path

import pytest

from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.store import db


RECORDED = json.loads((Path(__file__).parent / 'fixtures/agencies/identity_recovery_2026-10-21.json').read_text())


def held(conn, document):
    seed_buyers(conn)
    conn.execute('INSERT INTO background_pdf(url,kind,sha256,text,reference) VALUES (?,?,?,?,?)',
                 (document['url'], 'agency_board', document['sha256'], 'held text', document['reference']))
    db.mark_extracted(conn, document['sha256'], EXTRACTOR_VERSION, document['result_json'])


@pytest.mark.parametrize('document', RECORDED)
def test_all_reviewed_source_relationships_rebuild_exact_facts(conn, document):
    held(conn, document)
    result = backfill_from_extraction(conn, 'trca')
    contracts = json.loads(document['result_json'])['contracts']
    for recovery in document['recoveries']:
        index, native = recovery['contract_index'], recovery['native_ref']
        evidence = conn.execute('SELECT * FROM agency_derivation WHERE active=1 AND contract_index=?', (index,)).fetchone()
        assert evidence['native_ref'] == native
        assert bool(evidence['reason']) == bool(recovery.get('withheld_awards'))
        proof = json.loads(evidence['identity_evidence_json'])
        assert proof['native_ref'] == native
        assert all(s['passages'] and s['sha256'] and s['url'] for s in proof['sources'])
        assert json.loads(evidence['contract_json']) == contracts[index]
        for table, kind, name, amount in [('agency_award', 'awards', 'supplier_name_raw', 'award_amount'),
                                          ('agency_bid', 'bids', 'bidder_name_raw', 'bid_price')]:
            found = {(r[0], r[1]) for r in conn.execute(f'SELECT {name},{amount} FROM {table} WHERE native_ref=?', (native,))}
            expected = {(f['supplier_name'], f.get('amount_raw')) for f in contracts[index].get(kind, []) if f.get('supplier_name')}
            if kind == 'awards' and recovery.get('withheld_awards'):
                assert not found
                continue
            assert expected <= found
    assert result['coverage']['contracts_recovered'] == len(document['recoveries'])


@pytest.mark.parametrize('change', ['buyer', 'document', 'version', 'contract'])
def test_reviewed_identity_does_not_escape_its_evidence(change, monkeypatch):
    from toronto_bids.linking import agency_identity
    document = RECORDED[0]
    contract = json.loads(document['result_json'])['contracts'][0]
    corpus, sha = 'trca', document['sha256']
    assert agency_identity.reviewed_identity(corpus, sha, contract)
    if change == 'buyer': corpus = 'zoo'
    if change == 'document': sha = 'a' * 64
    if change == 'version': monkeypatch.setattr(agency_identity, 'EXTRACTOR_VERSION', 'unreviewed-version')
    if change == 'contract': contract['title'] = 'A different procurement'
    assert agency_identity.reviewed_identity(corpus, sha, contract) is None


def test_recovery_preserves_old_refusal_and_canonical_history(conn, monkeypatch):
    from toronto_bids.linking import agency_identity
    from toronto_bids.models import AgencyAward
    document = RECORDED[0]
    held(conn, document)
    resolver = agency_identity.reviewed_identity
    monkeypatch.setattr(agency_identity, 'reviewed_identity', lambda *a: None)
    backfill_from_extraction(conn, 'trca')
    old = conn.execute('SELECT id,first_seen FROM agency_derivation').fetchone()
    monkeypatch.setattr(agency_identity, 'reviewed_identity', resolver)
    contract = json.loads(document['result_json'])['contracts'][0]
    award = contract['awards'][0]
    buyer = conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    db.upsert_row(conn, AgencyAward(buyer_id=buyer, native_ref='10043863',
                  supplier_name_raw=award['supplier_name'], award_amount=award.get('amount_raw'), source='trca_board'), overwrite=True)
    conn.execute("UPDATE agency_award SET first_seen='2000-01-01'")
    first = conn.execute('SELECT id FROM agency_award').fetchone()[0]
    backfill_from_extraction(conn, 'trca')
    assert tuple(conn.execute('SELECT id,first_seen FROM agency_derivation WHERE active=0').fetchone()) == tuple(old)
    assert tuple(conn.execute('SELECT id,first_seen FROM agency_award WHERE id=?', (first,)).fetchone()) == (first, '2000-01-01')
    decisions = [tuple(r) for r in conn.execute('SELECT id,first_seen,contract_json FROM agency_derivation ORDER BY id')]
    backfill_from_extraction(conn, 'trca')
    assert [tuple(r) for r in conn.execute('SELECT id,first_seen,contract_json FROM agency_derivation ORDER BY id')] == decisions


def test_evidence_column_migrates_without_losing_refusals(conn):
    from tests.test_agency_extraction_fields import cache, contract
    cache(conn, 'trca', [contract(None), contract(None)])
    backfill_from_extraction(conn, 'trca')
    before = [tuple(r) for r in conn.execute('SELECT id,reason FROM agency_derivation')]
    conn.execute('ALTER TABLE agency_derivation DROP COLUMN identity_evidence_json')
    db.init_db(conn)
    assert [tuple(r) for r in conn.execute('SELECT id,reason FROM agency_derivation')] == before
    assert all(r[0] is None for r in conn.execute('SELECT identity_evidence_json FROM agency_derivation'))


def test_reviewed_suffix_correction_preserves_legacy_row_ids_and_history(conn):
    from toronto_bids.models import AgencyAward, AgencyBid
    document = next(d for d in RECORDED if d['url'].endswith('DocumentId=19466'))
    held(conn, document)
    contract = json.loads(document['result_json'])['contracts'][1]
    buyer = conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    for fact in contract['awards']:
        db.upsert_row(conn, AgencyAward(buyer_id=buyer, native_ref='10042722',
                      supplier_name_raw=fact['supplier_name'], award_amount=fact['amount_raw'],
                      source='trca_board', report_url=document['url']), overwrite=True)
    for fact in contract['bids']:
        db.upsert_row(conn, AgencyBid(buyer_id=buyer, native_ref='10042722',
                      bidder_name_raw=fact['supplier_name'], bid_price=fact['amount_raw'],
                      source='trca_board', report_url=document['url']), overwrite=True)
    before = {}
    for table in ['agency_bid', 'agency_award']:
        conn.execute(f"UPDATE {table} SET first_seen='2000-01-01'")
        before[table] = [tuple(r) for r in conn.execute(f'SELECT id,first_seen FROM {table} ORDER BY id')]
    backfill_from_extraction(conn, 'trca')
    for table in before:
        assert [tuple(r) for r in conn.execute(f'SELECT id,first_seen FROM {table} ORDER BY id')] == before[table]
        assert {r[0] for r in conn.execute(f'SELECT native_ref FROM {table}')} == {'10042722-01'}


@pytest.mark.parametrize('difference', ['report', 'supplier', 'amount'])
def test_reviewed_history_alias_does_not_merge_unproven_observations(conn, difference):
    from toronto_bids.models import AgencyAward
    from toronto_bids.linking.agency_reference import normalize_stored_references
    buyer = seed_buyers(conn)['trca']
    original = dict(buyer_id=buyer, native_ref='10042722', supplier_name_raw='Winner',
                    award_amount='$10', report_url='https://example.com/held', source='trca_board')
    if difference == 'report': original['report_url'] += '/different'
    if difference == 'supplier': original['supplier_name_raw'] = 'Another supplier'
    if difference == 'amount': original['award_amount'] = '$20'
    db.upsert_row(conn, AgencyAward(**original), overwrite=True)
    proof = dict(old_native_ref='10042722', native_ref='10042722-01', report_url='https://example.com/held',
                 bids=set(), awards={('Winner', '$10')})
    counts = normalize_stored_references(conn, 'trca', buyer, 'trca_board', proven_aliases=[proof])
    assert counts == {'normalized': 0, 'merged': 0}
    assert conn.execute('SELECT native_ref FROM agency_award').fetchone()[0] == '10042722'


def test_aggregate_vor_ceiling_is_not_restored_as_eight_vendor_awards(conn, monkeypatch):
    from toronto_bids.extraction import extract_and_backfill
    import toronto_bids.extract as extract
    document = next(d for d in RECORDED if d['url'].endswith('DocumentId=19889'))
    held(conn, document)
    monkeypatch.setattr(extract, 'ExtractionClient', lambda: (_ for _ in ()).throw(ValueError('no key')))
    failures = []
    result = extract_and_backfill(conn, 'trca', failures=failures)
    summary = result['coverage']
    assert result['bids_written'] == 8
    assert result['awards_written'] == 0
    assert summary['contracts_accepted'] == summary['contracts_refused'] == summary['contracts_withheld'] == 1
    assert summary['accepted_bid_facts'] == 8
    assert summary['refused_bid_facts'] == 0
    assert summary['withheld_award_facts'] == summary['refused_award_facts'] == 8
    assert [name for name, _ in failures] == ['coverage:trca']
    assert '8 individual award claim(s)' in failures[0][1]
    original = conn.execute('SELECT contract_json,reason,native_ref FROM agency_derivation WHERE active=1').fetchone()
    assert original['native_ref'] == '10042179'
    assert 'aggregate VOR ceiling' in original['reason']
    assert all(a['amount_raw'] == '$1,035,000' for a in json.loads(original['contract_json'])['awards'])


def test_reconciliation_follows_proven_identity_across_duplicate_reports(conn, tmp_path):
    from toronto_bids.agency_coverage import report
    from toronto_bids.models import AgencyAward
    documents = [d for d in RECORDED if d['url'].endswith(('DocumentId=5341', 'DocumentId=5426'))]
    for document in documents:
        held(conn, document)
        contract = json.loads(document['result_json'])['contracts'][0]
        buyer = conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
        db.upsert_row(conn, AgencyAward(buyer_id=buyer, native_ref=contract['reference'],
                      supplier_name_raw=contract['awards'][0]['supplier_name'], report_url=document['url'],
                      source='trca_board'), overwrite=True)
    conn.commit()
    old = db.connect(tmp_path / 'before.sqlite')
    conn.backup(old)
    backfill_from_extraction(conn, 'trca')
    result = report(conn, only='trca', baseline=old)['reconciliation']['agency_award']
    assert not result['unmatched']
    assert result['counts']['represented_by_accepted_evidence'] == 1
    assert conn.execute('SELECT count(*) FROM agency_award').fetchone()[0] == 10
    old.close()


def test_published_solicitation_and_contract_roles_restore_the_held_facts(conn):
    records = json.loads((Path(__file__).parent / 'fixtures/agencies/identity_refusals_2026-10-21.json').read_text())
    source = next(r for r in records if r['url'].endswith('DocumentId=19635'))
    seed_buyers(conn)
    conn.execute('INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)',
                 (source['url'], 'agency_board', source['sha256'], 'held text'))
    db.mark_extracted(conn, source['sha256'], EXTRACTOR_VERSION, source['result_json'])
    result = backfill_from_extraction(conn, 'trca')
    assert result['contracts_refused'] == 0
    assert result['bids_written'] == 2
    assert result['awards_written'] == 2
    assert {r[0] for r in conn.execute('SELECT native_ref FROM agency_award')} == {'10043863'}
    assert '10043792' not in {r[0] for r in conn.execute('SELECT native_ref FROM agency_solicitation')}
