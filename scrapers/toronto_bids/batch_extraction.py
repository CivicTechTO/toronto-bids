"""Durable asynchronous extraction, private to the scraper's operational store.

A document manifest groups every chunk before anything can enter the existing
cache. Job identity includes the complete request and prompt version. Submission
and billing state share one SQLite transaction; uncertain POSTs are never retried.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import uuid

import httpx

from toronto_bids.claude import ClaudeExtractionClient, _charge, api_headers, parse_response, request_allowance
from toronto_bids.extract import EXTRACTOR_VERSION, validate_extraction
from toronto_bids.llm_budget import BudgetExceeded, UsageBudget

_MODE = ContextVar('batch_extraction_mode', default=False)
_PENDING = ContextVar('batch_extraction_pending', default=0)
_API = 'https://api.anthropic.com/v1/messages/batches'
_MAX_BYTES = 64 * 1024 * 1024


class ExtractionPending(Exception):
    """Work is durably queued or submitted, and is not a completed extraction."""


def pending_events():
    return _PENDING.get()


def note_pending():
    _PENDING.set(_PENDING.get() + 1)


def batching_enabled():
    return _MODE.get()


@contextmanager
def nightly_batching(enabled=None):
    mode = os.environ.get('TB_CLAUDE_MODE', 'batch')
    if mode not in ('batch', 'sync'):
        raise ValueError('TB_CLAUDE_MODE must be batch or sync')
    token = _MODE.set(mode == 'batch' if enabled is None else enabled)
    pending = _PENDING.set(0)
    try:
        yield
    finally:
        _MODE.reset(token)
        _PENDING.reset(pending)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _request_json(body):
    # Schema property order affects constrained generation. Preserve the exact
    # qualified request through persistence; it is part of the job identity.
    return json.dumps(body, ensure_ascii=False, separators=(',', ':'))


def _job_key(version, body):
    return _sha(_json([version, _request_json(body)]))


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _remote_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'msgbatch_[A-Za-z0-9_-]{1,200}', value):
        raise ValueError('Invalid Claude message batch ID')
    return value


class BatchQueue:
    def __init__(self, path=None, *, budget=None, http=None, api_key=None):
        from toronto_bids.config import DATA_DIR
        self.budget = budget or UsageBudget(path or DATA_DIR / 'llm-usage.sqlite', os.environ.get('TB_LLM_MONTHLY_USD', '20'))
        self.path = self.budget.path
        self.http = http or httpx
        self.api_key = api_key

    def _connect(self):
        conn = self.budget._connect()
        conn.row_factory = sqlite3.Row
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS batch_job (
                id TEXT PRIMARY KEY, version TEXT NOT NULL, body TEXT NOT NULL,
                status TEXT NOT NULL, submission TEXT, reservation INTEGER,
                result TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS batch_document (
                id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, version TEXT NOT NULL,
                jobs TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting', source TEXT NOT NULL DEFAULT 'unspecified'
            );
            CREATE TABLE IF NOT EXISTS batch_submission (
                id TEXT PRIMARY KEY, remote_id TEXT UNIQUE, status TEXT NOT NULL,
                created_at TEXT NOT NULL, manifest TEXT NOT NULL, receipt TEXT, error TEXT
            );
        ''')
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if 'source' not in {r[1] for r in conn.execute('PRAGMA table_info(batch_document)')}:
                conn.execute("ALTER TABLE batch_document ADD COLUMN source TEXT NOT NULL DEFAULT 'unspecified'")
        return conn

    def has_work(self):
        if not self.path.exists():
            return False
        with sqlite3.connect(self.path) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='batch_job'").fetchone():
                return False
            return bool(conn.execute("SELECT 1 FROM batch_document WHERE status='waiting' LIMIT 1").fetchone()
                        or conn.execute("SELECT 1 FROM batch_job WHERE status IN ('submitting','submitted') LIMIT 1").fetchone())

    def status(self):
        if not self.path.exists():
            return {'jobs': {}, 'documents': {}, 'submissions': []}
        conn = self._connect()
        try:
            return {
                'jobs': dict(conn.execute('SELECT status,COUNT(*) FROM batch_job GROUP BY status').fetchall()),
                'documents': dict(conn.execute('SELECT status,COUNT(*) FROM batch_document GROUP BY status').fetchall()),
                'submissions': [dict(r) for r in conn.execute(
                    'SELECT id,remote_id,status,created_at,error FROM batch_submission ORDER BY created_at')],
            }
        finally:
            conn.close()

    def document(self, sha256, version, bodies, *, source='unspecified'):
        """Queue all chunks atomically, or return the complete validated result."""
        if not bodies:
            raise ValueError('A batch document must contain at least one chunk')
        keys = [_job_key(version, body) for body in bodies]
        manifest = _sha(_json([sha256, version, keys]))
        conn = self._connect()
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                for key, body in zip(keys, bodies):
                    conn.execute('INSERT OR IGNORE INTO batch_job (id,version,body,status) VALUES (?,?,?,\'queued\')',
                                 (key, version, _request_json(body)))
                conn.execute('INSERT OR IGNORE INTO batch_document (id,sha256,version,jobs,source) VALUES (?,?,?,?,?)',
                             (manifest, sha256, version, _json(keys), source))
            rows = [conn.execute('SELECT * FROM batch_job WHERE id=?', (key,)).fetchone() for key in keys]
            errors = [r['error'] for r in rows if r['status'] == 'failed']
            if errors:
                raise ValueError('Batch extraction failed; inspect extraction-batches status and retry: ' + '; '.join(errors))
            if all(r['status'] == 'ready' for r in rows):
                return self._merge(rows)
            _PENDING.set(_PENDING.get() + 1)
            raise ExtractionPending('Extraction queued/pending in Claude batch; prior archive facts retained')
        finally:
            conn.close()

    @staticmethod
    def _merge(rows):
        from toronto_bids.extraction import check_declared_counts, dedup_contracts
        results = [validate_extraction(json.loads(r['result'])) for r in rows]
        result = results[0] if len(results) == 1 else {
            'contracts': dedup_contracts([c for r in results for c in r['contracts']])}
        flags = check_declared_counts(result)
        if flags:
            result['_flags'] = flags
        return result

    def _get(self, suffix):
        response = self.http.get(_API + suffix, headers=api_headers(self.api_key), timeout=120)
        response.raise_for_status()
        return response

    def submit(self, *, limit=100):
        if not 1 <= limit <= 100:
            raise ValueError('Batch submission limit must be between 1 and 100')
        headers = api_headers(self.api_key)
        conn = self._connect()
        local_id = uuid.uuid4().hex
        requests, manifest, size = [], {}, 0
        budget_error = None
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute("UPDATE batch_job SET status='stale',error='Prompt version changed before submission' WHERE status='queued' AND version!=?", (EXTRACTOR_VERSION,))
                for row in conn.execute("SELECT * FROM batch_job WHERE status='queued' ORDER BY rowid LIMIT ?", (limit,)).fetchall():
                    body = json.loads(row['body'])
                    if _job_key(row['version'], body) != row['id']:
                        raise ValueError('Queued request hash mismatch')
                    if len(row['body'].encode()) > _MAX_BYTES:
                        raise ValueError('One queued request exceeds the 64 MiB submission limit')
                    if size + len(row['body'].encode()) > _MAX_BYTES:
                        break
                    try:
                        reservation = self.budget.reserve(body['model'], request_allowance(body, batch=True), conn=conn)
                    except BudgetExceeded as exc:
                        budget_error = str(exc)
                        break
                    custom_id = _sha(local_id + row['id'])
                    requests.append({'custom_id': custom_id, 'params': body})
                    manifest[custom_id] = {'job': row['id'], 'reservation': reservation}
                    size += len(row['body'].encode())
                    conn.execute("UPDATE batch_job SET status='submitting',submission=?,reservation=? WHERE id=?",
                                 (local_id, reservation, row['id']))
                if requests:
                    conn.execute('INSERT INTO batch_submission (id,status,created_at,manifest) VALUES (?,\'submitting\',?,?)',
                                 (local_id, datetime.now(timezone.utc).isoformat(), _json(manifest)))
            if not requests:
                if budget_error:
                    raise BudgetExceeded(budget_error)
                return {'submitted': 0}
            # One POST only. The durable 'submitting' record also covers a crash
            # after acceptance but before receipt persistence.
            try:
                response = self.http.post(_API, json={'requests': requests}, headers=headers, timeout=120)
                response.raise_for_status()
                remote = _remote_id(response.json().get('id'))
            except Exception as exc:
                rejected = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (400, 401, 402, 403, 404, 413, 422, 429)
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    conn.execute('UPDATE batch_submission SET status=?,error=? WHERE id=?',
                                 ('rejected' if rejected else 'uncertain', str(exc), local_id))
                    for item in manifest.values():
                        conn.execute('UPDATE batch_job SET status=?,error=? WHERE id=?',
                                     ('failed' if rejected else 'submitting', str(exc), item['job']))
                        if rejected:
                            self.budget.settle(item['reservation'], 0, 'rejected', conn=conn)
                raise
            with conn:
                conn.execute('UPDATE batch_submission SET remote_id=?,status=\'submitted\' WHERE id=?', (remote, local_id))
                conn.execute("UPDATE batch_job SET status='submitted' WHERE submission=?", (local_id,))
            return {'submitted': len(requests), 'local_id': local_id, 'remote_id': remote, 'budget_blocked': budget_error}
        finally:
            conn.close()

    def _receipt(self, remote, manifest, *, store=None):
        response = self._get('/' + _remote_id(remote) + '/results')
        text = response.text
        if store is not None:
            store(text)
        lines = [json.loads(line) for line in text.splitlines() if line.strip()]
        ids = [line.get('custom_id') for line in lines if isinstance(line, dict)]
        if len(ids) != len(lines) or len(ids) != len(set(ids)) or set(ids) != set(manifest):
            raise ValueError('Batch results contain missing, duplicate or foreign request IDs')
        return text, lines

    def _record(self, conn, item, line):
        row = conn.execute('SELECT * FROM batch_job WHERE id=?', (item['job'],)).fetchone()
        if row['status'] in ('ready', 'failed'):
            return
        result = line.get('result')
        if not isinstance(result, dict):
            conn.execute("UPDATE batch_job SET status='failed',error='Malformed batch result; billing unknown' WHERE id=?", (item['job'],))
            return
        kind = result.get('type')
        if kind in ('errored', 'expired', 'canceled'):
            self.budget.settle(item['reservation'], 0, 'rejected', conn=conn)
            conn.execute("UPDATE batch_job SET status='failed',error=? WHERE id=?",
                         (_json(result), item['job']))
            return
        if kind != 'succeeded' or not isinstance(result.get('message'), dict):
            conn.execute("UPDATE batch_job SET status='failed',error='Unknown batch result type; billing unknown' WHERE id=?", (item['job'],))
            return
        message = result['message']
        model = json.loads(row['body'])['model']
        try:
            if message.get('model', model) != model:
                raise ValueError('Batch response model differs from the reserved model')
            usage = message.get('usage')
            charge = (_charge(model, usage) + 1) // 2
            self.budget.settle(item['reservation'], charge, 'reported', usage, conn=conn)
            data = parse_response(message)
        except (ValueError, TypeError) as exc:
            # A success with invalid/missing usage keeps its reservation. An
            # invalid extraction with valid usage has already been charged.
            conn.execute("UPDATE batch_job SET status='failed',error=? WHERE id=?", (str(exc), item['job']))
        else:
            conn.execute("UPDATE batch_job SET status='ready',result=?,error=NULL WHERE id=?", (_json(data), item['job']))

    def collect(self, archive):
        """Poll once; import only complete documents, never wait for processing."""
        conn = self._connect()
        errors = []
        try:
            for batch in conn.execute("SELECT * FROM batch_submission WHERE status IN ('submitting','uncertain','submitted') ORDER BY created_at").fetchall():
                if not batch['remote_id']:
                    errors.append(f"Submission {batch['id']} has an uncertain receipt; use extraction-batches attach")
                    continue
                try:
                    remote = self._get('/' + _remote_id(batch['remote_id'])).json()
                    if not isinstance(remote, dict) or remote.get('id') != batch['remote_id']:
                        raise ValueError('Batch retrieval returned a different ID')
                    if remote.get('processing_status') in ('in_progress', 'canceling'):
                        continue
                    if remote.get('processing_status') != 'ended':
                        raise ValueError('Unknown batch processing status')
                    manifest = json.loads(batch['manifest'])
                    def store_receipt(text):
                        with conn:
                            conn.execute('UPDATE batch_submission SET receipt=? WHERE id=?', (text, batch['id']))
                    receipt, lines = self._receipt(batch['remote_id'], manifest, store=store_receipt)
                    with conn:
                        conn.execute('BEGIN IMMEDIATE')
                        for line in lines:
                            self._record(conn, manifest[line['custom_id']], line)
                        conn.execute("UPDATE batch_submission SET status='ended',error=NULL WHERE id=?", (batch['id'],))
                except (ValueError, TypeError, httpx.HTTPError) as exc:
                    errors.append(f"{batch['id']}: {exc}")
                    with conn:
                        conn.execute('UPDATE batch_submission SET error=? WHERE id=?', (str(exc), batch['id']))
            imported = self._import(conn, archive)
            failures = conn.execute("SELECT COUNT(*) FROM batch_job WHERE status='failed' AND version=?", (EXTRACTOR_VERSION,)).fetchone()[0]
            if failures:
                errors.append(f'{failures} batch request(s) failed; inspect extraction-batches status and retry')
            return {'cached': imported, 'errors': errors, 'queue': self.status()}
        finally:
            conn.close()

    def _import(self, conn, archive):
        imported = 0
        for doc in conn.execute("SELECT * FROM batch_document WHERE status='waiting'").fetchall():
            if doc['version'] != EXTRACTOR_VERSION:
                with conn:
                    conn.execute("UPDATE batch_document SET status='stale' WHERE id=?", (doc['id'],))
                continue
            jobs = [conn.execute('SELECT * FROM batch_job WHERE id=?', (key,)).fetchone() for key in json.loads(doc['jobs'])]
            if not all(row is not None and row['status'] == 'ready' for row in jobs):
                continue
            result = self._merge(jobs)
            # Two-store recovery is idempotent: a crash after this commit leaves
            # an existing cache entry, which a later import preserves verbatim.
            cursor = archive.execute('INSERT OR IGNORE INTO extraction_cache (sha256,extractor_version,result_json) VALUES (?,?,?)',
                                     (doc['sha256'], doc['version'], _json(result)))
            archive.commit()
            imported += cursor.rowcount
            with conn:
                conn.execute("UPDATE batch_document SET status='cached' WHERE id=?", (doc['id'],))
        return imported

    def attach(self, local_id, remote_id):
        """Recover a lost receipt only after the server proves every custom ID."""
        conn = self._connect()
        try:
            batch = conn.execute('SELECT * FROM batch_submission WHERE id=?', (local_id,)).fetchone()
            if batch is None or batch['remote_id'] or batch['status'] not in ('submitting', 'uncertain'):
                raise ValueError('Local submission is not awaiting receipt recovery')
            remote_id = _remote_id(remote_id)
            remote = self._get('/' + remote_id).json()
            if not isinstance(remote, dict) or remote.get('id') != remote_id or remote.get('processing_status') != 'ended':
                raise ValueError('Recovery requires an ended batch so its custom IDs can be verified')
            self._receipt(remote_id, json.loads(batch['manifest']))
            with conn:
                conn.execute("UPDATE batch_submission SET remote_id=?,status='submitted',error=NULL WHERE id=?", (remote_id, local_id))
                conn.execute("UPDATE batch_job SET status='submitted',error=NULL WHERE submission=?", (local_id,))
        finally:
            conn.close()

    def retry(self, job_id):
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute("UPDATE batch_job SET status='queued',submission=NULL,reservation=NULL,error=NULL WHERE id=? AND status='failed'", (job_id,))
                if cursor.rowcount != 1:
                    raise ValueError('Only a known failed job can be retried')
        finally:
            conn.close()

    def ready_documents(self):
        conn = self._connect()
        try:
            with conn:
                conn.execute("UPDATE batch_document SET status='stale' WHERE status='cached' AND version!=?", (EXTRACTOR_VERSION,))
            return [dict(r) for r in conn.execute(
                "SELECT id,sha256,source FROM batch_document WHERE status='cached' AND version=?", (EXTRACTOR_VERSION,))]
        finally:
            conn.close()

    def acknowledge(self, document_ids, *, status='applied'):
        if status not in ('applied', 'superseded'):
            raise ValueError('Invalid document completion status')
        conn = self._connect()
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                for doc_id in document_ids:
                    row = conn.execute('SELECT status,version FROM batch_document WHERE id=?', (doc_id,)).fetchone()
                    if row is None or row['version'] != EXTRACTOR_VERSION or row['status'] not in ('cached', status):
                        raise ValueError('Publication acknowledgement does not name ready documents')
                    conn.execute('UPDATE batch_document SET status=? WHERE id=?', (status, doc_id))
        finally:
            conn.close()

    def list_remote(self):
        return self._get('?limit=100').json()

    def jobs(self):
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute('SELECT id,version,status,submission,reservation,error FROM batch_job ORDER BY rowid')]
        finally:
            conn.close()


class BatchExtractionClient:
    def __init__(self, **kwargs):
        self.client = ClaudeExtractionClient(**kwargs)
        if len(self.client.models) != 1:
            raise ValueError('Batch extraction requires exactly one configured model')
        self.queue = BatchQueue(budget=self.client.budget, api_key=self.client.key)

    def extract_document(self, sha256, chunks, *, source='pdf'):
        return self.queue.document(sha256, EXTRACTOR_VERSION, [self.client.request_body(text) for text in chunks], source=source)

    def extract(self, text):
        return self.extract_document(_sha(text), [text], source='text')
