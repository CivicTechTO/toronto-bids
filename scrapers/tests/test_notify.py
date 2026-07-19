"""The nightly job's Slack summary.

`summarize` is pure, so the whole message is tested offline with no webhook and no network.
"""
from toronto_bids import notify

BEFORE = {"solicitation": 7641, "award": 14157, "bid": 18627, "supplier": 6738}
AFTER = {"solicitation": 7653, "award": 14165, "bid": 18632, "supplier": 6738}


def _counts(**kw):
    base = {k: 0 for k in ("solicitation", "award", "bid", "agency_award",
                           "agency_bid", "ariba_attachment")}
    base.update(kw)
    return base


def test_summary_shows_agency_and_attachment_growth():
    before = _counts(agency_award=100, agency_bid=200, ariba_attachment=1000)
    after = _counts(agency_award=107, agency_bid=215, ariba_attachment=1111)
    text = notify.summarize(before, after, [], 9, 31_000_000, 12.0)
    assert "agency awards 107 (+7)" in text
    assert "agency bids 215 (+15)" in text
    assert "ariba files 1,111 (+111)" in text


def test_summary_omits_zero_growth_agency_and_attachment_lines():
    c = _counts(solicitation=10)
    text = notify.summarize(c, c, [], 9, 1000, 1.0)
    assert "agency awards" not in text  # nothing captured -> no noise


def test_a_healthy_run_leads_with_success_and_the_counts():
    text = notify.summarize(BEFORE, AFTER, [], 9, 30_800_000, 192.0)
    assert text.startswith("✅ toronto-bids")
    assert "9/9 sources ok" in text
    assert "solicitations 7,653 (+12)" in text
    assert "awards 14,165 (+8)" in text
    assert "bids 18,632 (+5)" in text


def test_an_unchanged_count_shows_no_delta():
    """(+0) on every quiet table is noise; a delta appears only when something moved."""
    text = notify.summarize(BEFORE, AFTER, [], 9, 1, 1.0)
    assert "suppliers 6,738" in text
    assert "6,738 (+0)" not in text


def test_a_failure_leads_with_it_and_names_the_source_and_error():
    """The whole point of posting: a failure must be legible without opening a terminal."""
    text = notify.summarize(BEFORE, AFTER, [("ariba_discovery", "HTTPError 500")], 9,
                            30_800_000, 192.0)
    assert text.startswith("❌ toronto-bids")
    assert "1 failed" in text
    assert "ariba_discovery: HTTPError 500" in text


def test_a_failed_step_is_not_reported_as_a_failed_source():
    """`failures` carries whole-step failures (sync, award_summary, export) alongside the
    per-source ones pipeline.sync returns. 'N/9 sources FAILED' would call a dead disk a
    failed City feed."""
    text = notify.summarize(BEFORE, AFTER, [("export", "disk full")], 9, None, 5.0)
    assert "sources" not in text
    assert "export: disk full" in text


def test_a_failure_still_reports_the_export():
    """Export runs even after a partial sync — the message must say so, or a reader assumes
    the run produced nothing."""
    text = notify.summarize(BEFORE, AFTER, [("ariba_discovery", "boom")], 9, 30_800_000, 5.0)
    assert "export 29.4 MiB" in text


def test_a_missing_export_is_reported_as_missing_not_as_zero():
    text = notify.summarize(BEFORE, AFTER, [("export", "disk full")], 9, None, 5.0)
    assert "export FAILED" in text
    assert "0.0 MiB" not in text


def test_elapsed_is_human_readable():
    assert "3m12s" in notify.summarize(BEFORE, AFTER, [], 9, 1, 192.0)
    assert "45s" in notify.summarize(BEFORE, AFTER, [], 9, 1, 45.0)


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


def test_a_failed_before_count_shows_no_delta_rather_than_a_fabricated_one():
    """When the `before` count raised, the caller passes before={}. Rendering (+18,632) against
    a zero it never actually measured is a fabricated number in the exact mechanism the summary
    exists for. Show the absolute count, no delta."""
    text = notify.summarize({}, AFTER, [("counts", "locked")], 9, 30_800_000, 5.0)
    assert "bids 18,632" in text
    assert "(+" not in text


def test_a_malformed_webhook_exception_never_leaks_the_url_into_the_log(monkeypatch):
    """The exception branch must not undo what the status-code branch is careful about:
    httpx.InvalidURL embeds the request URL in its message, and log() goes to journald."""
    def raise_invalid(*a, **k):
        raise notify.httpx.InvalidURL("Invalid URL 'https://hooks.slack.test/SECRET-TOKEN'")
    monkeypatch.setattr(notify.httpx, "post", raise_invalid)
    said = []
    assert notify.post("hi", webhook="https://hooks.slack.test/SECRET-TOKEN", log=said.append) is False
    assert not any("SECRET-TOKEN" in m for m in said)
