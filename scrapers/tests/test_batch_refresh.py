"""Offline derivation and publication acknowledgement after asynchronous capture."""
import json
from pathlib import Path

import pytest

from toronto_bids import batch_extraction as batch
from toronto_bids.batch_refresh import refresh
from toronto_bids.buyers import seed_buyers
from toronto_bids.claude import ClaudeExtractionClient
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.store import db
from tests.test_batch_extraction import API


@pytest.fixture
def ready(tmp_path, monkeypatch, conn):
    monkeypatch.setattr('toronto_bids.config.DATA_DIR', tmp_path)
    monkeypatch.delenv('TB_CLAUDE_MODEL', raising=False)
    monkeypatch.delenv('TB_CLAUDE_EFFORT', raising=False)
    api = API()
    queue = batch.BatchQueue(tmp_path / 'ops.sqlite', http=api, api_key='test')
    client = ClaudeExtractionClient(api_key='test')
    seed_buyers(conn)
    conn.execute("INSERT INTO background_pdf (url,kind,sha256,text,reference) VALUES ('https://toronto.ca/ep/report.pdf','agency_board','pdf-sha','report','2026.EP1.1')")
    conn.commit()
    with pytest.raises(batch.ExtractionPending):
        queue.document('pdf-sha', EXTRACTOR_VERSION, [client.request_body('report')], source='pdf')
    queue.submit()
    custom = api.posts[0]['requests'][0]['custom_id']
    api.outcomes[custom] = {'type': 'succeeded', 'message': {
        'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({'contracts': [{
            'reference': 'EP01-2026', 'title': 'Works',
            'bids': [{'supplier_name': 'Supplier', 'amount_raw': '$100'}],
            'awards': [{'supplier_name': 'Supplier', 'amount_raw': '$100'}],
        }]})}], 'usage': {'input_tokens': 1000, 'output_tokens': 200}}}
    return queue, api, tmp_path / 'handoff.json'


def test_refresh_derives_exports_then_waits_for_publication_ack(ready, conn):
    queue, api, path = ready
    exports = []
    result = refresh(queue, conn, export=lambda: exports.append('export'), manifest_path=path)
    assert result['ready_documents'] and exports == ['export']
    assert conn.execute('SELECT COUNT(*) FROM agency_bid').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM agency_award').fetchone()[0] == 1
    assert queue.status()['documents'] == {'cached': 1}
    document_ids = json.loads(path.read_text())['documents']
    assert document_ids == result['ready_documents']
    queue.acknowledge(document_ids)
    path.unlink()
    result = refresh(queue, conn, export=lambda: exports.append('unexpected'), manifest_path=path)
    assert result['ready_documents'] == [] and not path.exists()
    assert exports == ['export'] and len(api.posts) == 1


def test_failed_export_keeps_work_ready_and_source_identity_stable(ready, conn):
    queue, api, path = ready
    def fail(): raise RuntimeError('export interrupted')
    with pytest.raises(RuntimeError):
        refresh(queue, conn, export=fail, manifest_path=path)
    assert not path.exists() and queue.status()['documents'] == {'cached': 1}
    before = tuple(conn.execute('SELECT id,first_seen FROM agency_bid').fetchone())
    refresh(queue, conn, export=lambda: None, manifest_path=path)
    assert tuple(conn.execute('SELECT id,first_seen FROM agency_bid').fetchone()) == before
    assert len(api.posts) == 1


def test_acknowledges_only_exported_ids_when_more_results_arrive(ready, conn):
    queue, api, path = ready
    result = refresh(queue, conn, export=lambda: None, manifest_path=path)
    ids = result['ready_documents']
    # Simulate another completed document arriving before publication finishes.
    ops = queue._connect()
    with ops:
        ops.execute("INSERT INTO batch_document (id,sha256,version,jobs,status,source) VALUES ('new','new',?,'[]','cached','pdf')", (EXTRACTOR_VERSION,))
    ops.close()
    queue.acknowledge(ids)
    assert [d['id'] for d in queue.ready_documents()] == ['new']


