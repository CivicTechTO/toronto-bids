"""#257: refused facts survive rebuilding and operational coverage is honest."""
import json
import hashlib
from pathlib import Path

import pytest

from tests.test_agency_extraction_fields import cache, contract
from toronto_bids import cli
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.store import db


@pytest.mark.parametrize('document', json.loads(
    (Path(__file__).parent / 'fixtures/agencies/identity_refusals_2026-10-21.json').read_text()))
def test_held_source_refusals_replay_with_exact_evidence(conn, document):
    from toronto_bids.buyers import seed_buyers
    seed_buyers(conn)
    assert hashlib.sha256(document['result_json'].encode()).hexdigest() == document['result_sha256']
    conn.execute('INSERT INTO background_pdf(url,kind,sha256,reference) VALUES (?,?,?,?)',
                 (document['url'], 'agency_board', document['sha256'], document['reference']))
    db.mark_extracted(conn, document['sha256'], EXTRACTOR_VERSION, document['result_json'])
    result = backfill_from_extraction(conn, document['corpus'])
    refused = conn.execute('SELECT * FROM agency_derivation WHERE reason IS NOT NULL ORDER BY contract_index').fetchall()
    assert [r['contract_index'] for r in refused] == document['refused_contract_indices']
    contracts = json.loads(document['result_json'])['contracts']
    assert [json.loads(r['contract_json']) for r in refused] == [contracts[i] for i in document['refused_contract_indices']]
    assert all(r['sha256'] == document['sha256'] and r['result_sha256'] == document['result_sha256'] for r in refused)
    assert result['coverage']['contracts_refused'] == len(refused)


def mixed(conn):
    url = cache(conn, 'trca', [contract('RFQ 10008808'), contract(None)])
    conn.execute("INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)",
                 (url + '&duplicate', 'agency_board', 'one', 'held text'))
    conn.commit()
    return url


def test_real_rebuild_retains_decisions_and_counts_unique_contracts(conn):
    mixed(conn)
    result = backfill_from_extraction(conn, 'trca')
    assert result['coverage']['contracts_accepted'] == 1
    assert result['coverage']['contracts_refused'] == 1
    assert result['coverage']['refused_bid_facts'] == 1
    assert result['coverage']['refused_award_facts'] == 1
    assert result['coverage']['source_observations'] == 4
    rows = conn.execute('SELECT * FROM agency_derivation WHERE active=1').fetchall()
    assert len(rows) == 4
    refused = [r for r in rows if r['reason']]
    assert all(r['native_ref'] is None for r in refused)
    assert json.loads(refused[0]['contract_json']) == contract(None)
    assert refused[0]['sha256'] == 'one'
    assert refused[0]['extractor_version'] == EXTRACTOR_VERSION
    assert conn.execute('SELECT count(*) FROM agency_bid').fetchone()[0] == 1
    first = [(r['id'], r['first_seen']) for r in rows]
    backfill_from_extraction(conn, 'trca')
    assert [(r['id'], r['first_seen']) for r in conn.execute('SELECT * FROM agency_derivation')] == first


def test_all_refused_evidence_survives_empty_rebuild_and_cache_change(conn):
    cache(conn, 'trca', [contract(None), contract(None)])
    backfill_from_extraction(conn, 'trca')
    assert conn.execute('SELECT count(*) FROM agency_derivation WHERE active=1').fetchone()[0] == 2
    db.mark_extracted(conn, 'one', EXTRACTOR_VERSION,
                      json.dumps({'contracts': [contract('10008808')]}))
    backfill_from_extraction(conn, 'trca')
    assert conn.execute('SELECT count(*) FROM agency_derivation WHERE active=0').fetchone()[0] == 2
    assert conn.execute('SELECT count(*) FROM agency_derivation WHERE active=1').fetchone()[0] == 1


def test_failed_swap_does_not_replace_ledger(conn, monkeypatch):
    mixed(conn)
    monkeypatch.setattr(db, 'rebuild_rows', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('swap failed')))
    with pytest.raises(RuntimeError, match='swap failed'):
        backfill_from_extraction(conn, 'trca')
    assert conn.execute('SELECT count(*) FROM agency_derivation').fetchone()[0] == 0


def test_cached_refusals_propagate_through_real_agency_capture(conn, monkeypatch):
    import toronto_bids.extract as extract
    monkeypatch.setattr(extract, 'ExtractionClient', lambda: (_ for _ in ()).throw(ValueError('no key')))
    mixed(conn)
    failures = cli._capture_agency_bodies(conn, bodies=['trca'], fetch=False,
                                        scrape=False, virtual_display=False, out=lambda m: None)
    assert [name for name, _ in failures] == ['identity:trca']
    assert '1 contract' in failures[0][1]
    assert '1 bid' in failures[0][1] and '1 award' in failures[0][1]


