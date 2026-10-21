"""Public WebLink capture: complete discovery, byte ownership, and retry semantics."""

import hashlib
import io
import json
import zipfile

import pytest

from toronto_bids import cli, config
from toronto_bids.sources import trca_history as history


def entry(entry_id, name, kind=-2, extension="pdf"):
    return dict(entryId=entry_id, name=name, type=kind, extension=extension)


def page(folder_id, rows, total=None):
    return {"data": dict(folderId=folder_id, results=rows,
                         totalEntries=len(rows) if total is None else total, failed=False)}


class Http:
    def __init__(self, folders=None, blobs=None):
        self.folders = folders or {}
        self.blobs = blobs or {}
        self.fetched = []

    def get_text(self, url):
        return '{"hasExportRights":true}'

    def post_json(self, url, json):
        assert url.endswith("FolderListingService.aspx/GetFolderListing2")
        response = self.folders[json["folderId"]]
        if isinstance(response, Exception):
            raise response
        return response

    def get_bytes(self, url):
        self.fetched.append(url)
        blob = self.blobs[url]
        if isinstance(blob, Exception):
            raise blob
        return blob


@pytest.fixture(autouse=True)
def no_wait(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    monkeypatch.setattr(config, "TRCA_HISTORY_DIR", tmp_path)


def test_capture_retries_missing_bytes_without_refetching_held(conn, tmp_path):
    pdf = b"%PDF-1.7\narchived bytes"
    folders = {
        history.MINUTE_BOOKS: page(history.MINUTE_BOOKS, [
            entry(10, '2018 TRCA Minute Book', 0), entry(20, '2019 TRCA Minute Book', 0)]),
        10: page(10, [entry(11, 'Minutes', 0), entry(12, 'Native attachment', extension='docx')]),
        11: page(11, [entry(13, 'A'), entry(14, 'B')]),
    }
    a, b = history.document_url(13), history.document_url(14)
    http = Http(folders, {a: pdf, b: b'<html>Not a PDF</html>'})
    result = history.capture_history(conn, http, log=lambda _: None)
    assert result['held'] == 1 and result['pending'] == 1 and result['failures']
    assert len(result['excluded_native_files']) == 1
    row = conn.execute('SELECT * FROM background_pdf WHERE url=?', (a,)).fetchone()
    assert row['kind'] == 'trca_history' and row['text'] is None
    assert row['sha256'] == hashlib.sha256(pdf).hexdigest()
    assert (tmp_path / (row['sha256'] + '.pdf')).read_bytes() == pdf
    # Held-but-unreadable PDFs are never refetched; no rediscovery on --resume.
    http = Http(blobs={b: pdf})
    result = history.capture_history(conn, http, discover=False, log=lambda _: None)
    assert result['held'] == 2 and not result['pending'] and not result['failures']
    assert http.fetched == [b]
    from toronto_bids.extraction import CORPORA
    assert conn.execute('SELECT COUNT(*) FROM background_pdf WHERE '+CORPORA['trca']).fetchone()[0] == 0


@pytest.mark.parametrize('reply', [
    {}, {'data': None}, {'data': {'failed': True, 'errMsg': 'not allowed'}},
    page(1, [], 2), page(1, [entry(2, 'a'), entry(2, 'duplicate')], 2),
    page(1, [], -1), page(1, [], True), page(99, []),
])
def test_malformed_or_truncated_folder_never_looks_complete(reply):
    with pytest.raises(ValueError):
        history.PublicArchive(Http({1: reply})).folder(1)


def test_pagination_uses_counts_and_stable_sort(monkeypatch):
    monkeypatch.setattr(history, 'PAGE_SIZE', 2)
    calls = []
    class Paged(Http):
        def post_json(self, url, json):
            calls.append(json)
            rows = [entry(i, str(i)) for i in range(2, 5)]
            return page(1, rows[json['start']:json['end']], 3)
    rows = history.PublicArchive(Paged()).folder(1)
    assert len(rows) == 3
    assert [c['start'] for c in calls] == [0, 2]
    assert [c['getNewListing'] for c in calls] == [True, False]
    assert all(c['sortColumn'] == 'Name' and c['sortAscending'] for c in calls)


def zipped(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for name, blob in files.items():
            z.writestr(name, blob)
    return output.getvalue()


class Export(Http):
    def __init__(self, blob, status=None):
        super().__init__()
        self.blob = blob
        self.status = status or {'finished': True}
        self.calls = []

    def post_json(self, url, json):
        self.calls.append((url, json))
        if url.endswith('StartExport'):
            return {'data': dict(token='12345678-1234-1234-1234-123456789abc', finished=False)}
        return {'data': self.status}

    def get_bytes(self, url):
        assert 'GetExportJob' in url
        assert self.calls[-1][0].endswith('CheckExportStatus')
        return self.blob


def test_scanned_document_uses_single_entry_export_and_checks_pdf():
    http = Export(zipped({'book/': b'', 'book/Minutes.pdf': b'%PDF-1.7\nscanned'}))
    assert history.PublicArchive(http).pdf(212735, '') == b'%PDF-1.7\nscanned'
    assert http.calls[0][1]['ids'] == [212735]


@pytest.mark.parametrize('blob', [b'<html>Failure</html>', zipped({'a.pdf': b'HTML'}),
                                   zipped({'a.pdf': b'%PDF-', 'b.pdf': b'%PDF-'}),
                                   zipped({'a.docx': b'PK'})])
def test_export_does_not_accept_error_pages_or_ambiguous_archives(blob):
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        history.PublicArchive(Export(blob)).pdf(2, '')


def test_discovery_failure_still_fetches_pending_queue(conn, tmp_path):
    url = history.document_url(13)
    conn.execute('INSERT INTO background_pdf (url,kind) VALUES (?,?)', (url, history.KIND))
    conn.commit()
    (tmp_path / 'inventory.json').write_text(json.dumps({'entries': [entry(13, 'old')], 'discovery_complete': True}))
    result = history.capture_history(conn, Http(
        {history.MINUTE_BOOKS: RuntimeError('calendar unavailable')}, {url: b'%PDF-1.7\nold'}),
        log=lambda _: None)
    assert result['newly_held'] == 1 and result['failures'][0][0] == 'discovery'


def test_cli_rejects_zero_limit():
    with pytest.raises(SystemExit):
        cli.main(['capture-trca-history', '--limit', '0'])


def test_incomplete_discovery_remains_failed_on_resume(conn, tmp_path):
    (tmp_path / 'inventory.json').write_text(json.dumps({
        'entries': [], 'discovery_complete': False, 'failures': [['discovery', 'lost page']]}))
    result = history.capture_history(conn, Http(), discover=False, log=lambda _: None)
    assert result['failures'] == [['discovery', 'lost page']]


def test_failed_rediscovery_retains_prior_metadata_for_pending_downloads(conn, tmp_path):
    url = history.document_url(13)
    conn.execute('INSERT INTO background_pdf (url,kind) VALUES (?,?)', (url, history.KIND))
    conn.commit()
    (tmp_path / 'inventory.json').write_text(json.dumps({
        'entries': [entry(13, 'previous walk')], 'discovery_complete': True}))
    # One year fails, while another is successfully walked. Older queue metadata survives.
    folders = {history.MINUTE_BOOKS: page(history.MINUTE_BOOKS, [
        entry(10, '2017 TRCA Minute Book', 0), entry(20, '2018 TRCA Minute Book', 0)]),
        10: RuntimeError('unavailable'), 20: page(20, [])}
    result = history.capture_history(conn, Http(folders, {url: b'%PDF-1.7\nold'}), log=lambda _: None)
    assert result['newly_held'] == 1 and result['failures']
    inventory = json.loads((tmp_path / 'inventory.json').read_text())
    assert inventory['entries'][0]['entryId'] == 13
    assert inventory['discovery_complete'] is False


@pytest.mark.parametrize('name,folder_id,count', [('series',1030915,66),('1957',212734,3),
                                                ('2018',1477012,10)])
def test_real_anonymous_folder_responses(name, folder_id, count):
    from pathlib import Path
    payload = json.loads((Path(__file__).parent / 'fixtures' / 'agencies' /
                          f'trca_laserfiche_{name}.json').read_text())
    rows = history.PublicArchive(Http({folder_id: payload})).folder(folder_id)
    assert len(rows) == count
    if name == 'series':
        assert sum(int(r['name'][:4]) < 2019 for r in rows) == 62


def test_anonymous_export_rights_are_required():
    http = Http()
    http.get_text = lambda _: '<html>Login required</html>'
    with pytest.raises(ValueError, match='no login attempted'):
        history.PublicArchive(http).open()


def test_export_errors_leave_document_unheld():
    with pytest.raises(ValueError, match='not available'):
        history.PublicArchive(Export(b'', {'errorMessage':'not available'})).pdf(2, '')


def test_export_wait_is_bounded():
    with pytest.raises(TimeoutError):
        history.PublicArchive(Export(b'')).pdf(2, '', timeout=0)


def test_changing_pagination_total_is_not_complete(monkeypatch):
    monkeypatch.setattr(history, 'PAGE_SIZE', 1)
    class Changed(Http):
        def post_json(self, url, json):
            return page(1, [entry(2+json['start'], 'record')], 2+json['start'])
    with pytest.raises(ValueError, match='changed during pagination'):
        history.PublicArchive(Changed()).folder(1)


def test_scanned_document_can_export_direct_pdf():
    assert history.PublicArchive(Export(b'%PDF-1.6\nscan')).pdf(2, '') == b'%PDF-1.6\nscan'


def test_pdf_shortcut_is_queued_and_uses_public_export(conn):
    folders = {history.MINUTE_BOOKS: page(history.MINUTE_BOOKS, [entry(10,'2018 TRCA Minute Book',0)]),
               10: page(10, [entry(12, 'Policy shortcut', -1)])}
    class Shortcut(Http):
        def post_json(self, url, json):
            if url.endswith('StartExport'):
                assert json['ids'] == [12]
                return {'data': {'errorMessage': 'Access denied. [9013]'}}
            return super().post_json(url, json)
    result = history.capture_history(conn, Shortcut(folders), log=lambda _: None)
    assert result['pending'] == 1 and result['held'] == 0
    assert 'Access denied' in result['failures'][0][1]
    assert result['failures'][0][0] == 'download'
