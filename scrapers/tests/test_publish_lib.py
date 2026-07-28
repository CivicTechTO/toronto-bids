"""`deploy/publish-lib.sh` — durable step recording + artifact freshness for publish-data.sh
(#176, deferred from the comment on #173).

`record_step` gives publish-data.sh — bash, downstream of `tb nightly` in its own systemd
unit — the same `sync_run` recording every Python nightly step gets via `_run_step`.
`verify_artifact_size` answers the stronger question the issue's comment asked for: not "did
the upload command exit 0" (the R2 mirror kept exiting 0 while dead for 8 nights) but "is what's
actually live right now the same size as what we hold locally" — checked via a real HTTP HEAD
request against the artifact itself.

Bash-subprocess tests, the same shape #173 introduced for resolve-node.sh: these functions have
real side effects (a subprocess `tb` invocation, a real `curl`), so a fake PATH / local HTTP
server stands in rather than mocking bash itself.
"""
import http.server
import shutil
import sqlite3
import subprocess
import sys
import threading

import pytest

from tests.helpers_deploy import PUBLISH_LIB, SCRAPERS_DIR, run_publish_lib

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None or shutil.which("uv") is None,
    reason="publish-lib.sh needs bash, curl and uv",
)


def test_the_library_exists():
    assert PUBLISH_LIB.exists(), f"missing {PUBLISH_LIB}"


# --- a local HEAD server, so verify_artifact_size needs no real network -------------------

class _HeadServer:
    """Answers every request with a fixed Content-Length (or none at all) and nothing else —
    just enough for `curl -sIL` to read a header from a real socket."""

    def __init__(self, content_length):
        handler = _make_handler(content_length)
        self._httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/artifact"

    def close(self):
        self._httpd.shutdown()
        self._thread.join(timeout=5)


def _make_handler(content_length):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            if content_length is not None:
                self.send_header("Content-Length", str(content_length))
            self.end_headers()

        def do_GET(self):
            self.do_HEAD()

        def log_message(self, *a):
            pass
    return Handler


@pytest.fixture
def head_server():
    servers = []

    def _start(content_length):
        s = _HeadServer(content_length)
        servers.append(s)
        return s
    yield _start
    for s in servers:
        s.close()


# --- verify_artifact_size, record_step stubbed out to isolate the size comparison ---------

_STUB_RECORD_STEP = 'record_step() { echo "STUB record_step $*"; }\n'


def test_verify_artifact_size_matches(tmp_path, head_server):
    local = tmp_path / "local.bin"
    local.write_bytes(b"x" * 12345)
    server = head_server(12345)
    res = run_publish_lib(tmp_path, _STUB_RECORD_STEP + f'''
verify_artifact_size demo "{server.url}" "{local}"
echo "RC=$?"
''', env={"DRY_RUN": "0"})
    assert "publish-data: verified demo (12345 bytes" in res.stdout, res.stdout + res.stderr
    assert "STUB record_step demo ok" in res.stdout
    assert "RC=0" in res.stdout


def test_verify_artifact_size_mismatch_is_reported_and_returns_nonzero(tmp_path, head_server):
    """The exact case #173 needed: a size that doesn't match what's actually live, independent
    of whether the upload command that wrote it claimed success."""
    local = tmp_path / "local.bin"
    local.write_bytes(b"x" * 100)
    server = head_server(999)
    res = run_publish_lib(tmp_path, _STUB_RECORD_STEP + f'''
verify_artifact_size demo "{server.url}" "{local}"
echo "RC=$?"
''', env={"DRY_RUN": "0"})
    assert "size mismatch: local=100 remote=999" in res.stderr
    assert "STUB record_step demo failed" in res.stdout
    assert "RC=1" in res.stdout


def test_verify_artifact_size_with_no_content_length_is_a_failure_not_a_crash(tmp_path, head_server):
    """A server that answers but sends no Content-Length (a misconfigured proxy, an unexpected
    response shape) must not read as a silent pass."""
    local = tmp_path / "local.bin"
    local.write_bytes(b"x" * 10)
    server = head_server(None)
    res = run_publish_lib(tmp_path, _STUB_RECORD_STEP + f'''
verify_artifact_size demo "{server.url}" "{local}"
echo "RC=$?"
''', env={"DRY_RUN": "0"})
    assert "no Content-Length from" in res.stderr
    assert "STUB record_step demo failed" in res.stdout
    assert "RC=1" in res.stdout


def test_verify_artifact_size_follows_a_redirect_to_read_the_final_content_length(tmp_path, head_server):
    """A GitHub release asset 302s to a signed blob URL whose own response carries the real
    Content-Length; the redirect response's is 0. `curl -sIL` must land on the LAST one."""
    real = head_server(555)

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(302)
            self.send_header("Location", real.url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        local = tmp_path / "local.bin"
        local.write_bytes(b"x" * 555)
        res = run_publish_lib(tmp_path, _STUB_RECORD_STEP + f'''
verify_artifact_size demo "http://127.0.0.1:{httpd.server_address[1]}/redirect" "{local}"
echo "RC=$?"
''', env={"DRY_RUN": "0"})
        assert "RC=0" in res.stdout, res.stdout + res.stderr
        assert "STUB record_step demo ok" in res.stdout
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


# --- record_step, against the real `tb` CLI -----------------------------------------------

def _last_run(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT source, status, error FROM sync_run ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()


def test_record_step_ok_writes_a_real_sync_run_row(tmp_path):
    res = run_publish_lib(tmp_path, 'record_step publish ok\n', env={
        "DRY_RUN": "0", "UV": shutil.which("uv"), "SCRAPERS": str(SCRAPERS_DIR),
        "TB_DATA_DIR": str(tmp_path),
    })
    assert res.returncode == 0, res.stdout + res.stderr
    row = _last_run(tmp_path / "bids.sqlite")
    assert row["source"] == "publish"
    assert row["status"] == "ok"
    assert row["error"] is None


def test_record_step_failed_carries_the_error_through_to_sync_run(tmp_path):
    res = run_publish_lib(
        tmp_path, 'record_step r2_mirror failed "wrangler exit 1"\n', env={
            "DRY_RUN": "0", "UV": shutil.which("uv"), "SCRAPERS": str(SCRAPERS_DIR),
            "TB_DATA_DIR": str(tmp_path),
        })
    assert res.returncode == 0, res.stdout + res.stderr
    row = _last_run(tmp_path / "bids.sqlite")
    assert row["source"] == "r2_mirror"
    assert row["status"] == "failed"
    assert row["error"] == "wrangler exit 1"


def test_record_step_is_a_pure_echo_under_dry_run_and_writes_nothing(tmp_path):
    res = run_publish_lib(tmp_path, 'record_step publish ok\n', env={
        "DRY_RUN": "1", "UV": shutil.which("uv"), "SCRAPERS": str(SCRAPERS_DIR),
        "TB_DATA_DIR": str(tmp_path),
    })
    assert "DRY-RUN record-step publish ok" in res.stdout
    assert not (tmp_path / "bids.sqlite").exists()


def test_record_steps_own_failure_is_a_warning_not_a_crash(tmp_path):
    """If `uv`/`tb` itself can't run, record_step must warn and return — a monitoring
    convenience call must never be why the actual publish looks broken."""
    res = run_publish_lib(tmp_path, 'record_step publish ok\n', env={
        "DRY_RUN": "0", "UV": "/nonexistent/uv", "SCRAPERS": str(SCRAPERS_DIR),
        "TB_DATA_DIR": str(tmp_path),
    })
    assert res.returncode == 0
    assert "WARNING — could not record step 'publish'" in res.stderr
