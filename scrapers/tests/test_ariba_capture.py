import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import playwright.sync_api
import pytest

from toronto_bids import config
from toronto_bids.sources import ariba_attachments as aa
from toronto_bids.sources import ariba_files

FIXTURES = Path(__file__).parent / "fixtures" / "ariba"
PUBLIC_SECTOR = (FIXTURES / "training_public_sector_disabled.txt").read_text()
CLOSED = (FIXTURES / "training_closed.txt").read_text()
DENIED = "You do not have the correct permission to view the event or the event link you clicked may be invalid."

PREVIEW_EVENT = {"rfx_id": "1110026792", "document_number": "5793328657"}
RETRY_DOC = "5909867893"
RETRY_EVENT = {"rfx_id": "1110027542", "document_number": RETRY_DOC}


class _FakeControl:
    def __init__(self, page=None, name="", visible=None):
        self.page = page
        self.name = name
        self._visible = visible
        self.first = self

    def count(self):
        return int(self.is_visible())

    def is_visible(self):
        if self._visible is not None:
            return self._visible
        ready = self.page.elapsed >= self.page.actions_ready_at
        if self.name == "Respond":
            return ready and (
                self.page.layout == "inline"
                or (self.page.layout == "overflow" and self.page.menu_open)
            )
        if self.name == "Additional Options":
            return ready and self.page.layout in ("overflow", "unrelated_menu")
        if self.name == "Download Content":
            return self.page.supported
        if self.name == "Register/Login":
            return self.page.body == "Register/Login"
        return False

    def is_enabled(self):
        return True

    def is_disabled(self):
        if self.page is None:
            return False
        if self.name == "Respond":
            assert self.is_visible(), "Respond queried before the UI exposed it"
        if self.page.disabled_reads > 0:
            self.page.disabled_reads -= 1
            return True
        return self.page.closed

    def click(self, **kwargs):
        if self.page is None:
            return
        assert self.is_visible()
        self.page.clicked.append(self.name)
        if self.name == "Additional Options":
            self.page.menu_open = not self.page.menu_open
        elif self.name == "Respond":
            self.page.responded = True


class _FakePage:
    def __init__(
        self,
        layout="inline",
        *,
        closed=False,
        disabled_reads=0,
        actions_ready_at=0,
        preview_loaded=True,
        body="",
        supported=False,
    ):
        self.layout = layout
        self.closed = closed
        self.disabled_reads = disabled_reads
        self.actions_ready_at = actions_ready_at
        self.preview_loaded = preview_loaded
        self.body = body
        self.supported = supported
        self.elapsed = 0
        self.menu_open = False
        self.responded = False
        self.clicked = []
        self.gotos = []

    def goto(self, url, *args, **kwargs):
        self.gotos.append(url)
        self.menu_open = False

    def wait_for_timeout(self, milliseconds):
        self.elapsed += milliseconds

    def query_selector(self, selector):
        assert selector.startswith("text=ID - ")
        return object() if self.preview_loaded else None

    def get_by_role(self, role, name, exact=False):
        assert role == "button"
        return _FakeControl(self, name)

    def inner_text(self, selector, timeout=None):
        assert selector == "body"
        return self.body


class _FakeTab:
    def __init__(self, url, title):
        self.url = url
        self._title = title

    def title(self):
        return self._title

    def content(self):
        return "<html>sourcing</html>"

    def screenshot(self, path, full_page=False, timeout=None):
        Path(path).write_bytes(b"png")


class _TimedOutPage(_FakePage):
    url = "https://portal.us.bn.cloud.ariba.com/dashboard/appext/comsapsbncdiscoveryui#/RfxEvent/preview/1110026792"

    def __init__(self):
        super().__init__(
            "inline",
            body="ID - 1110026792\n  Refrigeration   Upgrades\n" + "x" * 2000,
        )
        other = _FakeTab("https://service.ariba.com/Sourcing.aw/error", "Sourcing - Error")
        self.context = SimpleNamespace(pages=[self, other])

    def locator(self, selector):
        assert selector == "text=ID - " + PREVIEW_EVENT["rfx_id"]
        return SimpleNamespace(first=SimpleNamespace(is_visible=lambda: True))

    def title(self):
        return "SAP Business Network Discovery"

    def content(self):
        return "<html>preview</html>"

    def screenshot(self, path, full_page=False, timeout=None):
        Path(path).write_bytes(b"png")


