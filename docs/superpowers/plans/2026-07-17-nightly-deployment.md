# Nightly Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the archive on a nightly timer on `plexbox`, posting a compact summary to CivicTechTO Slack and a loud message when a source fails.

**Architecture:** A new `tb nightly` subcommand orchestrates `sync → enrich-awards --download → export → post summary`, with each step isolated so a failure never stops the steps behind it and the export runs even after a partial sync. A new `toronto_bids/notify.py` splits a **pure** `summarize()` from a thin `post()`, so the message is unit-tested offline with no webhook. Deployment is user-level systemd (`~/.config/systemd/user/`) because the box has no passwordless sudo and linger is already enabled.

**Tech Stack:** Python 3.12, `uv`, httpx (already a dependency), systemd 255 user units, Ubuntu 24.04.

**Spec:** [`docs/superpowers/specs/2026-07-17-deployment-design.md`](../specs/2026-07-17-deployment-design.md)

## Global Constraints

- **Use `uv` for all Python.** `uv add <package>`, never `uv pip install`. No version pins unless there is a concrete reason.
- **This repo is PUBLIC. No credential may ever be committed** — the Slack webhook lives only in `~/.config/toronto-bids/tb.env` (mode `0600`) on the server.
- **No new dependency is needed.** `httpx` is already in the project.
- **All tests are offline and fixture-based.** No test may make a network call.
- **`uv.lock` must be committed if dependencies change,** or CI (`uv sync --locked`) fails.
- **No lint/format/typecheck exists.** Don't invent those commands.
- **Nothing browser-bound goes on the scheduled path.** Do not install Playwright, the `council` extra, or Xvfb on the server.
- Run all commands from `scrapers/`.
- Target: `ssh.plexstone.stream` (host `plexbox`, user `alex`, keys already exchanged, no username needed).

---

### Task 1: `notify.py` — the pure summary and the thin poster

**Files:**
- Create: `scrapers/toronto_bids/notify.py`
- Test: `scrapers/tests/test_notify.py`

**Interfaces:**
- Consumes: `db.counts(conn) -> dict[str, int]` (existing); `pipeline.sync(...) -> list[tuple[str, str]]` of `(source_name, error)` (existing).
- Produces:
  - `summarize(before: dict[str, int], after: dict[str, int], failures: list[tuple[str, str]], n_sources: int, export_bytes: int | None, elapsed_s: float) -> str` — **pure, no I/O**.
  - `post(text: str, webhook: str | None = None, log=lambda _m: None) -> bool` — returns True if posted, False if no webhook or the post failed.

`summarize` takes `export_bytes: int | None`, **not a path** — stat'ing a file is I/O and would cost the purity that lets this be tested offline. The caller stats.

- [ ] **Step 1: Write the failing tests**

Create `scrapers/tests/test_notify.py`:

```python
"""The nightly job's Slack summary.

`summarize` is pure, so the whole message is tested offline with no webhook and no network.
"""
import pytest

from toronto_bids import notify

BEFORE = {"solicitation": 7641, "award": 14157, "bid": 18627, "supplier": 6738}
AFTER = {"solicitation": 7653, "award": 14165, "bid": 18632, "supplier": 6738}


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
    assert "export 29.4 MB" in text


def test_a_missing_export_is_reported_as_missing_not_as_zero():
    text = notify.summarize(BEFORE, AFTER, [("export", "disk full")], 9, None, 5.0)
    assert "export FAILED" in text
    assert "0.0 MB" not in text


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
    sent = {}
    monkeypatch.setattr(notify.httpx, "post",
                        lambda url, **kw: sent.update(url=url, **kw))
    assert notify.post("hello", webhook="https://hooks.slack.test/x") is True
    assert sent["url"] == "https://hooks.slack.test/x"
    assert sent["json"] == {"text": "hello"}


def test_post_reads_the_webhook_from_the_environment(monkeypatch):
    monkeypatch.setenv("TB_SLACK_WEBHOOK", "https://hooks.slack.test/env")
    sent = {}
    monkeypatch.setattr(notify.httpx, "post", lambda url, **kw: sent.update(url=url))
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_notify.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'toronto_bids.notify'`