def test_offline_waterfront_replay_uses_held_snapshot_and_never_extracts(conn, tmp_path, monkeypatch):
    import hashlib
    from toronto_bids.sources import waterfront as wf
    blocks = [dict(source_key='block', publication_month='2026-01', raw_text='Works - Supplier ($100)', source_position=0, labels='[]')]
    monkeypatch.setattr(wf, 'parse_publication', lambda _: blocks)
    class Page:
        def get_bytes(self, url): return b'real held bytes'
    # Initial capture queues one request and records the exact held snapshot pointer.
    class Pending:
        def extract(self, text): raise batch.ExtractionPending('pending')
    wf.capture_awards(conn, Page(), data_dir=tmp_path, client=Pending(), log=lambda _: None)
    text = wf.extraction_text('2026-01', blocks)
    digest = hashlib.sha256(text.encode()).hexdigest()
    db.mark_extracted(conn, digest, EXTRACTOR_VERSION, json.dumps({'contracts': [{'title':'Works','awards':[{'supplier_name':'Supplier','amount_raw':'($100)'}]}]}))
    def forbid(*args, **kwargs): raise AssertionError('No model or source network access permitted')
    monkeypatch.setattr(wf, 'ExtractionClient', forbid)
    result = wf.replay_cached_awards(conn, data_dir=tmp_path, only_extractions={digest}, log=lambda _: None)
    assert result['awards'] == 1 and result['processed'] == [digest] and not result['failures']
    assert conn.execute('SELECT award_amount_numeric FROM published_award').fetchone()[0] == 100
    latest = (tmp_path / 'waterfront/latest').read_text()
    (tmp_path / 'waterfront' / (latest+'.html')).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash'):
        wf.replay_cached_awards(conn, data_dir=tmp_path)


def test_coverage_refusals_are_reported_but_supported_export_can_publish(ready, conn, monkeypatch):
    queue, api, path = ready
    monkeypatch.setattr('toronto_bids.batch_refresh.backfill_from_extraction',
                        lambda *a, **k: {'coverage': {'contracts_refused': 2}})
    result = refresh(queue, conn, export=lambda: None, manifest_path=path)
    assert result['ready_documents'] and path.exists()
    assert any('refused or withheld' in error for error in result['errors'])


def test_nonzero_export_result_does_not_create_publication_handoff(ready, conn):
    queue, api, path = ready
    with pytest.raises(ValueError, match='export failed'):
        refresh(queue, conn, export=lambda: 1, manifest_path=path)
    assert not path.exists() and queue.status()['documents'] == {'cached': 1}


def test_waterfront_factory_tags_manifests_for_offline_collection(conn, tmp_path, monkeypatch):
    from toronto_bids.extract import ExtractionClient
    from toronto_bids.sources import waterfront as wf
    monkeypatch.setattr('toronto_bids.config.DATA_DIR', tmp_path)
    monkeypatch.setattr(wf, 'parse_publication', lambda _: [dict(
        source_key='block', publication_month='2026-01', raw_text='Works - Supplier ($100)', source_position=0, labels='[]')])
    class Page:
        def get_bytes(self, url): return b'held bytes'
    with batch.nightly_batching(enabled=True):
        client = ExtractionClient(api_key='test')
        result = wf.capture_awards(conn, Page(), data_dir=tmp_path, client=client, log=lambda _: None)
        ops = client._claude.queue._connect()
        try:
            assert ops.execute('SELECT source FROM batch_document').fetchone()[0] == 'waterfront'
        finally:
            ops.close()
    assert result['pending'] == 1 and not result['failures']


def test_budget_blocked_outbox_does_not_block_publication_of_completed_work(ready, conn):
    queue, api, path = ready
    body = ClaudeExtractionClient(api_key='test').request_body('another document')
    with pytest.raises(batch.ExtractionPending):
        queue.document('next', EXTRACTOR_VERSION, [body], source='pdf')
    queue.budget.limit = 2000 # Exactly the cost of the already completed first request.
    result = refresh(queue, conn, export=lambda: None, manifest_path=path)
    assert result['ready_documents'] and path.exists()
    assert any('budget' in error for error in result['errors'])
    assert len(api.posts) == 1 and queue.status()['jobs']['queued'] == 1


def test_bd_report_refusal_is_visible_in_batch_refresh(ready, conn):
    queue,api,path=ready
    conn.execute("UPDATE background_pdf SET kind='bgrd',reference='2014.BD1.1' WHERE sha256='pdf-sha'")
    conn.commit()
    result=refresh(queue,conn,export=lambda:None,manifest_path=path)
    assert any('coverage:bd_report: 1 report(s) refused' in e for e in result['errors'])
    assert conn.execute("SELECT COUNT(*) FROM bid WHERE source='bd_report'").fetchone()[0]==0