class _FakeSource:
    def __init__(self, names=("a.pdf", "b.pdf", "c.pdf"), fail=()):
        self.names = names
        self.fail = set(fail)

    def list_files(self):
        return [{"name": n, "key": n} for n in self.names]

    def download(self, file, dest):
        if file["name"] in self.fail:
            raise RuntimeError("download unavailable")
        dest.write_bytes(file["name"].encode())


def _install_playwright(monkeypatch, page=None):
    target_page = page if page is not None else _FakePage()
    browser = SimpleNamespace(
        new_context=lambda **k: browser,
        new_page=lambda: target_page,
        close=lambda: None,
    )

    class _PW:
        chromium = SimpleNamespace(launch=lambda **k: browser)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", _PW)
    monkeypatch.setattr(config, "ARIBA_USERNAME", "offline-user")
    monkeypatch.setattr(config, "ARIBA_PASSWORD", "offline-password")
    monkeypatch.setattr(aa, "login", lambda *a, **k: None)
    return target_page


@pytest.fixture
def preview_seam(monkeypatch, tmp_path):
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


@pytest.fixture
def training_capture(conn, monkeypatch, tmp_path):
    page = _install_playwright(monkeypatch)
    monkeypatch.setattr(aa, "_open_authed_preview", lambda *a, **k: True)
    monkeypatch.setattr(aa, "_preview_respond", lambda *a, **k: _FakeControl(visible=True))
    monkeypatch.setattr(aa, "_dismiss_cookie_banner", lambda *a: None)
    monkeypatch.setattr(aa, "AribaFileSource", lambda *a, **k: _FakeSource())

    def run(doc, rfx, body="", supported=False):
        page.body, page.supported = body, supported
        event = {
            "document_number": doc,
            "rfx_id": rfx,
            "closes": "2099-12-31",
            "title": "Mock Training for Suppliers",
        }
        monkeypatch.setattr(aa, "open_solicitation_events", lambda c: [event])
        failures, notices = [], []
        count = aa.capture_attachments(
            conn, dest_dir=tmp_path, failures=failures, training_restrictions=notices
        )
        return count, failures, notices

    return run


@pytest.fixture
def retry_harness(conn, tmp_path, monkeypatch):
    _install_playwright(monkeypatch)
    monkeypatch.setattr(aa, "open_solicitation_events", lambda c: [RETRY_EVENT])
    seen = []

    def run(action):
        def capture(page, event, dest_dir, log):
            seen.append(event["document_number"])
            return action(log)

        monkeypatch.setattr(aa, "capture_event", capture)
        failures = []
        count = aa.capture_attachments(conn, dest_dir=tmp_path, failures=failures)
        return count, failures

    def seed(fail=("b.pdf",)):
        path = ariba_files.capture_files(_FakeSource(fail=fail), RETRY_DOC, tmp_path)
        aa.store_bundle(conn, path, RETRY_DOC, tmp_path)
        return path

    return run, seed, seen


# --- Credentials, event isolation, targeting, and session recovery ---


def test_capture_attachments_fails_fast_with_no_credentials(conn, monkeypatch):
    monkeypatch.setattr(config, "ARIBA_USERNAME", None)
    monkeypatch.setattr(config, "ARIBA_PASSWORD", None)
    with pytest.raises(RuntimeError, match=r"unset.*scrapers/\.env"):
        aa.capture_attachments(conn)


