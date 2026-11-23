"""Lifecycle tests: durable requests, money boundaries, conservative imports, and refresh."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from toronto_bids import batch_extraction as batch
from toronto_bids.batch_refresh import refresh
from toronto_bids.buyers import seed_buyers
from toronto_bids.claude import ClaudeExtractionClient
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.store import db


class API:
    def __init__(self):
        self.posts = []
        self.requests = {}
        self.ended = True
        self.outcomes = {}
        self.post_status = 200
        self.post_json = None
        self.timeout_after_acceptance = False
        self.mutate_lines = lambda lines: lines

    def post(self, url, *, json, headers, timeout):
        self.posts.append(json)
        remote = 'msgbatch_' + str(len(self.posts))
        self.requests[remote] = json['requests']
        if self.timeout_after_acceptance:
            raise httpx.ReadTimeout('receipt lost')
        payload = self.post_json if self.post_json is not None else {'id': remote}
        return httpx.Response(self.post_status, json=payload, request=httpx.Request('POST', url))

    def get(self, url, *, headers, timeout):
        if not url.endswith('/results'):
            remote = url.rsplit('/', 1)[-1]
            return httpx.Response(
                200,
                json={'id': remote, 'processing_status': 'ended' if self.ended else 'in_progress'},
                request=httpx.Request('GET', url),
            )
        remote = url.rsplit('/', 2)[-2]
        lines = []
        for i, request in enumerate(self.requests[remote]):
            result = self.outcomes.get(request['custom_id'])
            if result is None:
                result = {
                    'type': 'succeeded',
                    'message': {
                        'model': request['params']['model'],
                        'stop_reason': 'end_turn',
                        'content': [
                            {
                                'type': 'text',
                                'text': json.dumps(
                                    {
                                        'contracts': [
                                            {
                                                'reference': str(i),
                                                'bids': [{'supplier_name': 'Supplier', 'amount_raw': '$100'}],
                                            }
                                        ]
                                    }
                                ),
                            }
                        ],
                        'usage': {'input_tokens': 1000, 'output_tokens': 200},
                    },
                }
            lines.append({'custom_id': request['custom_id'], 'result': result})
        lines = self.mutate_lines(list(reversed(lines)))
        return httpx.Response(200, text='\n'.join(json.dumps(line) for line in lines), request=httpx.Request('GET', url))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr('toronto_bids.config.DATA_DIR', tmp_path)
    monkeypatch.delenv('TB_CLAUDE_MODEL', raising=False)
    monkeypatch.delenv('TB_CLAUDE_EFFORT', raising=False)
    monkeypatch.delenv('TB_LLM_MONTHLY_USD', raising=False)
    api = API()
    queue = batch.BatchQueue(tmp_path / 'llm-usage.sqlite', http=api, api_key='test')
    client = ClaudeExtractionClient(api_key='test')
    with batch.nightly_batching(enabled=False):
        yield queue, api, client.request_body


@pytest.fixture
def ready(setup, conn, tmp_path):
    queue, api, body = setup
    seed_buyers(conn)
    conn.execute(
        "INSERT INTO background_pdf (url,kind,sha256,text,reference) "
        "VALUES ('https://toronto.ca/ep/report.pdf','agency_board','pdf-sha','report','2026.EP1.1')"
    )
    conn.commit()
    with pytest.raises(batch.ExtractionPending):
        queue.document('pdf-sha', EXTRACTOR_VERSION, [body('report')], source='pdf')
    queue.submit()
    custom = api.posts[0]['requests'][0]['custom_id']
    api.outcomes[custom] = {
        'type': 'succeeded',
        'message': {
            'stop_reason': 'end_turn',
            'content': [
                {
                    'type': 'text',
                    'text': json.dumps(
                        {
                            'contracts': [
                                {
                                    'reference': 'EP01-2026',
                                    'title': 'Works',
                                    'bids': [{'supplier_name': 'Supplier', 'amount_raw': '$100'}],
                                    'awards': [{'supplier_name': 'Supplier', 'amount_raw': '$100'}],
                                }
                            ]
                        }
                    ),
                }
            ],
            'usage': {'input_tokens': 1000, 'output_tokens': 200},
        },
    }
    return queue, api, tmp_path / 'handoff.json'


def enqueue(queue, body, doc='doc', text='one', version=EXTRACTOR_VERSION, source='unspecified'):
    with pytest.raises(batch.ExtractionPending):
        queue.document(doc, version, [body(text)], source=source)


def charges(queue):
    with sqlite3.connect(queue.path) as conn:
        return conn.execute('SELECT charged_microdollars,status FROM request ORDER BY id').fetchall()


def test_restart_deduplicates_unsubmitted_document_without_spending(setup):
    queue, api, body = setup
    enqueue(queue, body)
    restarted = batch.BatchQueue(queue.path, http=api, api_key='test')
    enqueue(restarted, body)
    assert len(queue.jobs()) == 1 and charges(queue) == [] and api.posts == []
    assert queue.status()['documents'] == {'waiting': 1}


def test_reordered_results_import_only_complete_document_and_charge_half(setup, conn):
    queue, api, body = setup
    with pytest.raises(batch.ExtractionPending):
        queue.document('whole-pdf', EXTRACTOR_VERSION, [body('first'), body('second')])
    queue.submit()
    assert len(charges(queue)) == 2 and all(state == 'reserved' for _, state in charges(queue))
    result = queue.collect(conn)
    assert result['errors'] == [] and result['cached'] == 1
    cached = json.loads(db.get_extraction(conn, 'whole-pdf', EXTRACTOR_VERSION))
    assert [c['reference'] for c in cached['contracts']] == ['0', '1']
    assert conn.execute('SELECT COUNT(*) FROM extraction_cache').fetchone()[0] == 1
    assert charges(queue) == [(2000, 'reported'), (2000, 'reported')]
    assert queue.collect(conn)['cached'] == 0
    assert len(api.posts) == 1


def test_in_progress_keeps_reservations_and_never_caches(setup, conn):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    reserved = charges(queue)
    api.ended = False
    assert queue.collect(conn)['cached'] == 0
    assert db.get_extraction(conn, 'doc', EXTRACTOR_VERSION) is None
    assert charges(queue) == reserved
    assert queue.has_work()


def test_concurrent_submitters_send_once(setup):
    queue, api, body = setup
    enqueue(queue, body)

    def submit(_):
        return batch.BatchQueue(queue.path, http=api, api_key='test').submit()['submitted']

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == [0, 1]
    assert len(api.posts) == 1 and len(charges(queue)) == 1


def test_unknown_receipt_never_resubmits_and_can_be_verified_then_attached(setup, conn):
    queue, api, body = setup
    enqueue(queue, body)
    api.timeout_after_acceptance = True
    with pytest.raises(httpx.ReadTimeout):
        queue.submit()
    assert queue.submit()['submitted'] == 0 and len(api.posts) == 1
    assert charges(queue)[0][1] == 'reserved'
    local = queue.status()['submissions'][0]['id']
    assert queue.collect(conn)['errors']
    api.ended = False
    with pytest.raises(ValueError, match='ended'):
        queue.attach(local, 'msgbatch_1')
    api.ended = True
    queue.attach(local, 'msgbatch_1')
    assert queue.collect(conn)['cached'] == 1
    assert charges(queue) == [(2000, 'reported')]


@pytest.mark.parametrize(
    'mutation, expect_foreign',
    [
        (lambda lines: lines + lines, False),
        (lambda lines: [], False),
        (lambda lines: [dict(lines[0], custom_id='foreign')], True),
    ],
)
def test_receipt_membership_must_be_exact_before_any_cache_or_settlement(setup, conn, mutation, expect_foreign):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    reserved = charges(queue)
    api.mutate_lines = mutation
    assert queue.collect(conn)['errors']
    assert charges(queue) == reserved
    assert db.get_extraction(conn, 'doc', EXTRACTOR_VERSION) is None
    ops = queue._connect()
    try:
        receipt = ops.execute('SELECT receipt FROM batch_submission').fetchone()[0]
        assert receipt is not None
        if expect_foreign:
            assert json.loads(receipt)['custom_id'] == 'foreign'
    finally:
        ops.close()


@pytest.mark.parametrize('kind', ['errored', 'expired', 'canceled'])
def test_terminal_unbilled_outcome_requires_explicit_retry(setup, conn, kind):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    custom = api.posts[0]['requests'][0]['custom_id']
    api.outcomes[custom] = {'type': kind}
    assert queue.collect(conn)['errors']
    assert charges(queue) == [(0, 'rejected')]
    with pytest.raises(ValueError, match='failed'):
        queue.document('doc', EXTRACTOR_VERSION, [body('one')])
    assert queue.submit()['submitted'] == 0
    queue.retry(queue.jobs()[0]['id'])
    queue.submit()
    assert api.posts[1]['requests'][0]['custom_id'] != custom
    assert queue.collect(conn)['cached'] == 1
    assert charges(queue) == [(0, 'rejected'), (2000, 'reported')]


def test_one_failed_chunk_keeps_whole_document_out_of_cache(setup, conn):
    queue, api, body = setup
    with pytest.raises(batch.ExtractionPending):
        queue.document('doc', EXTRACTOR_VERSION, [body('one'), body('two')])
    enqueue(queue, body, doc='independent', text='three')
    queue.submit()
    custom = api.posts[0]['requests'][1]['custom_id']
    api.outcomes[custom] = {'type': 'expired'}
    assert queue.collect(conn)['cached'] == 1
    assert db.get_extraction(conn, 'doc', EXTRACTOR_VERSION) is None
    assert db.get_extraction(conn, 'independent', EXTRACTOR_VERSION)


def test_invalid_output_is_billed_but_not_cached(setup, conn):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    custom = api.posts[0]['requests'][0]['custom_id']
    api.outcomes[custom] = {
        'type': 'succeeded',
        'message': {
            'stop_reason': 'max_tokens',
            'content': [{'type': 'text', 'text': '{"contracts": []}'}],
            'usage': {'input_tokens': 1000, 'output_tokens': 200},
        },
    }
    assert queue.collect(conn)['errors']
    assert charges(queue) == [(2000, 'reported')]
    assert db.get_extraction(conn, 'doc', EXTRACTOR_VERSION) is None


def test_missing_usage_keeps_conservative_reservation(setup, conn):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    reserved = charges(queue)
    custom = api.posts[0]['requests'][0]['custom_id']
    api.outcomes[custom] = {
        'type': 'succeeded',
        'message': {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '{"contracts": []}'}]},
    }
    assert queue.collect(conn)['errors'] and charges(queue) == reserved


def test_preserves_existing_cache_and_timestamp(setup, conn):
    queue, api, body = setup
    enqueue(queue, body)
    db.mark_extracted(conn, 'doc', EXTRACTOR_VERSION, '{"contracts": []}')
    before = tuple(conn.execute('SELECT * FROM extraction_cache').fetchone())
    queue.submit()
    assert queue.collect(conn)['cached'] == 0
    assert tuple(conn.execute('SELECT * FROM extraction_cache').fetchone()) == before


def test_stale_prompt_results_still_reconcile_without_import(setup, conn, monkeypatch):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    monkeypatch.setattr(batch, 'EXTRACTOR_VERSION', 'new-prompt')
    api.ended = False
    assert queue.collect(conn)['cached'] == 0
    assert queue.has_work()
    api.ended = True
    assert queue.collect(conn)['cached'] == 0
    assert charges(queue) == [(2000, 'reported')]
    assert conn.execute('SELECT COUNT(*) FROM extraction_cache').fetchone()[0] == 0


def test_submit_budget_check_happens_before_network(setup):
    queue, api, body = setup
    enqueue(queue, body)
    queue.budget.limit = 0
    with pytest.raises(ValueError, match='budget'):
        queue.submit()
    assert api.posts == [] and charges(queue) == []
    assert queue.jobs()[0]['status'] == 'queued'


def test_definite_submission_rejection_releases_allowance(setup):
    queue, api, body = setup
    enqueue(queue, body)
    api.post_status = 400
    with pytest.raises(httpx.HTTPStatusError):
        queue.submit()
    assert charges(queue) == [(0, 'rejected')]
    assert queue.jobs()[0]['status'] == 'failed'


def test_corpus_queues_every_chunk_and_limit_counts_pending_documents(setup, conn):
    from toronto_bids.extraction import extract_corpus

    queue, api, body = setup

    class Client:
        def extract_document(self, sha, chunks):
            return queue.document(sha, EXTRACTOR_VERSION, [body(text) for text in chunks])

    text = 'one paragraph\n\ntwo paragraph\n\nthree paragraph'
    for i in range(2):
        conn.execute(
            "INSERT INTO background_pdf (url,kind,sha256,text) VALUES (?,'award_summary',?,?)",
            (f'https://example.com/{i}', str(i), text),
        )
    conn.commit()
    stats = extract_corpus(conn, 'award_summary', client=Client(), labels={}, max_chars=20, limit=1)
    assert stats['pending'] == 1 and stats['errors'] == 0
    assert len(queue.jobs()) == 3
    assert conn.execute('SELECT COUNT(*) FROM extraction_cache').fetchone()[0] == 0
    queue.submit()
    sent = [request['params']['messages'][0]['content'][-1]['text'] for request in api.posts[0]['requests']]
    assert all(len(chunk) <= 20 for chunk in sent)
    assert ''.join(sent) == text
    queue.collect(conn)
    assert db.get_extraction(conn, '0', EXTRACTOR_VERSION)
    assert db.get_extraction(conn, '1', EXTRACTOR_VERSION) is None


def test_pending_step_is_not_success_or_failure(setup, conn):
    from toronto_bids.cli import _run_step

    queue, api, body = setup
    steps, failures = [], []

    def step():
        try:
            queue.document('doc', EXTRACTOR_VERSION, [body('one')])
        except batch.ExtractionPending:
            pass
        return 'captured'

    _run_step(steps, failures, 'awards', step, conn=conn)
    assert steps[0]['status'] == 'pending' and failures == []
    assert conn.execute('SELECT status FROM sync_run').fetchone()[0] == 'pending'


def test_cache_commit_can_be_recovered_after_manifest_commit_failure(setup, conn):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    ops = queue._connect()
    ops.execute(
        "CREATE TRIGGER simulate_crash BEFORE UPDATE OF status ON batch_document "
        "WHEN NEW.status='cached' BEGIN SELECT RAISE(FAIL, 'crash after cache commit'); END"
    )
    ops.commit()
    with pytest.raises(sqlite3.IntegrityError, match='crash'):
        queue.collect(conn)
    held = tuple(conn.execute('SELECT * FROM extraction_cache').fetchone())
    assert charges(queue) == [(2000, 'reported')]
    ops.execute('DROP TRIGGER simulate_crash')
    ops.commit()
    ops.close()
    assert queue.collect(conn)['cached'] == 0
    assert tuple(conn.execute('SELECT * FROM extraction_cache').fetchone()) == held
    assert charges(queue) == [(2000, 'reported')]


def test_waterfront_pending_retains_prior_claims_and_marks_coverage(setup, conn, monkeypatch, tmp_path):
    from toronto_bids.sources import waterfront as wf

    queue, api, body = setup
    blocks = [
        dict(
            source_key='block',
            publication_month='2026-01',
            raw_text='Works - Supplier ($100)',
            source_position=0,
            labels='[]',
        )
    ]
    monkeypatch.setattr(wf, 'parse_publication', lambda content: blocks)

    class Page:
        def get_bytes(self, url):
            return b'held source'

    class Immediate:
        def extract(self, text):
            return {'contracts': [{'title': 'Works', 'awards': [{'supplier_name': 'Supplier', 'amount_raw': '$100'}]}]}

    class Deferred:
        def extract(self, text):
            return queue.document(hashlib.sha256(text.encode()).hexdigest(), EXTRACTOR_VERSION, [body(text)])

    wf.capture_awards(conn, Page(), data_dir=tmp_path, client=Immediate(), log=lambda _: None)
    held = [tuple(r) for r in conn.execute('SELECT * FROM published_award')]
    conn.execute('DELETE FROM extraction_cache')
    conn.commit()
    result = wf.capture_awards(conn, Page(), data_dir=tmp_path, client=Deferred(), log=lambda _: None)
    assert result['pending'] == 1 and result['failures'] == []
    assert [tuple(r) for r in conn.execute('SELECT * FROM published_award')] == held
    assert conn.execute('SELECT status FROM publication_extraction').fetchone()[0] == 'pending'


def test_status_cli_is_offline_and_submit_failure_is_nonzero(setup, monkeypatch, capsys):
    from toronto_bids.cli import main

    queue, api, body = setup
    monkeypatch.setattr(batch, 'BatchQueue', lambda: queue)
    enqueue(queue, body)
    assert main(['extraction-batches', 'status']) == 0
    assert 'queued' in capsys.readouterr().out and not api.posts
    api.post_status = 401
    assert main(['extraction-batches', 'submit']) == 1
    assert 'Batch extraction:' in capsys.readouterr().err


def test_standalone_client_remains_sync_and_nightly_mode_is_scoped(setup):
    from toronto_bids.extract import ExtractionClient

    with batch.nightly_batching(enabled=True):
        assert isinstance(ExtractionClient(api_key='test')._claude, batch.BatchExtractionClient)
    assert isinstance(ExtractionClient(api_key='test')._claude, ClaudeExtractionClient)


def test_unknown_outcome_keeps_allowance_without_blocking_other_documents(setup, conn):
    queue, api, body = setup
    enqueue(queue, body, doc='one', text='one')
    enqueue(queue, body, doc='two', text='two')
    queue.submit()
    custom = api.posts[0]['requests'][0]['custom_id']
    api.outcomes[custom] = {'type': 'future_unknown_status'}
    result = queue.collect(conn)
    assert result['cached'] == 1 and result['errors']
    assert db.get_extraction(conn, 'one', EXTRACTOR_VERSION) is None
    assert db.get_extraction(conn, 'two', EXTRACTOR_VERSION)
    assert charges(queue)[0][1] == 'reserved' and charges(queue)[1] == (2000, 'reported')


def test_submission_timeout_http_status_is_uncertain_not_free(setup):
    queue, api, body = setup
    enqueue(queue, body)
    api.post_status = 408
    with pytest.raises(httpx.HTTPStatusError):
        queue.submit()
    assert charges(queue)[0][1] == 'reserved'
    assert queue.status()['submissions'][0]['status'] == 'uncertain'
    assert queue.submit()['submitted'] == 0


def test_explicit_corpus_batch_command_submits_and_exits_pending(setup, monkeypatch):
    from toronto_bids import cli

    queue, api, body = setup
    monkeypatch.setattr(batch, 'BatchQueue', lambda: queue)

    def corpus(args):
        assert batch.batching_enabled()
        enqueue(queue, body)
        return 2

    monkeypatch.setattr(cli, '_cmd_extract_corpus', corpus)
    assert cli.main(['extract', '--batch', '--corpus', 'award_summary']) == 2
    assert len(api.posts) == 1 and not batch.batching_enabled()
    assert cli.main(['extract', '--batch', '--corpus', 'award_summary']) == 2
    assert len(api.posts) == 1


def test_persistence_preserves_qualified_request_and_schema_field_order(setup):
    queue, api, body = setup
    original = body('source text')
    with pytest.raises(batch.ExtractionPending):
        queue.document('doc', EXTRACTOR_VERSION, [original])
    restarted = batch.BatchQueue(queue.path, http=api, api_key='test')
    restarted.submit()
    submitted = api.posts[0]['requests'][0]['params']
    assert json.dumps(submitted, ensure_ascii=False) == json.dumps(original, ensure_ascii=False)


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

    def fail():
        raise RuntimeError('export interrupted')

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
    ops = queue._connect()
    with ops:
        ops.execute(
            "INSERT INTO batch_document (id,sha256,version,jobs,status,source) VALUES ('new','new',?,'[]','cached','pdf')",
            (EXTRACTOR_VERSION,),
        )
    ops.close()
    queue.acknowledge(ids)
    assert [d['id'] for d in queue.ready_documents()] == ['new']


def test_offline_waterfront_replay_uses_held_snapshot_and_never_extracts(conn, tmp_path, monkeypatch):
    from toronto_bids.sources import waterfront as wf

    blocks = [
        dict(
            source_key='block',
            publication_month='2026-01',
            raw_text='Works - Supplier ($100)',
            source_position=0,
            labels='[]',
        )
    ]
    monkeypatch.setattr(wf, 'parse_publication', lambda _: blocks)

    class Page:
        def get_bytes(self, url):
            return b'real held bytes'

    class Pending:
        def extract(self, text):
            raise batch.ExtractionPending('pending')

    wf.capture_awards(conn, Page(), data_dir=tmp_path, client=Pending(), log=lambda _: None)
    text = wf.extraction_text('2026-01', blocks)
    digest = hashlib.sha256(text.encode()).hexdigest()
    db.mark_extracted(
        conn,
        digest,
        EXTRACTOR_VERSION,
        json.dumps({'contracts': [{'title': 'Works', 'awards': [{'supplier_name': 'Supplier', 'amount_raw': '($100)'}]}]}),
    )

    def forbid(*args, **kwargs):
        raise AssertionError('No model or source network access permitted')

    monkeypatch.setattr(wf, 'ExtractionClient', forbid)
    result = wf.replay_cached_awards(conn, data_dir=tmp_path, only_extractions={digest}, log=lambda _: None)
    assert result['awards'] == 1 and result['processed'] == [digest] and not result['failures']
    assert conn.execute('SELECT award_amount_numeric FROM published_award').fetchone()[0] == 100
    latest = (tmp_path / 'waterfront/latest').read_text()
    (tmp_path / 'waterfront' / (latest + '.html')).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash'):
        wf.replay_cached_awards(conn, data_dir=tmp_path)


def test_coverage_refusals_are_reported_but_supported_export_can_publish(ready, conn, monkeypatch):
    queue, api, path = ready
    monkeypatch.setattr(
        'toronto_bids.batch_refresh.backfill_from_extraction',
        lambda *a, **k: {'coverage': {'contracts_refused': 2}},
    )
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
    monkeypatch.setattr(
        wf,
        'parse_publication',
        lambda _: [
            dict(
                source_key='block',
                publication_month='2026-01',
                raw_text='Works - Supplier ($100)',
                source_position=0,
                labels='[]',
            )
        ],
    )

    class Page:
        def get_bytes(self, url):
            return b'held bytes'

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
    queue.budget.limit = 2000
    result = refresh(queue, conn, export=lambda: None, manifest_path=path)
    assert result['ready_documents'] and path.exists()
    assert any('budget' in error for error in result['errors'])
    assert len(api.posts) == 1 and queue.status()['jobs']['queued'] == 1


def test_refresh_handles_partial_submit_budget_blocked_return(ready, conn):
    from toronto_bids.claude import request_allowance

    queue, api, path = ready
    body = ClaudeExtractionClient(api_key='test').request_body
    req1, req2 = body('next one'), body('next two')
    with pytest.raises(batch.ExtractionPending):
        queue.document('next-1', EXTRACTOR_VERSION, [req1], source='pdf')
    with pytest.raises(batch.ExtractionPending):
        queue.document('next-2', EXTRACTOR_VERSION, [req2], source='pdf')
    # Room for the completed job (2000) plus exactly one of the two queued requests.
    queue.budget.limit = 2000 + request_allowance(req1, batch=True)

    result = refresh(queue, conn, export=lambda: None, manifest_path=path)
    assert result['submission']['submitted'] == 1
    assert result['submission']['budget_blocked']
    assert any('budget' in err for err in result['errors'])
    assert result['ready_documents'] and path.exists()
    assert len(api.posts) == 2 and queue.status()['jobs']['queued'] == 1


def test_bd_report_refusal_is_visible_in_batch_refresh(ready, conn):
    queue, api, path = ready
    conn.execute("UPDATE background_pdf SET kind='bgrd',reference='2014.BD1.1' WHERE sha256='pdf-sha'")
    conn.commit()
    result = refresh(queue, conn, export=lambda: None, manifest_path=path)
    assert any('coverage:bd_report: 1 report(s) refused' in e for e in result['errors'])
    assert conn.execute("SELECT COUNT(*) FROM bid WHERE source='bd_report'").fetchone()[0] == 0


def test_refresh_applies_waterfront_documents_and_isolates_replay_errors(setup, conn, monkeypatch, tmp_path):
    from toronto_bids.sources import waterfront as wf

    queue, api, body = setup
    enqueue(queue, body, doc='wf-sha', text='Works - Supplier ($100)', source='waterfront')
    queue.submit()
    path = tmp_path / 'handoff.json'
    exports = []

    monkeypatch.setattr(
        wf,
        'replay_cached_awards',
        lambda archive, *, only_extractions, log: {
            'processed': list(only_extractions),
            'failures': [('waterfront:2026-01', 'unresolved note')],
        },
    )
    result = refresh(queue, conn, export=lambda: exports.append('ok'), manifest_path=path)
    assert result['ready_documents'] and path.exists() and exports == ['ok']
    assert 'waterfront:2026-01: unresolved note' in result['errors']

    path.unlink()
    exports.clear()
    monkeypatch.setattr(
        wf,
        'replay_cached_awards',
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('snapshot hash mismatch')),
    )
    failed = refresh(queue, conn, export=lambda: exports.append('unexpected'), manifest_path=path)
    assert failed['ready_documents'] == [] and not path.exists() and exports == []
    assert any('apply:waterfront: snapshot hash mismatch' in err for err in failed['errors'])


def test_refresh_marks_unreferenced_pdf_documents_superseded(setup, conn, tmp_path):
    queue, api, body = setup
    enqueue(queue, body, doc='orphan-sha', text='orphaned report', source='pdf')
    queue.submit()
    path = tmp_path / 'handoff.json'
    exports = []

    result = refresh(queue, conn, export=lambda: exports.append('unexpected'), manifest_path=path)
    assert result['ready_documents'] == []
    assert len(result['ignored_documents']) == 1
    assert exports == [] and not path.exists()
    assert queue.status()['documents'] == {'superseded': 1}


def test_refresh_isolates_per_corpus_backfill_exceptions(ready, conn, monkeypatch, tmp_path):
    queue, api, path = ready
    body = ClaudeExtractionClient(api_key='test').request_body
    conn.execute(
        "INSERT INTO background_pdf (url,kind,sha256,text,document_number) "
        "VALUES ('https://example.com/as.pdf','award_summary','as-sha','form','1234567890')"
    )
    conn.commit()
    enqueue(queue, body, doc='as-sha', text='form', source='pdf')

    from toronto_bids import batch_refresh

    real_backfill = batch_refresh.backfill_from_extraction

    def fail_ep(archive, corpus, **kwargs):
        if corpus == 'ep':
            raise RuntimeError('ep broken')
        return real_backfill(archive, corpus, **kwargs)

    monkeypatch.setattr(batch_refresh, 'backfill_from_extraction', fail_ep)
    # Submit the second doc too so both end up cached together.
    queue.submit()
    exports = []
    partial = refresh(queue, conn, export=lambda: exports.append('partial'), manifest_path=path)
    assert any('apply:ep: ep broken' in err for err in partial['errors'])
    assert len(partial['ready_documents']) == 1
    assert exports == ['partial'] and path.exists()
    queue.acknowledge(partial['ready_documents'])
    path.unlink()

    # Now only the failing ep doc remains ready -> export must be skipped.
    exports.clear()
    all_failed = refresh(queue, conn, export=lambda: exports.append('unexpected'), manifest_path=path)
    assert all_failed['ready_documents'] == []
    assert exports == [] and not path.exists()
    assert any('apply:ep: ep broken' in err for err in all_failed['errors'])


def test_waterfront_write_rollback_keeps_result_pending_and_retries_after_clearing(
    setup, conn, monkeypatch, tmp_path
):
    from toronto_bids.sources import waterfront as wf

    queue, api, body = setup
    blocks = [
        dict(source_key='block-jan', publication_month='2026-01', raw_text='Jan Works - Alpha Co ($100)',
             source_position=0, labels='[]'),
        dict(source_key='block-feb', publication_month='2026-02', raw_text='Feb Works - Beta Inc ($200)',
             source_position=1, labels='[]'),
    ]
    monkeypatch.setattr(wf, 'parse_publication', lambda _: blocks)

    class HeldPage:
        def get_bytes(self, url):
            return b'waterfront html snapshot'

    class PendingClient:
        def extract(self, text):
            raise batch.ExtractionPending('pending')

    wf.capture_awards(conn, HeldPage(), data_dir=tmp_path, client=PendingClient(), log=lambda _: None)

    jan_text = wf.extraction_text('2026-01', [blocks[0]])
    feb_text = wf.extraction_text('2026-02', [blocks[1]])
    jan_sha = hashlib.sha256(jan_text.encode()).hexdigest()
    feb_sha = hashlib.sha256(feb_text.encode()).hexdigest()

    enqueue(queue, body, doc=jan_sha, text=jan_text, source='waterfront')
    enqueue(queue, body, doc=feb_sha, text=feb_text, source='waterfront')
    submitted = queue.submit()
    req_list = api.requests[submitted['remote_id']]
    payloads = [
        {'contracts': [{'title': 'Jan Works', 'awards': [{'supplier_name': 'Alpha Co', 'amount_raw': '($100)'}]}]},
        {'contracts': [{'title': 'Feb Works', 'awards': [{'supplier_name': 'Beta Inc', 'amount_raw': '($200)'}]}]},
    ]
    for req, payload in zip(req_list, payloads):
        api.outcomes[req['custom_id']] = {
            'type': 'succeeded',
            'message': {
                'model': req['params']['model'],
                'stop_reason': 'end_turn',
                'content': [{'type': 'text', 'text': json.dumps(payload)}],
                'usage': {'input_tokens': 1000, 'output_tokens': 200},
            },
        }

    # Force a DB write failure on February's published_award insert only.
    conn.execute(
        "CREATE TRIGGER fail_feb_award BEFORE INSERT ON published_award "
        "WHEN NEW.publication_key = 'block-feb' "
        "BEGIN SELECT RAISE(ABORT, 'forced award write failure'); END;"
    )
    conn.commit()

    path = tmp_path / 'handoff.json'
    exports = []
    first = refresh(queue, conn, export=lambda: exports.append('run1'), manifest_path=path)
    assert any('waterfront:2026-02: forced award write failure' in err for err in first['errors'])
    assert exports == ['run1'] and path.exists()
    # Only January committed and entered the publication handoff; February stays pending (not superseded).
    assert len(first['ready_documents']) == 1
    assert first['ignored_documents'] == []
    stored = [r[0] for r in conn.execute('SELECT supplier_name_raw FROM published_award ORDER BY supplier_name_raw')]
    assert stored == ['Alpha Co']

    # Acknowledge January as published; February remains cached for the next refresh.
    queue.acknowledge(first['ready_documents'])
    assert queue.status()['documents'] == {'applied': 1, 'cached': 1}
    path.unlink()

    # Clear the write failure and refresh again: February now commits and enters handoff.
    conn.execute('DROP TRIGGER fail_feb_award')
    conn.commit()
    exports.clear()
    second = refresh(queue, conn, export=lambda: exports.append('run2'), manifest_path=path)
    assert second['errors'] == []
    assert exports == ['run2'] and path.exists()
    assert len(second['ready_documents']) == 1
    stored = [r[0] for r in conn.execute('SELECT supplier_name_raw FROM published_award ORDER BY supplier_name_raw')]
    assert stored == ['Alpha Co', 'Beta Inc']
    queue.acknowledge(second['ready_documents'])
    assert queue.status()['documents'] == {'applied': 2}


def test_batch_split_document_merges_bids_awards_and_conflicts(setup, conn):
    queue, api, body = setup
    with pytest.raises(batch.ExtractionPending):
        queue.document('split-doc-sha', EXTRACTOR_VERSION, [body('chunk-1-bids'), body('chunk-2-awards')], source='pdf')
    submitted = queue.submit()
    reqs = api.requests[submitted['remote_id']]
    chunk_results = [
        {
            'contracts': [
                {
                    'reference': 'RFT 777',
                    'decision_date': '2025-04-01',
                    'bids': [
                        {'supplier_name': 'Alpha Ltd.', 'amount_raw': '$100,000', 'amount_basis': 'plus_HST', 'status': 'compliant'},
                        {'supplier_name': 'Beta Inc.', 'amount_raw': '$125,000', 'amount_basis': 'plus_HST', 'status': 'compliant'},
                    ],
                    'awards': [],
                }
            ]
        },
        {
            'contracts': [
                {
                    'reference': 'RFT 777',
                    'decision_date': '2025-04-15',
                    'bids': [
                        {'supplier_name': 'Alpha Ltd.', 'amount_raw': '$100,000', 'amount_basis': 'plus_HST', 'status': 'compliant'},
                    ],
                    'awards': [
                        {'supplier_name': 'Alpha Ltd.', 'amount_raw': '$100,000', 'amount_basis': 'plus_HST', 'value_confidential': False},
                    ],
                }
            ]
        },
    ]
    for req, payload in zip(reqs, chunk_results):
        api.outcomes[req['custom_id']] = {
            'type': 'succeeded',
            'message': {
                'model': req['params']['model'],
                'stop_reason': 'end_turn',
                'content': [{'type': 'text', 'text': json.dumps(payload)}],
                'usage': {'input_tokens': 1000, 'output_tokens': 200},
            },
        }
    collected = queue.collect(conn)
    assert collected['cached'] == 1 and collected['errors'] == []
    cached = json.loads(db.get_extraction(conn, 'split-doc-sha', EXTRACTOR_VERSION))
    assert len(cached['contracts']) == 1
    c = cached['contracts'][0]
    assert [b['supplier_name'] for b in c['bids']] == ['Alpha Ltd.', 'Beta Inc.']
    assert [a['supplier_name'] for a in c['awards']] == ['Alpha Ltd.']
    assert c['_merge_conflicts'] == [
        {'kind': 'field', 'field': 'decision_date', 'kept': '2025-04-01', 'conflicting': '2025-04-15'}
    ]


@pytest.mark.parametrize(
    'status, payload, expected_text',
    [
        (
            400,
            {
                'type': 'error',
                'error': {
                    'type': 'invalid_request_error',
                    'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.',
                },
            },
            'credit balance is too low',
        ),
        (
            400,
            {
                'type': 'error',
                'error': {
                    'type': 'billing_error',
                    'message': 'Organization has reached its workspace spend limit.',
                },
            },
            'workspace spend limit',
        ),
        (
            402,
            {
                'type': 'error',
                'error': {
                    'type': 'billing_error',
                    'message': 'Payment required to continue API access.',
                },
            },
            'Payment required',
        ),
    ],
)
def test_credit_exhaustion_rejection_releases_reservations_preserves_queue_and_recovers(
    setup, conn, status, payload, expected_text
):
    queue, api, body = setup
    enqueue(queue, body, doc='credit-doc', text='credit test', source='pdf')
    api.post_status = status
    api.post_json = payload

    with pytest.raises(httpx.HTTPStatusError, match=expected_text):
        queue.submit()

    # Local spending reservation is released and submission records the billing rejection.
    assert charges(queue) == [(0, 'rejected')]
    sub = queue.status()['submissions'][0]
    assert sub['status'] == 'rejected' and expected_text in sub['error']

    # Jobs stay cleanly queued so no manual retry is required and re-queuing stays pending.
    job = queue.jobs()[0]
    assert job['status'] == 'queued' and job['submission'] is None and job['reservation'] is None and job['error'] is None
    assert queue.has_work()
    with pytest.raises(batch.ExtractionPending):
        queue.document('credit-doc', EXTRACTOR_VERSION, [body('credit test')], source='pdf')

    # After credits are restored, the next scheduled submit succeeds with a fresh custom_id and collects cleanly.
    first_custom = api.posts[0]['requests'][0]['custom_id']
    api.post_status = 200
    api.post_json = None
    submitted = queue.submit()
    assert submitted['submitted'] == 1
    assert api.posts[1]['requests'][0]['custom_id'] != first_custom
    collected = queue.collect(conn)
    assert collected['errors'] == [] and collected['cached'] == 1
    assert db.get_extraction(conn, 'credit-doc', EXTRACTOR_VERSION)
    assert charges(queue) == [(0, 'rejected'), (2000, 'reported')]


def test_credit_rejection_preserves_accepted_batches_and_recovers_on_next_refresh(setup, conn, tmp_path):
    queue, api, body = setup
    seed_buyers(conn)
    for sha, slug in [('pdf-1', 'r1'), ('pdf-2', 'r2')]:
        conn.execute(
            "INSERT INTO background_pdf (url,kind,sha256,text,reference) VALUES (?,'agency_board',?,'report','2026.EP1.1')",
            (f'https://toronto.ca/ep/{slug}.pdf', sha),
        )
    conn.commit()

    # Batch 1 is accepted and still in progress on Anthropic.
    enqueue(queue, body, doc='pdf-1', text='first report', source='pdf')
    first = queue.submit()
    api.ended = False
    first_custom = api.posts[0]['requests'][0]['custom_id']
    api.outcomes[first_custom] = {
        'type': 'succeeded',
        'message': {
            'stop_reason': 'end_turn',
            'content': [
                {
                    'type': 'text',
                    'text': json.dumps(
                        {
                            'contracts': [
                                {
                                    'reference': 'EP01-2026',
                                    'title': 'First Works',
                                    'bids': [{'supplier_name': 'Alpha Inc.', 'amount_raw': '$100'}],
                                    'awards': [{'supplier_name': 'Alpha Inc.', 'amount_raw': '$100'}],
                                }
                            ]
                        }
                    ),
                }
            ],
            'usage': {'input_tokens': 1000, 'output_tokens': 200},
        },
    }

    # Doc 2 is queued, then Anthropic rejects its submission due to exhausted credits.
    enqueue(queue, body, doc='pdf-2', text='second report', source='pdf')
    api.post_status = 400
    api.post_json = {
        'type': 'error',
        'error': {
            'type': 'invalid_request_error',
            'message': 'Your credit balance is too low to access the Anthropic API.',
        },
    }
    with pytest.raises(httpx.HTTPStatusError, match='credit balance is too low'):
        queue.submit()

    # Previously accepted batch 1 keeps its tracking state and reservation; doc 2 stays queued with released reservation.
    submissions = {s['remote_id']: s['status'] for s in queue.status()['submissions']}
    assert submissions[first['remote_id']] == 'submitted'
    assert submissions[None] == 'rejected'
    jobs_by_status = queue.status()['jobs']
    assert jobs_by_status == {'submitted': 1, 'queued': 1}
    assert charges(queue)[0][1] == 'reserved' and charges(queue)[1] == (0, 'rejected')

    # Batch 1 finishes while credits are still exhausted: hourly refresh publishes doc 1 and keeps doc 2 queued.
    api.ended = True
    handoff = tmp_path / 'handoff.json'
    exports = []
    during_outage = refresh(queue, conn, export=lambda: exports.append('outage'), manifest_path=handoff)
    assert len(during_outage['ready_documents']) == 1 and exports == ['outage'] and handoff.exists()
    assert any('credit balance is too low' in err for err in during_outage['errors'])
    assert queue.status()['jobs'] == {'ready': 1, 'queued': 1}
    assert charges(queue)[0] == (2000, 'reported') and charges(queue)[2] == (0, 'rejected')
    queue.acknowledge(during_outage['ready_documents'])
    handoff.unlink()

    # Credits are restored: next hourly refresh automatically submits doc 2, and the following refresh collects it.
    api.post_status = 200
    api.post_json = None
    exports.clear()
    resumed = refresh(queue, conn, export=lambda: exports.append('unexpected'), manifest_path=handoff)
    assert resumed['errors'] == [] and resumed['submission']['submitted'] == 1
    assert queue.status()['jobs'] == {'ready': 1, 'submitted': 1}
    second_custom = api.posts[-1]['requests'][0]['custom_id']
    api.outcomes[second_custom] = {
        'type': 'succeeded',
        'message': {
            'stop_reason': 'end_turn',
            'content': [
                {
                    'type': 'text',
                    'text': json.dumps(
                        {
                            'contracts': [
                                {
                                    'reference': 'EP02-2026',
                                    'title': 'Second Works',
                                    'bids': [{'supplier_name': 'Beta Ltd.', 'amount_raw': '$200'}],
                                    'awards': [{'supplier_name': 'Beta Ltd.', 'amount_raw': '$200'}],
                                }
                            ]
                        }
                    ),
                }
            ],
            'usage': {'input_tokens': 1000, 'output_tokens': 200},
        },
    }

    finished = refresh(queue, conn, export=lambda: exports.append('recovered'), manifest_path=handoff)
    assert finished['errors'] == [] and len(finished['ready_documents']) == 1 and exports == ['recovered']
    assert conn.execute('SELECT COUNT(*) FROM agency_bid').fetchone()[0] == 2


@pytest.mark.parametrize(
    'status, payload',
    [
        (400, {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'max_tokens: must be positive'}}),
        (401, {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}}),
        (403, {'type': 'error', 'error': {'type': 'permission_error', 'message': 'Your API key does not have permission'}}),
        (429, {'type': 'error', 'error': {'type': 'rate_limit_error', 'message': 'Rate limit exceeded'}}),
    ],
)
def test_non_credit_permanent_rejections_still_mark_jobs_failed(setup, status, payload):
    queue, api, body = setup
    enqueue(queue, body, doc='perm-fail', text='permanent failure')
    api.post_status = status
    api.post_json = payload

    with pytest.raises(httpx.HTTPStatusError):
        queue.submit()

    assert charges(queue) == [(0, 'rejected')]
    assert queue.status()['submissions'][0]['status'] == 'rejected'
    assert queue.jobs()[0]['status'] == 'failed'
    assert queue.submit()['submitted'] == 0


def test_uncertain_5xx_keeps_reservation_and_submitting_state_even_if_body_mentions_credits(setup):
    queue, api, body = setup
    enqueue(queue, body, doc='uncertain-doc', text='uncertain')
    api.post_status = 500
    api.post_json = {
        'type': 'error',
        'error': {'type': 'api_error', 'message': 'Your credit balance is too low'},
    }

    with pytest.raises(httpx.HTTPStatusError):
        queue.submit()

    assert charges(queue)[0][1] == 'reserved'
    assert queue.status()['submissions'][0]['status'] == 'uncertain'
    assert queue.jobs()[0]['status'] == 'submitting'
    assert queue.submit()['submitted'] == 0