- [ ] **Step 3: Write the implementation**

Create `scrapers/toronto_bids/notify.py`:

```python
"""Operational notification for the nightly job (deployment spec §3.4).

The v1 design dropped Slack notifications from scope ("these can wrap the CLI later if
wanted"); this is that wrapper, and it carries no data — only whether the run worked.

The split is deliberate: `summarize` is pure, so the entire message is tested offline against
fixture counts, and `post` is one HTTP call with nothing to get wrong.
"""
import os

import httpx

# Tables worth a line in a one-line summary. The full set is in `tb status`; this is the
# headline: what the archive is FOR (solicitations, awards, bids) plus the dimension the bids
# feed. Everything else is either derived or quiet.
_HEADLINE = (("solicitation", "solicitations"), ("award", "awards"),
             ("bid", "bids"), ("supplier", "suppliers"))
_SLACK_TIMEOUT = 15.0


def _count(before: dict, after: dict, key: str, label: str) -> str:
    """'solicitations 7,653 (+12)', or without the delta when nothing moved."""
    now = after.get(key, 0)
    delta = now - before.get(key, 0)
    return f"{label} {now:,}" + (f" ({delta:+,})" if delta else "")


def _elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def summarize(before: dict, after: dict, failures: list, n_sources: int,
              export_bytes: int | None, elapsed_s: float) -> str:
    """The one-line message. Pure — `export_bytes` is passed in, never stat'ed here.

    Posted on EVERY run, not only on failure, and that is the design: the failure mode a
    failures-only alert cannot catch is the timer never firing at all, where silence and health
    look identical. A nightly line makes silence itself the signal.
    """
    parts = [_count(before, after, key, label) for key, label in _HEADLINE]
    parts.append(f"export {export_bytes / 1_000_000:.1f} MB" if export_bytes is not None
                 else "export FAILED")
    parts.append(_elapsed(elapsed_s))
    if not failures:
        return f"✅ toronto-bids — {n_sources}/{n_sources} sources ok · " + " · ".join(parts)
    # NOT "N/{n_sources} sources FAILED": `failures` mixes per-source failures from
    # pipeline.sync with whole-step failures (sync, award_summary, export), and calling a
    # dead disk a failed City feed would send someone to the wrong system at 06:00.
    named = ", ".join(f"{name}: {error}" for name, error in failures)
    return f"❌ toronto-bids — {len(failures)} failed · {named} · " + " · ".join(parts)


def post(text: str, webhook: str | None = None, log=lambda _m: None) -> bool:
    """Post to Slack. Returns True if it went out.

    No webhook -> a silent no-op, so a dev machine and CI need no separate code path.
    A failed post is logged and swallowed: the archive outranks the notification, and a dead
    webhook must never turn a good sync into a failed unit.
    """
    webhook = webhook or os.environ.get("TB_SLACK_WEBHOOK")
    if not webhook:
        return False
    try:
        httpx.post(webhook, json={"text": text}, timeout=_SLACK_TIMEOUT)
        return True
    except Exception as exc:
        log(f"  slack post failed: {exc}")
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_notify.py -q`
Expected: PASS — 11 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS — 455 passed (444 existing + 11 new)

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/notify.py scrapers/tests/test_notify.py
git commit -m "feat(notify): a pure Slack summary and a thin poster for the nightly job"
```

---

### Task 2: `tb nightly` — the orchestration the timer calls

**Files:**
- Modify: `scrapers/toronto_bids/cli.py` (add `nightly` subparser in `build_parser`, add `_cmd_nightly`, register in `main`'s dispatch)
- Test: `scrapers/tests/test_nightly.py`

**Interfaces:**
- Consumes: `notify.summarize(before, after, failures, n_sources, export_bytes, elapsed_s) -> str` and `notify.post(text, webhook=None, log=...) -> bool` (Task 1); `pipeline.sync(conn, http) -> list[tuple[str, str]]`; `pipeline.default_sources() -> list`; `db.counts(conn) -> dict`; `export_json(conn, out_path) -> Path`; `award_summary.download_award_summaries(conn, http, dest_dir=None, log=...) -> int`; `award_summary.store_award_summary_bids(conn, log=...) -> int` (all existing).
- Produces: `_cmd_nightly(args) -> int` — 0 on a clean run, 1 if any step failed.

- [ ] **Step 1: Write the failing tests**

Create `scrapers/tests/test_nightly.py`:

```python
"""`tb nightly` — what the systemd timer calls (deployment spec §3.3).

Every step is isolated the way pipeline.run_source already isolates sources: one failure never
stops the steps behind it. These tests are offline — every network-touching call is patched.
"""
import pytest