def test_capture_attachments_isolates_failures_and_supports_only_filter(conn, monkeypatch, tmp_path):
    _install_playwright(monkeypatch)
    monkeypatch.setattr(
        aa,
        "open_solicitation_events",
        lambda c: [
            {"rfx_id": "1", "document_number": "1111111111"},
            {"rfx_id": "2", "document_number": "2222222222"},
        ],
    )
    seen = []

    def fake_capture_event(page, event, dest_dir, log=lambda _m: None):
        seen.append(event["document_number"])
        if event["document_number"] == "1111111111":
            raise RuntimeError("content tree never resolved")
        return None

    monkeypatch.setattr(aa, "capture_event", fake_capture_event)
    failures = []
    assert aa.capture_attachments(conn, dest_dir=tmp_path, failures=failures) == 0
    assert seen == ["1111111111", "2222222222"]
    assert failures == [("ariba:Doc1111111111", "content tree never resolved")]
    assert aa.capture_attachments(conn, dest_dir=tmp_path) == 0

    seen.clear()
    assert aa.capture_attachments(conn, dest_dir=tmp_path, only=["2222222222"]) == 0
    assert seen == ["2222222222"]
    with pytest.raises(ValueError, match="9999999999"):
        aa.capture_attachments(conn, dest_dir=tmp_path, only=["9999999999"])
    assert seen == ["2222222222"]


def test_denied_event_gets_one_fresh_login_and_reuses_recovered_session(monkeypatch, tmp_path):
    closed = []
    old = SimpleNamespace(context=SimpleNamespace(close=lambda: closed.append("old")))
    fresh = SimpleNamespace(context=SimpleNamespace(close=lambda: closed.append("fresh")))
    browser = SimpleNamespace(
        new_context=lambda **kw: SimpleNamespace(new_page=lambda: fresh, close=fresh.context.close)
    )
    calls, logins = [], []

    def capture(page, event, dest_dir, **kw):
        calls.append(page)
        if page is old:
            raise aa.EventUnavailable("denied", "denied")
        return "bundle"

    monkeypatch.setattr(aa, "capture_event", capture)
    monkeypatch.setattr(aa, "login", lambda page, *a, **kw: logins.append(page))
    saved, page = aa._capture_with_session_recovery(
        browser, old, {"document_number": "1"}, tmp_path, lambda m: None
    )
    assert saved == "bundle" and page is fresh
    assert calls == [old, fresh] and logins == [fresh] and closed == ["old"]


def test_repeated_denial_is_bounded_and_closed_event_skips_reauth(monkeypatch, tmp_path):
    closed = []
    old, fresh = SimpleNamespace(), SimpleNamespace()
    browser = SimpleNamespace(
        new_context=lambda **kw: SimpleNamespace(new_page=lambda: fresh, close=lambda: closed.append(True))
    )
    calls = []

    def always_denied(page, *a, **kw):
        calls.append(page)
        raise aa.EventUnavailable("denied", "still denied")

    monkeypatch.setattr(aa, "capture_event", always_denied)
    monkeypatch.setattr(aa, "login", lambda *a, **kw: None)
    with pytest.raises(aa.EventUnavailable, match="still denied"):
        aa._capture_with_session_recovery(browser, old, {"document_number": "1"}, tmp_path, lambda m: None)
    assert calls == [old, fresh] and closed == [True]

    monkeypatch.setattr(
        aa, "capture_event", lambda *a, **kw: (_ for _ in ()).throw(aa.EventUnavailable("closed", "closed"))
    )
    with pytest.raises(aa.EventUnavailable, match="closed"):
        aa._capture_with_session_recovery(None, None, {}, tmp_path, lambda m: None)


# --- Preview layout, Respond readiness, closed-mid-capture salvage, and timeout evidence ---


@pytest.mark.parametrize("layout", ["inline", "overflow"])
def test_capture_reaches_documents_with_inline_or_overflow_respond(preview_seam, layout):
    dest, bundle, calls = preview_seam
    page = _FakePage(layout)
    assert aa.capture_event(page, PREVIEW_EVENT, dest) == bundle
    assert page.clicked == (["Additional Options", "Respond"] if layout == "overflow" else ["Respond"])
    assert calls == [PREVIEW_EVENT["document_number"]]


