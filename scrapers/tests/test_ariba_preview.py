"""Preview action layouts exercised through the real capture_event entry point (#241)."""
import pytest

from toronto_bids.sources import ariba_attachments as aa

EVENT = {"rfx_id": "1110026792", "document_number": "5793328657"}


class _Control:
    def __init__(self, page, name):
        self.page, self.name = page, name

    def is_visible(self):
        ready = self.page.elapsed >= self.page.actions_ready_at
        if self.name == "Respond":
            return ready and (self.page.layout == "inline" or
                              (self.page.layout == "overflow" and self.page.menu_open))
        if self.name == "Additional Options":
            return ready and self.page.layout in ("overflow", "unrelated_menu")
        return False

    def is_enabled(self):
        return True

    def is_disabled(self):
        assert self.is_visible(), "Respond queried before the UI exposed it"
        return self.page.closed

    def click(self, **kwargs):
        assert self.is_visible()
        self.page.clicked.append(self.name)
        if self.name == "Additional Options":
            self.page.menu_open = not self.page.menu_open
        elif self.name == "Respond":
            self.page.responded = True


class _Page:
    def __init__(self, layout, closed=False, actions_ready_at=0):
        self.layout, self.closed = layout, closed
        self.actions_ready_at = actions_ready_at
        self.elapsed = 0
        self.menu_open = self.responded = False
        self.clicked = []

    def goto(self, *args, **kwargs):
        self.menu_open = False

    def wait_for_timeout(self, milliseconds):
        self.elapsed += milliseconds

    def query_selector(self, selector):
        # The identity marker exists even when action controls are not ready yet.
        assert selector == 'text=ID - ' + EVENT['rfx_id']
        return object()

    def get_by_role(self, role, name, exact=False):
        assert role == "button"
        assert exact is True or name == "Download Content"
        return _Control(self, name)


@pytest.fixture
def capture_seam(monkeypatch, tmp_path):
    calls = []
    bundle = tmp_path / "recovered.zip"
    monkeypatch.setattr(aa, "_dismiss_cookie_banner", lambda page: None)
    monkeypatch.setattr(aa, "AribaFileSource", lambda page, rfx_id, log: page)

    def wait_post_respond(page, control):
        assert page.responded
        return "event"

    def capture_files(page, document_number, dest_dir, log):
        calls.append(document_number)
        return bundle

    monkeypatch.setattr(aa, "_wait_post_respond", wait_post_respond)
    monkeypatch.setattr(aa.ariba_files, "capture_files", capture_files)
    return tmp_path, bundle, calls


@pytest.mark.parametrize("layout", ["inline", "overflow"])
def test_capture_reaches_documents_with_inline_or_overflow_respond(capture_seam, layout):
    dest, bundle, calls = capture_seam
    page = _Page(layout)
    assert aa.capture_event(page, EVENT, dest) == bundle
    assert page.clicked == (["Additional Options", "Respond"] if layout == "overflow" else ["Respond"])
    assert calls == [EVENT["document_number"]]


def test_preview_identity_does_not_imply_actions_are_ready(capture_seam):
    dest, bundle, calls = capture_seam
    page = _Page("overflow", actions_ready_at=6750)
    assert aa.capture_event(page, EVENT, dest) == bundle
    assert page.elapsed >= 6750
    assert page.clicked == ["Additional Options", "Respond"]


@pytest.mark.parametrize("layout", ["missing", "unrelated_menu"])
def test_absent_respond_is_actionable_failure_not_a_skip(capture_seam, layout):
    dest, _, calls = capture_seam
    page = _Page(layout)
    with pytest.raises(RuntimeError, match="1110026792.*Respond"):
        aa.capture_event(page, EVENT, dest)
    assert calls == []
    assert not page.responded
    # An unrelated menu is not repeatedly toggled in the hope a missing action appears.
    assert page.clicked == (["Additional Options"] if layout == "unrelated_menu" else [])


def test_disabled_overflow_respond_keeps_existing_closed_event_behavior(capture_seam):
    dest, _, calls = capture_seam
    page = _Page("overflow", closed=True)
    assert aa.capture_event(page, EVENT, dest) is None
    assert page.clicked == ["Additional Options"]
    assert calls == []


def test_overflow_action_does_not_bypass_permission_denial(capture_seam, monkeypatch):
    dest, _, calls = capture_seam
    monkeypatch.setattr(aa, "_wait_post_respond", lambda *a: "denied")
    page = _Page("overflow")
    assert aa.capture_event(page, EVENT, dest) is None
    assert page.clicked == ["Additional Options", "Respond"]
    assert calls == []
