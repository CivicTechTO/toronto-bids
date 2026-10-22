"""#259: validated browser bytes and replacements never disguise original gaps."""
import hashlib
import json
from pathlib import Path

import pytest

from toronto_bids import cli, config
from toronto_bids.store import db

FIXTURES = Path(__file__).parent / 'fixtures/agencies'


def catalog():
    from toronto_bids.sources.trca_document_recovery import load_documents
    return {r['document_id']: r for r in load_documents()}


def test_real_pdfs_preserve_original_and_replacement_provenance(conn, tmp_path):
    from toronto_bids.sources.trca_document_recovery import recover_documents
    documents = [catalog()[20812], catalog()[20197]]
    original = (FIXTURES / 'trca_20812.pdf').read_bytes()
    replacement = (FIXTURES / 'trca_20636.pdf').read_bytes()
    calls = []
    def fetch(url):
        calls.append(url)
        body = original if url.endswith('20812') else replacement if url.endswith('20636') else b'<html><title>404 - File or directory not found.</title></html>'
        return dict(status=404 if url.endswith('20197') else 200, url=url,
                    content_type='text/html' if url.endswith('20197') else 'application/pdf', body=body)
    report = recover_documents(conn, fetch, documents=documents, reports_dir=tmp_path)
    assert report['originals_held'] == 1
    assert report['replacement_coverage'] == 1
    assert report['unresolved_originals'] == 1
    assert report['without_verified_replacement'] == 0
    rows = {r['url']: dict(r) for r in conn.execute('SELECT * FROM background_pdf')}
    assert rows[documents[0]['original_url']]['sha256'] == hashlib.sha256(original).hexdigest()
    assert rows[documents[1]['original_url']]['sha256'] is None
    assert rows[documents[1]['replacements'][0]['url']]['sha256'] == hashlib.sha256(replacement).hexdigest()
    assert 'June 16, 2026' in rows[documents[0]['original_url']]['text']
    first = [(r['id'], r['url'], r['sha256'], r['first_seen']) for r in conn.execute('SELECT * FROM background_pdf')]
    assert len(report['failures']) == 1
    assert documents[1]['original_url'] in report['failures'][0]
    assert json.loads((tmp_path / 'document-recovery.json').read_text()) == report
    calls.clear()
    recover_documents(conn, fetch, documents=documents, reports_dir=tmp_path)
    assert calls == [documents[1]['original_url']], 'held originals and replacements must not be refetched'
    assert [(r['id'], r['url'], r['sha256'], r['first_seen']) for r in conn.execute('SELECT * FROM background_pdf')] == first
    assert len(list((tmp_path / 'recovery_runs').glob('*.json'))) == 2


@pytest.mark.parametrize('body', [b'<html>Verifying your browser</html>',
                                 b'<html><body>Chrome PDF viewer</body></html>', b'%PDF-1.7\nbroken'])
def test_invalid_browser_responses_never_become_held_pdfs(conn, tmp_path, body):
    from toronto_bids.sources.trca_document_recovery import recover_documents
    document = catalog()[20812]
    result = recover_documents(conn, lambda url: dict(status=200, url=url, content_type='application/pdf', body=body),
                               documents=[document], reports_dir=tmp_path)
    assert result['originals_held'] == 0 and result['unresolved_originals'] == 1
    assert result['failures']
    assert conn.execute('SELECT sha256 FROM background_pdf').fetchone()[0] is None
    assert not list(tmp_path.glob('*.pdf'))