def test_preview_identity_does_not_imply_actions_are_ready(preview_seam):
    dest, bundle, _ = preview_seam
    page = _FakePage("overflow", actions_ready_at=6750)
    assert aa.capture_event(page, PREVIEW_EVENT, dest) == bundle
    assert page.elapsed >= 6750
    assert page.clicked == ["Additional Options", "Respond"]


@pytest.mark.parametrize("layout", ["missing", "unrelated_menu"])
def test_absent_respond_is_actionable_failure_not_a_skip(preview_seam, layout):
    dest, _, calls = preview_seam
    page = _FakePage(layout)
    with pytest.raises(RuntimeError, match="1110026792.*Respond"):
        aa.capture_event(page, PREVIEW_EVENT, dest)
    assert calls == []
    assert not page.responded
    assert page.clicked == (["Additional Options"] if layout == "unrelated_menu" else [])


def test_disabled_overflow_respond_without_partials_skips_event(preview_seam):
    dest, _, calls = preview_seam
    page = _FakePage("overflow", closed=True)
    assert aa.capture_event(page, PREVIEW_EVENT, dest) is None
    assert page.clicked == ["Additional Options"]
    assert calls == []


def test_capture_event_preview_retry_exhaustion_raises(preview_seam):
    dest, _, calls = preview_seam
    page = _FakePage("inline", preview_loaded=False)
    with pytest.raises(RuntimeError, match="authenticated event preview never loaded.*1110026792"):
        aa.capture_event(page, PREVIEW_EVENT, dest)
    assert len(page.gotos) == 3
    assert page.clicked == []
    assert calls == []


def test_capture_event_salvages_partials_when_respond_stably_disabled(preview_seam):
    dest, _, calls = preview_seam
    doc = PREVIEW_EVENT["document_number"]
    pdir = ariba_files.partial_dir(dest, doc)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    ariba_files.write_manifest(
        pdir,
        ariba_files.make_fingerprint([{"key": "a", "name": "a.pdf"}, {"key": "b", "name": "b.pdf"}]),
    )
    page = _FakePage("inline", closed=True)
    logs = []

    salvaged = aa.capture_event(page, PREVIEW_EVENT, dest, log=logs.append)
    assert salvaged == dest / f"Doc{doc}.zip"
    with zipfile.ZipFile(salvaged) as zf:
        assert zf.namelist() == ["a.pdf"]
    assert json.loads((dest / f"Doc{doc}.omitted.json").read_text())["omitted"] == ["b.pdf"]
    assert any("closed mid-capture" in m for m in logs)
    assert page.clicked == [] and calls == []


def test_capture_event_treats_transient_disabled_respond_as_open(preview_seam, monkeypatch):
    dest, bundle, calls = preview_seam
    doc = PREVIEW_EVENT["document_number"]
    pdir = ariba_files.partial_dir(dest, doc)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    partials_intact = []

    def capture_checking_partials(page, document_number, dest_dir, log):
        partials_intact.append((ariba_files.partial_dir(dest_dir, document_number) / "files" / "a.pdf").exists())
        calls.append(document_number)
        return bundle

    monkeypatch.setattr(aa.ariba_files, "capture_files", capture_checking_partials)
    page = _FakePage("inline", disabled_reads=1)
    logs = []

    assert aa.capture_event(page, PREVIEW_EVENT, dest, log=logs.append) == bundle
    assert page.clicked == ["Respond"]
    assert calls == [doc]
    assert partials_intact == [True]
    assert any("partials kept" in m for m in logs)


