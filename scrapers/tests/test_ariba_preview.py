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


class _TimedOutPage(_Page):
    """What a post-Respond timeout leaves behind: still on Discovery, a second tab opened."""
    url = "https://portal.us.bn.cloud.ariba.com/dashboard/appext/comsapsbncdiscoveryui#/RfxEvent/preview/1110026792"

    def __init__(self):
        super().__init__("inline")
        other = _Tab("https://service.ariba.com/Sourcing.aw/error", "Sourcing - Error")
        self.context = type("Ctx", (), {"pages": [self, other]})()

    def locator(self, selector):
        assert selector == "text=ID - " + EVENT["rfx_id"]
        visible = type("L", (), {"is_visible": lambda s: True})()
        return type("Ls", (), {"first": visible})()

    def title(self):
        return "SAP Business Network Discovery"

    def inner_text(self, selector, timeout=None):
        assert selector == "body"
        return "ID - 1110026792\n  Refrigeration   Upgrades\n" + "x" * 2000

    def content(self):
        return "<html>preview</html>"

    def screenshot(self, path, full_page=False, timeout=None):
        from pathlib import Path
        Path(path).write_bytes(b"png")


class _Tab:
    """A second tab Respond opened (e.g. a Sourcing popup)."""
    def __init__(self, url, title):
        self.url, self._title = url, title

    def title(self):
        return self._title

    def content(self):
        return "<html>sourcing</html>"

    def screenshot(self, path, full_page=False, timeout=None):
        from pathlib import Path
        Path(path).write_bytes(b"png")


def test_respond_timeout_records_what_followed(capture_seam, monkeypatch):
    """#304: a timeout after Respond says what the browser showed, and keeps the page on disk."""
    dest, _, calls = capture_seam
    monkeypatch.setattr(aa, "_wait_post_respond", lambda *a: "timeout")
    with pytest.raises(RuntimeError) as err:
        aa.capture_event(_TimedOutPage(), EVENT, dest)
    message = str(err.value)
    assert "the Sourcing event never loaded after Respond" in message
    assert "SAP Business Network Discovery" in message            # title
    assert "preview/1110026792" in message                         # current URL
    assert "Sourcing.aw/error" in message                          # the other tab
    assert "Sourcing - Error" in message                           # ...and its title
    assert "preview still visible" in message
    assert "ID - 1110026792 Refrigeration Upgrades" in message     # whitespace-collapsed excerpt
    assert len(message) < 1500                                     # bounded for Slack/sync_run
    saved = sorted(p.name.split("-", 2)[-1] if "tab" in p.name else p.suffix
                   for p in (dest / aa.RESPOND_TIMEOUT_DIRNAME).iterdir())
    assert saved == [".html", ".png", "tab1.html", "tab1.png"]   # both tabs kept
    assert calls == []


def test_respond_timeout_evidence_survives_a_broken_page(capture_seam, monkeypatch):
    """Evidence gathering is best-effort: a page that throws still yields the original failure."""
    dest, _, _ = capture_seam
    monkeypatch.setattr(aa, "_wait_post_respond", lambda *a: "timeout")
    with pytest.raises(RuntimeError, match="never loaded after Respond"):
        aa.capture_event(_Page("inline"), EVENT, dest)   # no url/title/content/screenshot


def test_overflow_action_does_not_bypass_permission_denial(capture_seam, monkeypatch):
    dest, _, calls = capture_seam
    monkeypatch.setattr(aa, "_wait_post_respond", lambda *a: "denied")
    page = _Page("overflow")
    with pytest.raises(aa.EventUnavailable, match="denied permission"):
        aa.capture_event(page, EVENT, dest)
    assert page.clicked == ["Additional Options", "Respond"]
    assert calls == []
