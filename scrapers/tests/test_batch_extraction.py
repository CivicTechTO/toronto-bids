"""Lifecycle tests: durable requests, money boundaries and conservative imports."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from toronto_bids import batch_extraction as batch
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
        self.timeout_after_acceptance = False
        self.mutate_lines = lambda lines: lines

    def post(self, url, *, json, headers, timeout):
        self.posts.append(json)
        remote = 'msgbatch_' + str(len(self.posts))
        self.requests[remote] = json['requests']
        if self.timeout_after_acceptance:
            raise httpx.ReadTimeout('receipt lost')
        return httpx.Response(self.post_status, json={'id': remote}, request=httpx.Request('POST', url))

    def get(self, url, *, headers, timeout):
        if not url.endswith('/results'):
            remote = url.rsplit('/', 1)[-1]
            return httpx.Response(200, json={'id': remote, 'processing_status': 'ended' if self.ended else 'in_progress'}, request=httpx.Request('GET', url))
        remote = url.rsplit('/', 2)[-2]
        lines = []
        for i, request in enumerate(self.requests[remote]):
            result = self.outcomes.get(request['custom_id'])
            if result is None:
                result = {'type': 'succeeded', 'message': {
                    'model': request['params']['model'], 'stop_reason': 'end_turn',
                    'content': [{'type': 'text', 'text': json.dumps({'contracts': [
                        {'reference': str(i), 'bids': [{'supplier_name': 'Supplier', 'amount_raw': '$100'}]},
                    ]})}], 'usage': {'input_tokens': 1000, 'output_tokens': 200},
                }}
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


def enqueue(queue, body, doc='doc', text='one', version=EXTRACTOR_VERSION):
    with pytest.raises(batch.ExtractionPending):
        queue.document(doc, version, [body(text)])


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


@pytest.mark.parametrize('mutation', [
    lambda lines: lines + lines,
    lambda lines: [],
    lambda lines: [dict(lines[0], custom_id='foreign')],
])
def test_receipt_membership_must_be_exact_before_any_cache_or_settlement(setup, conn, mutation):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    reserved = charges(queue)
    api.mutate_lines = mutation
    assert queue.collect(conn)['errors']
    assert charges(queue) == reserved
    assert db.get_extraction(conn, 'doc', EXTRACTOR_VERSION) is None


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
    api.outcomes[custom] = {'type': 'succeeded', 'message': {
        'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '{"contracts": []}'}],
        'usage': {'input_tokens': 1000, 'output_tokens': 200}}}
    assert queue.collect(conn)['errors']
    assert charges(queue) == [(2000, 'reported')]
    assert db.get_extraction(conn, 'doc', EXTRACTOR_VERSION) is None


def test_missing_usage_keeps_conservative_reservation(setup, conn):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    reserved = charges(queue)
    custom = api.posts[0]['requests'][0]['custom_id']
    api.outcomes[custom] = {'type': 'succeeded', 'message': {
        'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '{"contracts": []}'}]}}
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
    assert queue.has_work()  # Billing must still be collected after manifests go stale.
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
    queue, api, body = setup
    from toronto_bids.extraction import extract_corpus
    class Client:
        def extract_document(self, sha, chunks):
            return queue.document(sha, EXTRACTOR_VERSION, [body(text) for text in chunks])
    for i in range(2):
        conn.execute("INSERT INTO background_pdf (url,kind,sha256,text) VALUES (?,'award_summary',?,?)",
                     (f'https://example.com/{i}', str(i), 'one paragraph\n\ntwo paragraph\n\nthree paragraph'))
    conn.commit()
    stats = extract_corpus(conn, 'award_summary', client=Client(), labels={}, max_chars=20, limit=1)
    assert stats['pending'] == 1 and stats['errors'] == 0
    assert len(queue.jobs()) == 3
    assert conn.execute('SELECT COUNT(*) FROM extraction_cache').fetchone()[0] == 0
    queue.submit()
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
    ops.execute("CREATE TRIGGER simulate_crash BEFORE UPDATE OF status ON batch_document WHEN NEW.status='cached' BEGIN SELECT RAISE(FAIL, 'crash after cache commit'); END")
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
    blocks = [dict(source_key='block', publication_month='2026-01', raw_text='Works - Supplier ($100)',
                   source_position=0, labels='[]')]
    monkeypatch.setattr(wf, 'parse_publication', lambda content: blocks)
    class Page:
        def get_bytes(self, url): return b'held source'
    class Immediate:
        def extract(self, text):
            return {'contracts': [{'title': 'Works', 'awards': [{'supplier_name': 'Supplier', 'amount_raw': '$100'}]}]}
    class Deferred:
        def extract(self, text):
            import hashlib
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


def test_invalid_receipt_is_retained_for_diagnosis(setup, conn):
    queue, api, body = setup
    enqueue(queue, body)
    queue.submit()
    api.mutate_lines = lambda lines: [dict(lines[0], custom_id='foreign')]
    assert queue.collect(conn)['errors']
    ops = queue._connect()
    try:
        receipt = ops.execute('SELECT receipt FROM batch_submission').fetchone()[0]
        assert json.loads(receipt)['custom_id'] == 'foreign'
    finally:
        ops.close()


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
    # Object equality hides reordering, which changes constrained generation.
    assert json.dumps(submitted, ensure_ascii=False) == json.dumps(original, ensure_ascii=False)