def test_respond_timeout_records_evidence_and_survives_broken_page(preview_seam, monkeypatch):
    dest, _, calls = preview_seam
    monkeypatch.setattr(aa, "_wait_post_respond", lambda *a: "timeout")
    with pytest.raises(RuntimeError) as err:
        aa.capture_event(_TimedOutPage(), PREVIEW_EVENT, dest)
    message = str(err.value)
    assert "the Sourcing event never loaded after Respond" in message
    assert "SAP Business Network Discovery" in message
    assert "preview/1110026792" in message
    assert "Sourcing.aw/error" in message
    assert "Sourcing - Error" in message
    assert "preview still visible" in message
    assert "ID - 1110026792 Refrigeration Upgrades" in message
    assert len(message) < 1500
    saved = sorted(
        p.name.split("-", 2)[-1] if "tab" in p.name else p.suffix
        for p in (dest / aa.RESPOND_TIMEOUT_DIRNAME).iterdir()
    )
    assert saved == [".html", ".png", "tab1.html", "tab1.png"]
    assert calls == []

    with pytest.raises(RuntimeError, match="never loaded after Respond"):
        aa.capture_event(_FakePage("inline"), PREVIEW_EVENT, dest)


def test_overflow_action_does_not_bypass_permission_denial(preview_seam, monkeypatch):
    dest, _, calls = preview_seam
    monkeypatch.setattr(aa, "_wait_post_respond", lambda *a: "denied")
    page = _FakePage("overflow")
    with pytest.raises(aa.EventUnavailable, match="denied permission"):
        aa.capture_event(page, PREVIEW_EVENT, dest)
    assert page.clicked == ["Additional Options", "Respond"]
    assert calls == []


# --- Post-Respond classification and training-event restrictions ---


@pytest.mark.parametrize(
    "body,outcome",
    [(PUBLIC_SECTOR, "public_sector_disabled"), (CLOSED, "closed"), (DENIED, "denied")],
)
def test_post_respond_distinguishes_explicit_source_rejections(body, outcome):
    page = _FakePage(body=body.replace(" ", "\u00a0"))
    assert aa._wait_post_respond(page, _FakeControl(visible=False), timeout_ms=3000) == outcome
    assert page.elapsed == 0


@pytest.mark.parametrize(
    "body",
    [
        "Mock training event closes in 2099",
        "This event is no longer accepting responses: unknown server error",
        "Event is closed",
    ],
)
def test_unscoped_text_cannot_prove_supported_restriction(body):
    assert aa._wait_post_respond(_FakePage(body=body), _FakeControl(visible=False), timeout_ms=2000) == "timeout"


def test_supported_event_reaches_capture_even_with_training_metadata():
    page = _FakePage(body="Mock training event, 2099", supported=True)
    assert aa._wait_post_respond(page, _FakeControl(visible=True), timeout_ms=2000) == "event"


@pytest.mark.parametrize(
    "doc,rfx,body,reason",
    [
        ("3861692164", "15346406", PUBLIC_SECTOR, "public_sector_disabled"),
        ("3861744322", "15346471", CLOSED, "closed"),
        ("4044346425", "16516115", DENIED, "denied"),
    ],
)
def test_verified_training_restrictions_are_noticed_without_false_capture(
    training_capture, conn, tmp_path, doc, rfx, body, reason
):
    count, failures, notices = training_capture(doc, rfx, body)
    assert count == 0 and not failures
    assert len(notices) == 1 and notices[0]["document_number"] == doc
    assert notices[0]["reason"] == reason
    assert not (tmp_path / f"Doc{doc}.zip").exists()
    assert conn.execute("SELECT count(*) FROM ariba_attachment").fetchone()[0] == 0


@pytest.mark.parametrize(
    "doc,rfx",
    [("3949177873", "15889645"), ("3949149442", "15889504"), ("3861692164", "15346406")],
)
def test_supported_training_events_are_captured(training_capture, conn, doc, rfx):
    count, failures, notices = training_capture(doc, rfx, supported=True)
    assert count == 1 and not failures and not notices
    assert conn.execute("SELECT count(*) FROM ariba_attachment").fetchone()[0] == 3


