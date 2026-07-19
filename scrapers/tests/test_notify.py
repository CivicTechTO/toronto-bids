"""The nightly job's Slack summary.

`summarize` is pure, so the whole message is tested offline with no webhook and no network.
"""
from toronto_bids import notify


def _counts(**kw):
    base = {k: 0 for k in ("solicitation", "award", "bid", "supplier",
                           "agency_award", "agency_bid", "ariba_attachment")}
    base.update(kw)
    return base


def test_clean_run_header_and_growth():
    report = {"ok": True, "before": _counts(solicitation=7434, bid=18605),
              "after": _counts(solicitation=7446, bid=18632),
              "failures": [], "export_bytes": 32_651_042, "elapsed_s": 3484.0}
    text = notify.summarize(report)
    assert text.startswith("*✅ toronto-bids nightly* · 58m04s")
    assert "*Growth*" in text
    assert "solicitations 7,446 (+12)" in text
    assert "bids 18,632 (+27)" in text
    assert "*Failures*" not in text


def test_failed_run_header_and_failures_section():
    report = {"ok": False, "before": _counts(), "after": _counts(),
              "failures": [("ariba_attachments", "TimeoutError on Respond")],
              "export_bytes": 1000, "elapsed_s": 61.0}
    text = notify.summarize(report)
    assert text.startswith("*❌ toronto-bids nightly*")
    assert "*Failures (1)*" in text
    assert "ariba_attachments: TimeoutError on Respond" in text


def test_steps_section_renders_status_detail_and_skip():
    report = {"ok": True, "before": _counts(), "after": _counts(), "failures": [],
              "steps": [
                  {"name": "sync", "status": "ok", "detail": "9/9 sources", "seconds": 192.0, "error": None},
                  {"name": "council", "status": "skip", "detail": "not the 1st", "seconds": 0.0, "error": None},
                  {"name": "ariba attachments", "status": "fail", "detail": "", "seconds": 2880.0, "error": "boom"},
              ],
              "export_bytes": 1000, "elapsed_s": 3600.0}
    text = notify.summarize(report)
    assert "*Steps*" in text
    assert "✅ sync  9/9 sources · 3m12s" in text
    assert "➖ council  not the 1st" in text
    assert "❌ ariba attachments  boom · 48m0" in text[:1000] or "❌ ariba attachments  boom · 48m00s" in text


def test_sources_section_flags_zero_fetch():
    report = {"ok": True, "before": _counts(), "after": _counts(), "failures": [],
              "sources": [
                  {"source": "odata_solicitations", "status": "ok", "rows_fetched": 7446, "rows_upserted": 12, "error": None},
                  {"source": "suspended_firms", "status": "ok", "rows_fetched": 0, "rows_upserted": 0, "error": None},
              ],
              "export_bytes": 1000, "elapsed_s": 60.0}
    text = notify.summarize(report)
    assert "*Sources* (fetched → new)" in text
    assert "odata_solicitations 7,446 → +12" in text
    assert "⚠ suspended_firms 0 fetched" in text


def test_nothing_moved_omits_growth_and_empty_before_suppresses_deltas():
    same = _counts(solicitation=10)
    assert "*Growth*" not in notify.summarize(
        {"ok": True, "before": same, "after": same, "failures": [], "export_bytes": 1, "elapsed_s": 1.0})
    # empty before => before-count failed => no fabricated deltas
    assert "*Growth*" not in notify.summarize(
        {"ok": True, "before": {}, "after": _counts(bid=5), "failures": [], "export_bytes": 1, "elapsed_s": 1.0})


def test_missing_export_is_reported_as_failed():
    report = {"ok": False, "before": _counts(), "after": _counts(), "failures": [],
              "export_bytes": None, "elapsed_s": 60.0}
    text = notify.summarize(report)
    assert "export FAILED" in text