def test_coverage_command_reconciles_baseline_without_writes(conn, monkeypatch, tmp_path, capsys):
    from toronto_bids.models import AgencyAward
    url = mixed(conn)
    buyer = conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    db.upsert_row(conn, AgencyAward(buyer_id=buyer, native_ref='unknown',
                  supplier_name_raw='Winner', award_amount='$20', report_url=url,
                  source='trca_board'), overwrite=True)
    # Give the refused award a distinguishable value, so it cannot be mistaken for accepted evidence.
    data = {'contracts': [contract('10008808'), contract(None)]}
    data['contracts'][1]['awards'][0]['amount_raw'] = '$20'
    db.mark_extracted(conn, 'one', EXTRACTOR_VERSION, json.dumps(data))
    baseline = tmp_path / 'baseline.sqlite'
    old = db.connect(baseline)
    conn.commit()
    conn.backup(old)
    old.close()
    backfill_from_extraction(conn, 'trca')
    before = conn.total_changes
    class KeepOpen:
        def __getattr__(self, name): return getattr(conn, name)
        def close(self): pass
    monkeypatch.setattr(cli, '_open_db', lambda: KeepOpen())
    assert cli.main(['agency-coverage', '--only', 'trca', '--baseline', str(baseline)]) == 1
    result = json.loads(capsys.readouterr().out)
    missing = result['reconciliation']['agency_award']['unmatched']
    assert len(missing) == 1
    assert missing[0]['disposition'] == 'refused_identity'
    assert missing[0]['evidence'][0]['contract']['reference'] is None
    assert result['agencies']['trca']['summary']['contracts_refused'] == 1
    assert conn.total_changes == before


def test_normalization_and_alias_consolidation_are_separate_from_refusal(conn):
    from toronto_bids.models import AgencyAward
    cache(conn, 'trca', [contract('10008808')])
    buyer = conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    for ref in ['10008808', 'RFQ No. 10008808']:
        db.upsert_row(conn, AgencyAward(buyer_id=buyer, native_ref=ref,
                      supplier_name_raw='Winner', award_amount='$10', source='trca_board'), overwrite=True)
    result = backfill_from_extraction(conn, 'trca')['coverage']
    assert result['contracts_accepted'] == 1
    assert result['contracts_refused'] == 0
    assert result['aliases_normalized'] == 1
    assert result['aliases_merged'] == 1


def test_original_refusal_survives_a_later_supported_decision(conn, monkeypatch):
    from toronto_bids.linking import agency_reference
    cache(conn, 'trca', [contract('RFT 10008808 and 10009999')])
    backfill_from_extraction(conn, 'trca')
    monkeypatch.setattr(agency_reference, 'normalize_agency_reference', lambda *a: '10008808')
    backfill_from_extraction(conn, 'trca')
    rows = conn.execute('SELECT reason,active FROM agency_derivation ORDER BY id').fetchall()
    assert len(rows) == 2
    assert rows[0]['reason'] and not rows[0]['active']
    assert rows[1]['reason'] is None and rows[1]['active']


def test_reconciliation_counts_aliases_across_urls_and_lists_changed_bid_amounts(conn, tmp_path):
    from toronto_bids.agency_coverage import report
    cache(conn, 'trca', [contract('10008808')])
    backfill_from_extraction(conn, 'trca')
    old = db.connect(tmp_path / 'baseline.sqlite')
    conn.backup(old)
    old.execute("UPDATE agency_award SET native_ref='RFQ No. 10008808',report_url='another observation'")
    old.execute("UPDATE agency_bid SET bid_price='$20'")
    old.commit()
    data = report(conn, only='trca', baseline=old)['reconciliation']
    assert data['agency_award']['counts']['canonical_alias'] == 1
    assert data['agency_award']['unmatched'] == []
    assert data['agency_bid']['counts']['same_bid_key_changed_amount'] == 1
    change = data['agency_bid']['changed_bid_amounts'][0]
    assert change['prior_row']['bid_price'] == '$20'
    assert change['current_row']['bid_price'] == '$10'
    assert data['agency_bid']['unmatched'] == []
    old.close()


def test_standalone_enrichment_exits_incomplete_and_status_keeps_the_explanation(conn, monkeypatch, capsys):
    import toronto_bids.extract as extract
    monkeypatch.setattr(extract, 'ExtractionClient', lambda: (_ for _ in ()).throw(ValueError('no key')))
    mixed(conn)
    class KeepOpen:
        def __getattr__(self, name): return getattr(conn, name)
        def close(self): pass
    monkeypatch.setattr(cli, '_open_db', lambda: KeepOpen())
    assert cli.main(['enrich-agencies', '--only', 'trca']) == 1
    text = capsys.readouterr()
    assert 'FAILED  identity:trca' in text.err
    assert 'download:trca' not in text.err
    assert cli.main(['status']) == 0
    assert 'trca: incomplete; 1 accepted, 1 refused contracts' in capsys.readouterr().out


def test_inspection_distinguishes_uninitialized_and_complete_coverage(conn):
    from toronto_bids.agency_coverage import report
    assert report(conn, only='trca')['incomplete']
    cache(conn, 'trca', [contract('10008808')])
    backfill_from_extraction(conn, 'trca')
    assert not report(conn, only='trca')['incomplete']