@pytest.mark.parametrize(
    "doc,rfx,body",
    [
        ("5999999999", "1110009999", PUBLIC_SECTOR),
        ("5999999999", "1110009999", CLOSED),
        ("5999999999", "1110009999", DENIED),
        ("3861692164", "15346406", "Unknown loading failure"),
        ("3861692164", "15346406", "Register/Login"),
        ("3861692164", "unexpected-posting", PUBLIC_SECTOR),
    ],
)
def test_real_procurement_and_mismatched_identity_remain_failures(training_capture, doc, rfx, body):
    count, failures, notices = training_capture(doc, rfx, body)
    assert count == 0 and len(failures) == 1 and not notices
    assert failures[0][0] == f"ariba:Doc{doc}"


def test_training_restriction_never_hides_held_attachment_gaps(training_capture, conn, tmp_path):
    doc = "3861692164"
    bundle = ariba_files.capture_files(_FakeSource(fail=("b.pdf",)), doc, tmp_path)
    aa.store_bundle(conn, bundle, doc, tmp_path)
    gap = bundle.with_suffix(".omitted.json")
    before, evidence = bundle.read_bytes(), gap.read_bytes()
    rows = [tuple(r) for r in conn.execute("SELECT * FROM ariba_attachment")]

    count, failures, notices = training_capture(doc, "15346406", PUBLIC_SECTOR)
    assert count == 0 and failures and not notices
    assert "b.pdf" in failures[0][1]
    assert bundle.read_bytes() == before and gap.read_bytes() == evidence
    assert [tuple(r) for r in conn.execute("SELECT * FROM ariba_attachment")] == rows


# --- Incomplete-bundle retry orchestration ---


def test_complete_archives_stay_skipped(retry_harness):
    run, seed, seen = retry_harness
    seed(fail=())
    assert run(lambda log: pytest.fail("complete bundle retried")) == (0, [])
    assert seen == []


def test_partial_retries_resolve_gaps_and_rebuild_index(retry_harness, conn, tmp_path):
    run, seed, seen = retry_harness
    path = seed()
    assert conn.execute("select count(*) from ariba_attachment").fetchone()[0] == 2
    count, failures = run(lambda log: ariba_files.capture_files(_FakeSource(), RETRY_DOC, tmp_path, log=log))
    assert count == 1 and not failures and seen == [RETRY_DOC]
    assert not path.with_suffix(".omitted.json").exists()
    assert conn.execute("select count(*) from ariba_attachment").fetchone()[0] == 3
    assert run(lambda log: pytest.fail("resolved bundle retried")) == (0, [])


@pytest.mark.parametrize("outcome", ["error", "closed", "denied", "poorer"])
def test_failed_or_poorer_retries_keep_archive_gap_and_index(retry_harness, conn, tmp_path, outcome):
    run, seed, seen = retry_harness
    path = seed()
    gap = path.with_suffix(".omitted.json")
    before, old_gap = path.read_bytes(), gap.read_bytes()

    def action(log):
        if outcome == "error":
            raise RuntimeError("Sourcing did not load")
        if outcome in ("closed", "denied"):
            log("Respond disabled (closed)" if outcome == "closed" else "no permission to view the event")
            return None
        return ariba_files.capture_files(_FakeSource(fail=("b.pdf", "c.pdf")), RETRY_DOC, tmp_path, log=log)

    count, failures = run(action)
    assert seen == [RETRY_DOC] and count == 0
    assert failures and failures[0][0] == "ariba:Doc" + RETRY_DOC
    assert path.read_bytes() == before and gap.read_bytes() == old_gap
    assert conn.execute("select count(*) from ariba_attachment").fetchone()[0] == 2


def test_corrupt_omission_evidence_is_not_silently_cleared(retry_harness, tmp_path):
    run, seed, seen = retry_harness
    path = seed()
    gap = path.with_suffix(".omitted.json")
    gap.write_text("invalid json")
    before = path.read_bytes()
    count, failures = run(lambda log: ariba_files.capture_files(_FakeSource(), RETRY_DOC, tmp_path, log=log))
    assert count == 0 and failures and seen == [RETRY_DOC]
    assert path.read_bytes() == before and gap.read_text() == "invalid json"