from toronto_bids import cli, config, notify


@pytest.fixture
def nightly(conn, monkeypatch, tmp_path):
    """A `tb nightly` with the network removed and the data dir pointed at tmp."""
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [])
    monkeypatch.setattr(cli.HttpClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(cli.HttpClient, "close", lambda self: None)
    from toronto_bids.sources import award_summary
    monkeypatch.setattr(award_summary, "download_award_summaries", lambda *a, **k: 0)
    monkeypatch.setattr(award_summary, "store_award_summary_bids", lambda *a, **k: 0)
    monkeypatch.setattr(notify, "post", lambda *a, **k: False)
    return lambda: cli.main(["nightly"])


def test_a_clean_run_exits_zero_and_writes_the_export(nightly, tmp_path):
    assert nightly() == 0
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_failed_source_exits_non_zero_so_systemd_sees_it(nightly, monkeypatch):
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [("ariba_discovery", "boom")])
    assert nightly() == 1


def test_the_export_runs_even_after_a_partial_sync(nightly, monkeypatch, tmp_path):
    """Rows are committed per-source and never deleted, so partial data is still data.
    Skipping the export would discard a good artifact over one bad feed."""
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [("ariba_discovery", "boom")])
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_raising_sync_is_caught_and_the_run_continues(nightly, monkeypatch, tmp_path):
    """pipeline.sync catches per-source, but a failure in the pass machinery itself would
    otherwise take the export down with it."""
    def boom(*a, **k):
        raise RuntimeError("pipeline exploded")
    monkeypatch.setattr(cli.pipeline, "sync", boom)
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_raising_award_summary_step_does_not_stop_the_export(nightly, monkeypatch, tmp_path):
    from toronto_bids.sources import award_summary
    def boom(*a, **k):
        raise RuntimeError("portal down")
    monkeypatch.setattr(award_summary, "download_award_summaries", boom)
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_the_summary_is_posted(nightly, monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    assert nightly() == 0
    assert len(posted) == 1
    assert posted[0].startswith("✅ toronto-bids")


def test_a_failing_run_posts_the_failure(nightly, monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.append(text) or True)
    monkeypatch.setattr(cli.pipeline, "sync", lambda *a, **k: [("ariba_discovery", "boom")])
    assert nightly() == 1
    assert posted[0].startswith("❌ toronto-bids")
    assert "ariba_discovery" in posted[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_nightly.py -q`
Expected: FAIL — `argparse` exits 2 with "invalid choice: 'nightly'"

- [ ] **Step 3: Add the subparser**

In `scrapers/toronto_bids/cli.py`, in `build_parser()`, after the `p_export` block (around line 21):

```python
    sub.add_parser(
        "nightly",
        help="Sync, archive new Award Summary Forms, export, and post a summary to Slack. "
             "What the systemd timer runs — see docs/superpowers/specs/"
             "2026-07-17-deployment-design.md")
```

- [ ] **Step 4: Add the command**

In `scrapers/toronto_bids/cli.py`, add after `_cmd_export`:

```python
def _cmd_nightly(args) -> int:
    """The whole unattended run: sync -> award summaries -> export -> tell Slack.

    Each step is isolated exactly as pipeline.run_source isolates a source: a failure is
    recorded and the steps behind it still run. In particular the EXPORT RUNS EVEN AFTER A
    PARTIAL SYNC — rows are committed per-source and never deleted, so partial data is still
    data, and discarding a good artifact over one bad feed would be the worse outcome.

    Exits non-zero if anything failed, so systemd marks the unit failed and the next run's
    Slack line is not the only record.
    """
    import time
    from pathlib import Path

    from toronto_bids import notify
    from toronto_bids.sources.award_summary import (
        download_award_summaries, store_award_summary_bids)

    started = time.monotonic()
    out = lambda m: print(m, flush=True)
    conn = _open_db()
    before = db.counts(conn)
    failures: list[tuple[str, str]] = []

    http = HttpClient()
    try:
        try:
            failures.extend(pipeline.sync(conn, http))
        except Exception as exc:
            failures.append(("sync", str(exc)))
        try:
            download_award_summaries(conn, http, log=out)
            store_award_summary_bids(conn, log=out)
        except Exception as exc:
            failures.append(("award_summary", str(exc)))
    finally:
        http.close()

    export_bytes = None
    try:
        written = export_json(conn, Path(config.DATA_DIR) / "export" / "bids.json")
        export_bytes = written.stat().st_size
    except Exception as exc:
        failures.append(("export", str(exc)))

    after = db.counts(conn)
    conn.close()

    text = notify.summarize(before, after, failures, len(pipeline.default_sources()),
                            export_bytes, time.monotonic() - started)
    print(text)
    for name, error in failures:
        print(f"FAILED  {name}: {error}", file=sys.stderr)
    notify.post(text, log=lambda m: print(m, file=sys.stderr))
    return 1 if failures else 0
```

- [ ] **Step 5: Register it in the dispatch**

`main()` is an if-chain, not a mapping. In `scrapers/toronto_bids/cli.py`, add this immediately after the `export` branch and before the `enrich-council` branch:

```python
    if args.command == "nightly":
        return _cmd_nightly(args)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_nightly.py -q`
Expected: PASS — 7 passed

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS — 462 passed

- [ ] **Step 8: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_nightly.py
git commit -m "feat(cli): tb nightly — the unattended sync/enrich/export/notify run"
```

---

### Task 3: The systemd units and the deploy README

**Files:**
- Create: `deploy/tb-nightly.service`
- Create: `deploy/tb-nightly.timer`
- Create: `deploy/README.md`

**Interfaces:**
- Consumes: `tb nightly` (Task 2), `TB_DATA_DIR` (existing, read by `config.py`), `TB_SLACK_WEBHOOK` (Task 1).
- Produces: unit files installed to `~/.config/systemd/user/` in Task 6.

No tests — these are config. They are verified live in Task 6.

- [ ] **Step 1: Create the service unit**

Create `deploy/tb-nightly.service`:

```ini
[Unit]
Description=toronto-bids nightly sync, enrich, export, notify
Documentation=https://github.com/CivicTechTO/toronto-bids/blob/main/docs/superpowers/specs/2026-07-17-deployment-design.md
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=%h/toronto-bids/scrapers
# The default data dir is scrapers/files/ — inside the checkout. On a server the archive must
# live where a `git pull` cannot reach it.
Environment=TB_DATA_DIR=%h/tb-data
# The leading '-' makes this optional: no webhook file -> notify.post() no-ops and the run
# still succeeds. The file holds a credential and is mode 0600; it is never in git.
EnvironmentFile=-%h/.config/toronto-bids/tb.env
ExecStart=%h/.local/bin/uv run tb nightly
# A full sync measures ~3 minutes (sync_run, 2026-07-16). 30m is a generous ceiling.
TimeoutStartSec=30m
```

- [ ] **Step 2: Create the timer unit**

Create `deploy/tb-nightly.timer`:

```ini
[Unit]
Description=Run toronto-bids nightly
Documentation=https://github.com/CivicTechTO/toronto-bids/blob/main/docs/superpowers/specs/2026-07-17-deployment-design.md

[Timer]
# The box runs on UTC, so a bare `OnCalendar=daily` would fire at 20:00 Toronto. systemd >=252
# accepts a timezone here and owns the DST arithmetic; verified on the host with
# `systemd-analyze calendar`.
OnCalendar=*-*-* 05:30:00 America/Toronto
# Don't hit the City's endpoints at a round number.
RandomizedDelaySec=30m
# A home server is not assumed to be up: run a missed job on the next boot.
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create the deploy README**

Create `deploy/README.md`:

````markdown
# Deploying the nightly job

Design: [`docs/superpowers/specs/2026-07-17-deployment-design.md`](../docs/superpowers/specs/2026-07-17-deployment-design.md)

User-level systemd. The host has no passwordless sudo, and linger is already enabled for the
operator, so the only privileged step is installing two apt packages.

## 1. Prerequisites (the only step needing a password)

```shell
sudo apt install -y poppler-utils pipx
pipx install uv
```

`poppler-utils` is required: `download_pdf` shells out to `pdftotext`, and a missing binary
raises `FileNotFoundError` per form — `download_award_summaries` catches it, logs `skipped`,
never writes the row, and therefore **re-downloads the same form on every run, forever**.

`pipx` rather than uv's `curl … | sh` installer: distro packages over piping a remote script
into a shell.

## 2. Code

```shell
git clone https://github.com/CivicTechTO/toronto-bids.git ~/toronto-bids
cd ~/toronto-bids/scrapers && uv sync --locked
uv run tb --version
```

## 3. The Slack webhook

The repo is public. The webhook is a credential and lives only here, mode `0600`:

```shell
mkdir -p ~/.config/toronto-bids
read -rs W && printf 'TB_SLACK_WEBHOOK=%s\n' "$W" > ~/.config/toronto-bids/tb.env
chmod 600 ~/.config/toronto-bids/tb.env
```

Unset it and `tb nightly` still runs — it just posts nothing.

## 4. Units

```shell
mkdir -p ~/.config/systemd/user
cp ~/toronto-bids/deploy/tb-nightly.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
```

## 5. Prove it before scheduling it

```shell
systemctl --user start tb-nightly.service
journalctl --user -u tb-nightly -n 50 --no-pager
```

Then enable the timer:

```shell
systemctl --user enable --now tb-nightly.timer
systemctl --user list-timers tb-nightly.timer
```

## Operating

| what | command |
|---|---|
| last run | `journalctl --user -u tb-nightly -n 50 --no-pager` |
| next fire | `systemctl --user list-timers tb-nightly.timer` |
| run now | `systemctl --user start tb-nightly.service` |
| row counts / per-source status | `cd ~/toronto-bids/scrapers && TB_DATA_DIR=~/tb-data uv run tb status` |
| update | `cd ~/toronto-bids && git pull && cd scrapers && uv sync --locked` |

Updating is deliberately manual.

## What does NOT run here

`enrich-council` and `enrich-titles --scrape` need a **headed** Chromium (TMMIS is Akamai-gated
and blocks headless). They are not on the timer and Playwright is not installed:

- `enrich-titles --scrape` **will never find another agenda** — the Bid Award Panel was
  abolished on 2025-10-01 by By-law 766-2025, and the 891 cached pages are the final corpus.
- `enrich-council` covers 3 suspended firms and changes rarely.

Run them by hand if they are ever needed.
````

- [ ] **Step 4: Verify the calendar spec parses**

Run: `ssh ssh.plexstone.stream 'systemd-analyze calendar "*-*-* 05:30:00 America/Toronto"'`
Expected: `Normalized form: *-*-* 05:30:00 America/Toronto` and a `Next elapse` in UTC.

- [ ] **Step 5: Commit**

```bash
git add deploy/
git commit -m "feat(deploy): systemd user units and the deployment runbook"
```

---

### Task 4: Provision plexbox

**Files:** none in the repo — this task runs on the server.

**Interfaces:**
- Consumes: `deploy/README.md` §1–2 (Task 3).
- Produces: `~/toronto-bids` checkout with a working `tb`, ready for the archive to land in Task 5.

- [ ] **Step 1: Ask the operator to install the prerequisites**

This needs a password, so it cannot be run unattended. Ask the user to run:

```
! ssh -t ssh.plexstone.stream 'sudo apt install -y poppler-utils pipx && pipx install uv && pipx ensurepath'
```

- [ ] **Step 2: Verify the toolchain landed**

Run:
```bash
ssh ssh.plexstone.stream 'export PATH=$HOME/.local/bin:$PATH; uv --version; pdftotext -v 2>&1 | head -1'
```
Expected: a `uv <version>` line and a `pdftotext version <n>` line.

- [ ] **Step 3: Clone and sync**

Run:
```bash
ssh ssh.plexstone.stream 'export PATH=$HOME/.local/bin:$PATH
git clone https://github.com/CivicTechTO/toronto-bids.git ~/toronto-bids
cd ~/toronto-bids/scrapers && uv sync --locked && uv run tb --version'
```
Expected: `tb 0.1.0` (or the current `__version__`).

- [ ] **Step 4: Verify the tests pass on the server**

Run:
```bash
ssh ssh.plexstone.stream 'export PATH=$HOME/.local/bin:$PATH; cd ~/toronto-bids/scrapers && uv run pytest -q 2>&1 | tail -3'
```
Expected: PASS. Council tests skip silently without `pdftotext`; poppler is installed, so they run.

No commit — nothing in the repo changed.

---

### Task 5: Migrate the archive

**Files:** none in the repo.

**Interfaces:**
- Consumes: `~/toronto-bids` from Task 4.
- Produces: `~/tb-data/` on the server holding `bids.sqlite` and every directory the passes read.

**`files/documents/award_summary/` is not optional.** Since #116, `store_award_summary_bids`
reads each form's cells off the PDF on disk. With only the database, all 229 forms log
`unreadable` and 1,058 bids silently vanish from the next export.

- [ ] **Step 1: Record the laptop's counts, to compare against**

Run:
```bash
cd scrapers && uv run tb status | head -14
```
Expected: note `solicitation`, `award`, `bid`, `supplier`. At time of writing: bid = 18,632.

- [ ] **Step 2: Ask the operator to run the rsync**

26 GB over the network is long-running, so hand it over rather than running it here. Ask the user to run:

```
! rsync -aH --info=progress2 ~/code/personal/toronto-bids/scrapers/files/ ssh.plexstone.stream:tb-data/
```

Note `tb-data/` is relative to the remote home, matching `TB_DATA_DIR=%h/tb-data` in the unit.

- [ ] **Step 3: Verify the archive arrived intact**

Run:
```bash
ssh ssh.plexstone.stream 'export PATH=$HOME/.local/bin:$PATH
du -sh ~/tb-data ~/tb-data/documents/award_summary
ls ~/tb-data/documents/award_summary | wc -l
cd ~/toronto-bids/scrapers && TB_DATA_DIR=$HOME/tb-data uv run tb status | head -14'
```
Expected: `~/tb-data` ≈ 26 G; `documents/award_summary` holds 229 files; the counts match Step 1 exactly, `bid` included.

- [ ] **Step 4: Prove the award-summary PDFs are readable there**

The whole point of Step 3's file check — verify it end-to-end rather than trusting the count:

```bash
ssh ssh.plexstone.stream 'export PATH=$HOME/.local/bin:$PATH
cd ~/toronto-bids/scrapers && TB_DATA_DIR=$HOME/tb-data uv run tb enrich-awards 2>&1 | tail -6'
```
Expected: `bids from award summaries : 1058`, no `unreadable` lines, and `(0 new)` — it is idempotent, so a correct migration adds nothing.

No commit.

---

### Task 6: Install the units, prove a real run, enable the timer

**Files:** none in the repo.

**Interfaces:**
- Consumes: `deploy/tb-nightly.service`, `deploy/tb-nightly.timer`, `deploy/README.md` (Task 3); the provisioned box (Task 4); the migrated archive (Task 5).
- Produces: a live nightly timer.

- [ ] **Step 1: Ask the operator to install the webhook**

Never paste a webhook into a transcript. Ask the user to run:

```
! ssh -t ssh.plexstone.stream 'mkdir -p ~/.config/toronto-bids && read -rsp "Slack webhook: " W && printf "TB_SLACK_WEBHOOK=%s\n" "$W" > ~/.config/toronto-bids/tb.env && chmod 600 ~/.config/toronto-bids/tb.env && echo && ls -l ~/.config/toronto-bids/tb.env'
```
Expected: `-rw------- 1 alex alex … tb.env`

- [ ] **Step 2: Install the units**

Run:
```bash
ssh ssh.plexstone.stream 'mkdir -p ~/.config/systemd/user
cp ~/toronto-bids/deploy/tb-nightly.service ~/toronto-bids/deploy/tb-nightly.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user cat tb-nightly.service | head -5'
```
Expected: the unit prints back.

- [ ] **Step 3: Run it once, by hand, before scheduling anything**

Run:
```bash
ssh ssh.plexstone.stream 'systemctl --user start tb-nightly.service; systemctl --user is-failed tb-nightly.service; journalctl --user -u tb-nightly -n 40 --no-pager'
```
Expected: `inactive` (i.e. not failed) from `is-failed`, and a journal ending in a `✅ toronto-bids — 9/9 sources ok · …` line. Confirm with the user that the message reached the Slack channel.

- [ ] **Step 4: Verify the export was written to the data dir, not the checkout**

Run:
```bash
ssh ssh.plexstone.stream 'ls -lh ~/tb-data/export/bids.json; git -C ~/toronto-bids status --short'
```
Expected: a ~29 MB `bids.json` under `~/tb-data`, and a **clean** git status — the run must not have written anything into the checkout.

- [ ] **Step 5: Enable the timer**

Run:
```bash
ssh ssh.plexstone.stream 'systemctl --user enable --now tb-nightly.timer; systemctl --user list-timers tb-nightly.timer --no-pager'
```
Expected: `NEXT` shows tomorrow 09:30 UTC (05:30 Toronto) ± the randomized delay.

- [ ] **Step 6: Confirm it survives a logout**

Linger is already enabled, but verify rather than assume:

```bash
ssh ssh.plexstone.stream 'loginctl show-user alex | grep -i linger'
```
Expected: `Linger=yes`

- [ ] **Step 7: Correct the stale claim in CLAUDE.md**

`CLAUDE.md` says "`tb sync` hits live City endpoints and is slow; the tests are the dev loop." The measured figure is ~3 minutes. Update that line to keep the tests-are-the-dev-loop point while dropping the false claim, and add `tb nightly` to the commands block:

```shell
uv run tb nightly                         # what the server's timer runs: sync + award forms + export + Slack
```

- [ ] **Step 8: Commit and open the PR**

```bash
git add CLAUDE.md
git commit -m "docs: tb nightly, and sync is ~3 minutes rather than slow"
git push -u origin deploy-nightly-server
gh pr create --title "Nightly deployment on plexbox (sync + export + Slack)" --body "…"
```

---

## Self-review

**Spec coverage:**

| spec § | task |
|---|---|
| §3.1 user-level systemd, one sudo command | 3 (units use `%h`), 4 (step 1) |
| §3.2 `TB_DATA_DIR` outside the checkout | 3 (`Environment=`), 6 (step 4 verifies) |
| §3.3 `tb nightly`, step isolation, export after partial sync | 2 |
| §3.4 Slack summary, pure `summarize`, no-op without webhook, failure never fails the run | 1 |
| §3.5 webhook is a credential, `0600`, never in git | 3 (`EnvironmentFile=-`), 6 (step 1) |
| §3.6 timer, Toronto TZ, `Persistent`, `RandomizedDelaySec`, `TimeoutStartSec` | 3, verified 3 (step 4) and 6 (step 5) |
| §4 migration incl. `documents/award_summary/` | 5 |
| §5 error handling table | 1 (post swallows), 2 (step isolation tests) |
| §6 testing | 1, 2, 6 (step 3 live) |
| §7 non-goals | nothing installs Playwright/Docker; README states it |
| §8 poppler wart | 3 (README §1 explains the forever-retry) |

**Type consistency:** `summarize(before, after, failures, n_sources, export_bytes, elapsed_s)` and `post(text, webhook=None, log=...)` are used with those exact names and arities in Task 2's `_cmd_nightly` and Task 1's tests. `export_bytes` is `int | None` in both.

**Placeholders:** none — every step carries its code or its exact command. The one `--body "…"` in Task 6 step 8 is a PR description to be written at the time from the commits.