def test_growth_labels_agency_and_ariba():
    before = _counts(agency_award=100, agency_bid=200, ariba_attachment=1000)
    after = _counts(agency_award=107, agency_bid=215, ariba_attachment=1111)
    text = notify.summarize({"ok": True, "before": before, "after": after,
                             "failures": [], "export_bytes": 1, "elapsed_s": 1.0})
    assert "agency awards 107 (+7)" in text
    assert "agency bids 215 (+15)" in text
    assert "ariba files 1,111 (+111)" in text


def test_post_without_a_webhook_is_a_no_op_and_makes_no_request(monkeypatch):
    """Absence of the credential degrades gracefully — this Mac and CI stay silent with no
    separate code path, the same way the rewrite design's goal 3 asks of auth-optional parts."""
    monkeypatch.delenv("TB_SLACK_WEBHOOK", raising=False)
    def boom(*a, **k):
        raise AssertionError("must not make a request without a webhook")
    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify.post("hello") is False


def test_post_sends_the_text_as_a_slack_payload(monkeypatch):
    class Resp:
        status_code = 200
    sent = {}
    def fake_post(url, **kw):
        sent.update(url=url, **kw)
        return Resp()
    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert notify.post("hello", webhook="https://hooks.slack.test/x") is True
    assert sent["url"] == "https://hooks.slack.test/x"
    assert sent["json"] == {"text": "hello"}


def test_post_reads_the_webhook_from_the_environment(monkeypatch):
    class Resp:
        status_code = 200
    monkeypatch.setenv("TB_SLACK_WEBHOOK", "https://hooks.slack.test/env")
    sent = {}
    def fake_post(url, **kw):
        sent.update(url=url)
        return Resp()
    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert notify.post("hello") is True
    assert sent["url"] == "https://hooks.slack.test/env"


def test_a_slack_failure_never_fails_the_run(monkeypatch):
    """The archive outranks the notification. A dead webhook must not turn a good sync into a
    failed unit."""
    def boom(*a, **k):
        raise OSError("slack is down")
    monkeypatch.setattr(notify.httpx, "post", boom)
    said = []
    assert notify.post("hello", webhook="https://hooks.slack.test/x", log=said.append) is False
    assert any("slack" in m.lower() for m in said)


def test_a_rejected_webhook_is_reported_rather_than_counted_as_sent(monkeypatch):
    """A revoked webhook returns 4xx without raising. Reporting that as success is how the
    channel goes quiet while the job believes it is still talking."""
    class Resp:
        status_code = 403
    monkeypatch.setattr(notify.httpx, "post", lambda url, **kw: Resp())
    said = []
    assert notify.post("hi", webhook="https://hooks.slack.test/x", log=said.append) is False
    assert any("403" in m for m in said)


def test_a_rejection_never_leaks_the_webhook_into_the_log(monkeypatch):
    """log() goes to journald. raise_for_status() would put the URL in the exception message
    and write the credential to the system log — which is why the status code is read by hand."""
    class Resp:
        status_code = 403
    monkeypatch.setattr(notify.httpx, "post", lambda url, **kw: Resp())
    said = []
    notify.post("hi", webhook="https://hooks.slack.test/SECRET-TOKEN", log=said.append)
    assert not any("SECRET-TOKEN" in m for m in said)


def test_a_successful_post_returns_true(monkeypatch):
    class Resp:
        status_code = 200
    monkeypatch.setattr(notify.httpx, "post", lambda url, **kw: Resp())
    assert notify.post("hi", webhook="https://hooks.slack.test/x") is True


def test_a_malformed_webhook_exception_never_leaks_the_url_into_the_log(monkeypatch):
    """The exception branch must not undo what the status-code branch is careful about:
    httpx.InvalidURL embeds the request URL in its message, and log() goes to journald."""
    def raise_invalid(*a, **k):
        raise notify.httpx.InvalidURL("Invalid URL 'https://hooks.slack.test/SECRET-TOKEN'")
    monkeypatch.setattr(notify.httpx, "post", raise_invalid)
    said = []
    assert notify.post("hi", webhook="https://hooks.slack.test/SECRET-TOKEN", log=said.append) is False
    assert not any("SECRET-TOKEN" in m for m in said)