def test_first_partial_capture_and_improving_retries_report_remaining_gaps(retry_harness, conn, tmp_path):
    run, _, _ = retry_harness
    count, failures = run(
        lambda log: ariba_files.capture_files(_FakeSource(fail=("b.pdf",)), RETRY_DOC, tmp_path, log=log)
    )
    assert count == 1 and failures and "b.pdf" in failures[0][1]
    assert conn.execute("select count(*) from ariba_attachment").fetchone()[0] == 2

    path = tmp_path / f"Doc{RETRY_DOC}.zip"
    path.with_suffix(".omitted.json").write_text(json.dumps({"omitted": ["b.pdf", "d.pdf"]}))
    count, failures = run(
        lambda log: ariba_files.capture_files(
            _FakeSource(names=("a.pdf", "b.pdf", "c.pdf", "d.pdf"), fail=("d.pdf",)),
            RETRY_DOC,
            tmp_path,
            log=log,
        )
    )
    assert count == 1 and failures
    assert json.loads(path.with_suffix(".omitted.json").read_text())["omitted"] == ["d.pdf"]

    count, failures = run(
        lambda log: ariba_files.capture_files(
            _FakeSource(names=("a.pdf", "b.pdf", "c.pdf", "d.pdf")), RETRY_DOC, tmp_path, log=log
        )
    )
    assert count == 1 and not failures
    assert not path.with_suffix(".omitted.json").exists()


def test_collision_evidence_reported_in_capture_failures(retry_harness, tmp_path):
    run, seed, _ = retry_harness
    path = seed()
    path.with_suffix(".omitted.json").write_text(json.dumps({"omitted": [], "collided": 1}))
    count, failures = run(lambda log: ariba_files.capture_files(_FakeSource(), RETRY_DOC, tmp_path, log=log))
    assert count == 1 and failures and "1 collapsed link" in failures[0][1]


# --- Ariba request gate and .awmenu popup handling ---


class _DownloadPage:
    def __init__(self, stuck=False):
        self.busy = False
        self.stuck = stuck
        self.elapsed = 0
        self.opened = None
        self.download_name = None
        self.keyboard = SimpleNamespace(press=lambda key: None)

    def evaluate(self, script):
        assert "isRequestInProgress" in script
        return self.busy

    def wait_for_timeout(self, milliseconds):
        self.elapsed += milliseconds
        if not self.stuck and self.elapsed >= 1500:
            self.busy = False

    def expect_download(self, **kwargs):
        return _FakeDownload(self)


class _FakeDownload:
    def __init__(self, page):
        self.page = page
        self.value = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if args[0] is None and self.page.download_name is None:
            raise TimeoutError("no download started")

    def save_as(self, dest):
        Path(dest).write_text(self.page.download_name)
        self.page.download_name = None
        self.page.busy = True
        self.page.elapsed = 0


def _download_source(page):
    source = aa.AribaFileSource.__new__(aa.AribaFileSource)
    source.page = page
    source._require_event_view = lambda action: None
    source._await_menu_clear = lambda file: None
    source._close_popups = lambda: None

    def resolve(file):
        def click(**kwargs):
            if not page.busy:
                page.opened = file["name"]
                if file.get("kind") == ariba_files.MENU_ITEM:
                    page.download_name = file["name"]

        return SimpleNamespace(click=click)

    source._resolve_anchor = resolve
    source._ensure_clickable = lambda file, link: None

    def menu(file):
        if page.opened != file["name"]:
            raise RuntimeError("no VISIBLE Download this attachment")
        return SimpleNamespace(click=lambda: setattr(page, "download_name", file["name"]))

    source._await_menu_item = menu
    return source