def test_standalone_exits_incomplete_and_does_not_extract(conn, tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager
    from toronto_bids.sources import trca_document_recovery as recovery
    @contextmanager
    def browser():
        yield lambda url: dict(status=404, url=url, content_type='text/html', body=b'not found')
    monkeypatch.setattr(recovery, 'browser_fetch', browser)
    monkeypatch.setattr(config, 'TRCA_REPORTS_DIR', tmp_path)
    monkeypatch.setattr(cli, '_open_db', lambda: conn)
    assert cli.main(['recover-trca-documents', '--only', '20812']) == 1
    assert '1 original' in capsys.readouterr().out


@pytest.mark.parametrize('target', [
    'https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId=21025',
    'https://pub-trca.escribemeetings.com/Login.aspx',
    'https://example.com/FileStream.ashx?DocumentId=20812',
])
def test_redirected_pdf_is_not_assigned_to_requested_original(conn, tmp_path, target):
    from toronto_bids.sources.trca_document_recovery import recover_documents
    body = (FIXTURES / 'trca_20812.pdf').read_bytes()
    result = recover_documents(conn, lambda url: dict(status=200, url=target, content_type='application/pdf', body=body),
                               documents=[catalog()[20812]], reports_dir=tmp_path)
    assert result['originals_held'] == 0
    assert result['documents'][0]['observation']['url'] == target
    assert 'redirected' in result['failures'][0]
    assert conn.execute('SELECT sha256 FROM background_pdf').fetchone()[0] is None


def test_changed_replacement_requires_review_and_leaves_original_queued(conn, tmp_path):
    from toronto_bids.sources.trca_document_recovery import recover_documents
    document = catalog()[20197]
    wrong_pdf = (FIXTURES / 'trca_20812.pdf').read_bytes()
    def fetch(url):
        return dict(status=404 if url == document['original_url'] else 200,
                    url=url, content_type='application/pdf', body=wrong_pdf)
    result = recover_documents(conn, fetch, documents=[document], reports_dir=tmp_path)
    assert result['replacement_coverage'] == 0
    assert result['without_verified_replacement'] == 1
    assert 'reviewed SHA-256' in result['documents'][0]['replacements'][0]['observation']['error']
    assert conn.execute('SELECT COUNT(*) FROM background_pdf WHERE sha256 IS NOT NULL').fetchone()[0] == 0
    assert not list(tmp_path.glob('*.pdf'))


def test_transport_failure_continues_and_pending_queue_still_reports_missing_original(conn, tmp_path):
    from toronto_bids.sources.trca_document_recovery import recover_documents
    from toronto_bids.sources.trca_board import _store_pending_pdfs
    documents = [catalog()[5558], catalog()[20812]]
    body = (FIXTURES / 'trca_20812.pdf').read_bytes()
    def fetch(url):
        if url == documents[0]['original_url']:
            raise TimeoutError('public retrieval timed out')
        return dict(status=200, url=url, content_type='application/pdf', body=body)
    result = recover_documents(conn, fetch, documents=documents, reports_dir=tmp_path)
    assert result['originals_held'] == 1 and result['unresolved_originals'] == 1
    assert 'timed out' in result['failures'][0]
    class Unavailable:
        def get_bytes(self, url):
            assert url == documents[0]['original_url'], 'held original must not be downloaded again'
            raise RuntimeError('HTTP 404; availability cause unverified')
    failures = []
    assert _store_pending_pdfs(conn, Unavailable(), tmp_path, '%escribemeetings.com/%',
                               lambda _: None, 'trca', failures=failures) == 0
    assert len(failures) == 1
    assert failures[0][0] == 'download:trca'
    assert documents[0]['original_url'] in failures[0][1]


def test_corrupt_held_pdf_is_reported_without_overwriting_index(conn, tmp_path):
    from toronto_bids.sources.trca_document_recovery import recover_documents
    document = catalog()[20812]
    body = (FIXTURES / 'trca_20812.pdf').read_bytes()
    def fetch(url):
        return dict(status=200, url=url, content_type='application/pdf', body=body)
    recover_documents(conn, fetch, documents=[document], reports_dir=tmp_path)
    before = dict(conn.execute('SELECT * FROM background_pdf').fetchone())
    Path(before['local_path']).write_bytes(b'<html>not the archived PDF</html>')
    def unexpected_fetch(url):
        pytest.fail('must expose archive corruption rather than silently replace held data')
    result = recover_documents(conn, unexpected_fetch, documents=[document], reports_dir=tmp_path)
    assert result['failures'] and result['originals_held'] == 0
    assert dict(conn.execute('SELECT * FROM background_pdf').fetchone()) == before
