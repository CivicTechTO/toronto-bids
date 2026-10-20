"""Source-confirmed training restrictions must not hide supported events (#242)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from toronto_bids.sources import ariba_attachments as aa, ariba_files

FIXTURES = Path(__file__).parent / 'fixtures' / 'ariba'
PUBLIC_SECTOR = (FIXTURES / 'training_public_sector_disabled.txt').read_text()
CLOSED = (FIXTURES / 'training_closed.txt').read_text()
DENIED = 'You do not have the correct permission to view the event or the event link you clicked may be invalid.'


class Control:
    def __init__(self, visible=False):
        self.visible = visible
        self.first = self

    def count(self):
        return int(self.visible)

    def is_visible(self):
        return self.visible

    def is_disabled(self):
        return False

    def click(self):
        pass


class Page:
    def __init__(self, body='', supported=False):
        self.body, self.supported, self.elapsed = body, supported, 0

    def inner_text(self, selector):
        assert selector == 'body'
        return self.body

    def get_by_role(self, role, name, **kwargs):
        return Control((self.supported and name == 'Download Content') or
                       (self.body == 'Register/Login' and name == 'Register/Login'))

    def wait_for_timeout(self, ms):
        self.elapsed += ms


@pytest.mark.parametrize('body,outcome', [(PUBLIC_SECTOR, 'public_sector_disabled'),
                                         (CLOSED, 'closed'), (DENIED, 'denied')])
def test_post_respond_distinguishes_explicit_source_rejections(body, outcome):
    page = Page(body.replace(' ', '\u00a0'))
    assert aa._wait_post_respond(page, Control(), timeout_ms=3000) == outcome
    assert page.elapsed == 0


@pytest.mark.parametrize('body', ['Mock training event closes in 2099',
                                 'This event is no longer accepting responses: unknown server error',
                                 'Event is closed'])
def test_title_deadline_or_unscoped_text_cannot_prove_a_supported_restriction(body):
    assert aa._wait_post_respond(Page(body), Control(), timeout_ms=2000) == 'timeout'


def test_a_supported_event_reaches_capture_even_with_training_metadata():
    page = Page('Mock training event, 2099', supported=True)
    assert aa._wait_post_respond(page, Control(True), timeout_ms=2000) == 'event'


class Source:
    def __init__(self, fail=()):
        self.fail = fail

    def list_files(self):
        return [{'name': n, 'key': n} for n in ('a.pdf', 'b.pdf', 'c.pdf')]

    def download(self, file, dest):
        if file['name'] in self.fail:
            raise RuntimeError('download failed')
        dest.write_bytes(file['name'].encode())


@pytest.fixture
def capture(conn, monkeypatch, tmp_path):
    import playwright.sync_api
    page = Page()
    browser = SimpleNamespace(new_context=lambda **k: browser, new_page=lambda: page,
                              close=lambda: None)
    class PW:
        chromium = SimpleNamespace(launch=lambda **k: browser)
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', PW)
    monkeypatch.setattr(aa.config, 'ARIBA_USERNAME', 'offline-user')
    monkeypatch.setattr(aa.config, 'ARIBA_PASSWORD', 'offline-password')
    monkeypatch.setattr(aa, 'login', lambda *a, **k: None)
    monkeypatch.setattr(aa, '_open_authed_preview', lambda *a, **k: True)
    monkeypatch.setattr(aa, '_preview_respond', lambda *a, **k: Control(True))
    monkeypatch.setattr(aa, '_dismiss_cookie_banner', lambda *a: None)
    monkeypatch.setattr(aa, 'AribaFileSource', lambda *a, **k: Source())
    def run(doc, rfx, body='', supported=False):
        page.body, page.supported = body, supported
        event = {'document_number': doc, 'rfx_id': rfx, 'closes': '2099-12-31',
                 'title': 'Mock Training for Suppliers'}
        monkeypatch.setattr(aa, 'open_solicitation_events', lambda c: [event])
        failures, notices = [], []
        count = aa.capture_attachments(conn, dest_dir=tmp_path, failures=failures,
                                       training_restrictions=notices)
        return count, failures, notices
    return run


@pytest.mark.parametrize('doc,rfx,body,reason', [
    ('3861692164', '15346406', PUBLIC_SECTOR, 'public_sector_disabled'),
    ('3861744322', '15346471', CLOSED, 'closed'),
    ('4044346425', '16516115', DENIED, 'denied'),
])
def test_verified_training_restrictions_are_noticed_without_false_capture(
        capture, conn, tmp_path, doc, rfx, body, reason):
    count, failures, notices = capture(doc, rfx, body)
    assert count == 0 and not failures
    assert len(notices) == 1 and notices[0]['document_number'] == doc
    assert notices[0]['reason'] == reason
    assert not (tmp_path / f'Doc{doc}.zip').exists()
    assert conn.execute('SELECT count(*) FROM ariba_attachment').fetchone()[0] == 0


@pytest.mark.parametrize('doc,rfx', [('3949177873', '15889645'), ('3949149442', '15889504'),
                                    ('3861692164', '15346406')])
def test_supported_training_events_are_captured_including_a_previously_restricted_one(
        capture, conn, doc, rfx):
    count, failures, notices = capture(doc, rfx, supported=True)
    assert count == 1 and not failures and not notices
    assert conn.execute('SELECT count(*) FROM ariba_attachment').fetchone()[0] == 3


@pytest.mark.parametrize('doc,rfx,body', [
    ('5999999999', '1110009999', PUBLIC_SECTOR),
    ('5999999999', '1110009999', CLOSED),
    ('5999999999', '1110009999', DENIED),
    ('3861692164', '15346406', 'Unknown loading failure'),
    ('3861692164', '15346406', 'Register/Login'),
    ('3861692164', 'unexpected-posting', PUBLIC_SECTOR),
])
def test_real_procurement_unknown_errors_and_mismatched_identity_remain_failures(capture, doc, rfx, body):
    count, failures, notices = capture(doc, rfx, body)
    assert count == 0 and len(failures) == 1 and not notices
    assert failures[0][0] == f'ariba:Doc{doc}'


def test_a_training_restriction_never_hides_held_attachment_gaps(capture, conn, tmp_path):
    doc = '3861692164'
    bundle = ariba_files.capture_files(Source(fail=('b.pdf',)), doc, tmp_path)
    aa.store_bundle(conn, bundle, doc, tmp_path)
    gap = bundle.with_suffix('.omitted.json')
    before, evidence = bundle.read_bytes(), gap.read_bytes()
    rows = [tuple(r) for r in conn.execute('SELECT * FROM ariba_attachment')]
    count, failures, notices = capture(doc, '15346406', PUBLIC_SECTOR)
    assert count == 0 and failures and not notices
    assert 'b.pdf' in failures[0][1]
    assert bundle.read_bytes() == before and gap.read_bytes() == evidence
    assert [tuple(r) for r in conn.execute('SELECT * FROM ariba_attachment')] == rows