@pytest.mark.parametrize("kind", [ariba_files.TREE_LINK, ariba_files.MENU_ITEM])
def test_next_document_waits_for_ariba_after_previous_bytes_saved(tmp_path, kind):
    page = _DownloadPage()
    source = _download_source(page)
    source.download({"name": "first.pdf", "kind": ariba_files.TREE_LINK}, tmp_path / "first")
    assert page.busy
    source.download({"name": "second.pdf", "kind": kind}, tmp_path / "second")
    assert (tmp_path / "first").read_text() == "first.pdf"
    assert (tmp_path / "second").read_text() == "second.pdf"


def test_stuck_request_refuses_to_click_next_document(tmp_path):
    page = _DownloadPage(stuck=True)
    page.busy = True
    with pytest.raises(RuntimeError, match="request.*still.*progress"):
        _download_source(page).download({"name": "next.pdf"}, tmp_path / "next")
    assert page.opened is None
    assert not (tmp_path / "next").exists()


class _PopupMenu:
    def __init__(self, page, name):
        self.page, self.name = page, name

    def get_attribute(self, name):
        assert name == "id"
        return self.name

    def wait_for(self, state, **kwargs):
        assert (self.page.active == self.name) == (state == "visible")


class _PopupCollection:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    @property
    def first(self):
        return self.items[0]

    def evaluate_all(self, script):
        assert "id" in script
        return [item.name for item in self.items]

    def click(self, **kwargs):
        assert len(self.items) == 1
        self.items[0].click(**kwargs)


class _PopupPage:
    def __init__(self):
        self.active = "second"
        self.clicked = []
        self.dismissals = 0
        self.owners = {"first": 1, "second": 1}

    def locator(self, selector):
        if selector == ".awmenu:visible":
            return _PopupCollection([_PopupMenu(self, self.active)] if self.active else [])
        if selector.startswith("[id="):
            return _PopupMenu(self, json.loads(selector[len("[id=") : -1]))
        prefix = 'a[bh="PML"][_mid='
        assert selector.startswith(prefix)
        name = json.loads(selector[len(prefix) : -1])

        def pointer_click(**kwargs):
            if self.active:
                raise RuntimeError("References popup intercepts pointer events")
            self.clicked.append(name)
            self.active = None if self.active == name else name

        return _PopupCollection([SimpleNamespace(click=pointer_click)] * self.owners[name])

    def evaluate(self, script):
        assert "hideActiveMenu" in script
        self.active = None
        self.dismissals += 1


class _PopupReference:
    def __init__(self, page, menu):
        self.page, self.menu = page, menu

    def is_visible(self):
        return self.page.active == self.menu

    def scroll_into_view_if_needed(self, **kwargs):
        assert self.is_visible()

    def locator(self, selector):
        assert "ancestor::" in selector and "awmenu" in selector
        return _PopupCollection([_PopupMenu(self.page, self.menu)])

    def wait_for(self, state, **kwargs):
        assert state == "visible" and self.is_visible()


def _popup_source(page):
    source = aa.AribaFileSource.__new__(aa.AribaFileSource)
    source.page = page
    source._toggled = set()
    source._expand_references = lambda: setattr(page, "active", "second")
    return source


def test_hidden_reference_reopens_its_own_menu():
    page = _PopupPage()
    source = _popup_source(page)
    for menu in ["first", "second", "first"]:
        source._ensure_clickable(
            {"name": menu + ".pdf", "kind": ariba_files.MENU_ITEM}, _PopupReference(page, menu)
        )
        assert page.active == menu
    assert page.clicked == ["first", "second", "first"]


def test_open_reference_popup_is_closed_via_widget():
    page = _PopupPage()
    _popup_source(page)._close_popups()
    assert page.active is None and page.clicked == [] and page.dismissals == 1


@pytest.mark.parametrize("owners", [0, 2])
def test_missing_or_ambiguous_menu_owner_refuses_to_guess(owners):
    page = _PopupPage()
    page.owners["first"] = owners
    with pytest.raises(RuntimeError, match="owner"):
        _popup_source(page)._ensure_clickable(
            {"name": "first.pdf", "kind": ariba_files.MENU_ITEM}, _PopupReference(page, "first")
        )
    assert page.clicked == []
