# Expanded Nightly Slack Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the nightly's one-line Slack notification with a multi-section mrkdwn report (Steps / Sources / Growth / Failures), and have `publish-data.sh` post a separate one-line publish result.

**Architecture:** A pure `summarize(report: dict) -> str` renders the whole message and degrades gracefully over a partial `report` (missing sections just don't appear). `_cmd_nightly` assembles the `report` — first from data it already has (Task 2), then enriched with structured per-step records and this-run `sync_run` rows (Task 3). The publish line is a guarded bash `slack_notify` in `publish-data.sh` (Task 4). Delivering the formatter and the plumbing in that order keeps every task green and independently shippable.

**Tech Stack:** Python 3.12, `uv`, pytest (offline, `monkeypatch`). Bash + `curl` for the publish line. Slack renders mrkdwn inside the `{"text": …}` payload — no Block Kit.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-nightly-slack-report-design.md`.
- **No lint/format/typecheck exists** — the only check is `uv run pytest` from `scrapers/`. Don't invent tooling.
- **`summarize` stays pure** (no I/O, no `db`/network) and total over a partial `report` — a missing key degrades to an omitted section, never a `KeyError`.
- **The webhook is a credential and this repo is public.** Never log or echo `TB_SLACK_WEBHOOK`. `notify.post` already logs only status codes / exception *types*; the new bash `slack_notify` must pass the webhook only as the curl URL, never in an echoed/logged string.
- **The exit-code contract is unchanged:** `_cmd_nightly` returns 1 iff there was any failure. The expansion changes *presentation only* — not what counts as a failure.
- **Isolation preserved:** every nightly step stays wrapped so a raising step never stops later steps, the export, or the post.
- **Every-run posting is retained** (the heartbeat: silence = the timer never fired).
- Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE
  ```
- Branch `feat-nightly-slack-report` (already checked out). Do not commit to `main`.

## File Structure

- `scrapers/toronto_bids/store/db.py` — add `sync_runs_since(conn, after_id)` (Task 1).
- `scrapers/toronto_bids/notify.py` — rewrite `summarize` to `summarize(report: dict)` + section helpers (Task 2).
- `scrapers/toronto_bids/cli.py` — `_cmd_nightly`: build `report` and call new `summarize` (Task 2); add `_run_step` + `steps`/`sources` bookkeeping (Task 3).
- `scrapers/tests/test_notify.py` — rewrite for the report form (Task 2).
- `scrapers/tests/test_nightly.py` — assert the new message sections (Tasks 2, 3).
- `scrapers/tests/test_db.py` (or nearest existing db test file) — `sync_runs_since` (Task 1).
- `deploy/publish-data.sh`, `deploy/README.md` — publish Slack line (Task 4).

---

## Task 1: `db.sync_runs_since` — this-run per-source rows

**Files:**
- Modify: `scrapers/toronto_bids/store/db.py` (near `finish_sync_run` ~line 277)
- Test: `scrapers/tests/test_db.py` (create if absent; otherwise append)

**Interfaces:**
- Produces: `sync_runs_since(conn, after_id: int) -> list[dict]` — every `sync_run` row with `id > after_id`, ordered by `id`, each a dict with keys `source, status, rows_fetched, rows_upserted, error`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

Create/append `scrapers/tests/test_db.py`:

```python
from toronto_bids.store import db


def test_sync_runs_since_returns_only_newer_rows(conn):
    r1 = db.start_sync_run(conn, "odata_solicitations")
    db.finish_sync_run(conn, r1, status="ok", rows_fetched=7446, rows_upserted=12)
    cutoff = r1
    r2 = db.start_sync_run(conn, "ariba_discovery")
    db.finish_sync_run(conn, r2, status="ok", rows_fetched=1670, rows_upserted=0)
    r3 = db.start_sync_run(conn, "ckan_awarded")
    db.finish_sync_run(conn, r3, status="failed", rows_fetched=0, rows_upserted=0, error="boom")

    rows = db.sync_runs_since(conn, cutoff)
    assert [r["source"] for r in rows] == ["ariba_discovery", "ckan_awarded"]
    assert rows[0]["rows_fetched"] == 1670 and rows[0]["rows_upserted"] == 0
    assert rows[1]["status"] == "failed" and rows[1]["error"] == "boom"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_db.py -v`
Expected: FAIL — `db.sync_runs_since` does not exist.

- [ ] **Step 3: Implement**

In `db.py`, after `finish_sync_run`:

```python
def sync_runs_since(conn, after_id: int) -> list[dict]:
    """Every sync_run row newer than after_id, oldest first — one nightly's per-source detail.

    Capture MAX(id) before pipeline.sync, pass it here after, and you get exactly the rows that
    run wrote (sync_run.id is autoincrement).
    """
    cur = conn.execute(
        "SELECT source, status, rows_fetched, rows_upserted, error "
        "FROM sync_run WHERE id > ? ORDER BY id", (after_id,))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
```

- [ ] **Step 4: Run test**

Run: `cd scrapers && uv run pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `cd scrapers && uv run pytest -q` (expect all pass), then:

```bash
git add scrapers/toronto_bids/store/db.py scrapers/tests/test_db.py
git commit -m "feat(db): sync_runs_since — a nightly's per-source rows for the report

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 2: Rewrite `summarize` to the report form; wire `_cmd_nightly` to it

**Files:**
- Modify: `scrapers/toronto_bids/notify.py` (`summarize` ~line 40; keep `_count`, `_elapsed`, `_HEADLINE`)
- Modify: `scrapers/toronto_bids/cli.py` (`_cmd_nightly` — the `text = notify.summarize(...)` call ~line 480, and assemble a `report` dict)
- Test: `scrapers/tests/test_notify.py` (rewrite), `scrapers/tests/test_nightly.py` (adjust message assertions)

**Interfaces:**
- Produces: `summarize(report: dict) -> str`. `report` keys (all optional; summarize is total over partials): `ok: bool`, `steps: list[dict]`, `sources: list[dict]`, `before: dict`, `after: dict`, `failures: list[tuple[str,str]]`, `export_bytes: int|None`, `elapsed_s: float`. Step dict: `{name, status: "ok"|"fail"|"skip", detail: str, seconds: float, error: str|None}`. Source dict: `{source, status, rows_fetched, rows_upserted, error}`.
- Consumes: nothing from other tasks (Task 3 fills `steps`/`sources`; this task leaves them `[]`).

- [ ] **Step 1: Write the failing tests**

Replace the body of `scrapers/tests/test_notify.py` (keep the existing `post`-related tests if any; replace the `summarize` tests) with report-based tests:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_notify.py -v`
Expected: FAIL — `summarize` has the old signature.

- [ ] **Step 3: Rewrite `summarize` and add section helpers**

In `notify.py`, keep `_elapsed` and `post`. Replace `_HEADLINE`/`_count`/`_seg`/`summarize` with:

```python
_STEP_ICON = {"ok": "✅", "fail": "❌", "skip": "➖"}

# Nicer labels for the Growth section; unlisted tables fall back to the raw key.
_LABELS = {
    "solicitation": "solicitations", "award": "awards", "bid": "bids", "supplier": "suppliers",
    "noncompetitive": "noncompetitive", "ariba_posting": "ariba postings",
    "suspended_firm": "suspended firms", "capital_project": "capital projects",
    "council_item": "council items", "background_pdf": "background pdfs",
    "composite_award": "composite awards", "agency_solicitation": "agency solicitations",
    "agency_award": "agency awards", "agency_bid": "agency bids",
    "ariba_attachment": "ariba files",
}
# Housekeeping tables whose growth is noise in a report about the archive.
_GROWTH_SKIP = {"sync_run", "buyer"}


def _growth(before: dict, after: dict) -> list[str]:
    """'solicitations 7,446 (+12)' for every table that grew. Empty `before` (the count step
    failed) yields nothing — a delta against a zero nobody measured is a fabricated number."""
    if not before:
        return []
    out = []
    for key, now in after.items():
        if key in _GROWTH_SKIP:
            continue
        delta = now - before.get(key, 0)
        if delta:
            out.append(f"{_LABELS.get(key, key)} {now:,} ({delta:+,})")
    return out


def summarize(report: dict) -> str:
    """The nightly message — multi-section Slack mrkdwn. Pure and total over a partial report:
    a missing/empty section is simply omitted, never a KeyError, because the report itself must
    never be the thing that fails the run it exists to make visible.
    """
    before = report.get("before", {})
    after = report.get("after", {})
    failures = report.get("failures", [])
    steps = report.get("steps", [])
    sources = report.get("sources", [])
    export_bytes = report.get("export_bytes")
    elapsed_s = report.get("elapsed_s", 0.0)
    ok = report.get("ok", not failures)

    lines = [f"*{'✅' if ok else '❌'} toronto-bids nightly* · {_elapsed(elapsed_s)}"]

    if steps:
        lines += ["", "*Steps*"]
        for s in steps:
            icon = _STEP_ICON.get(s.get("status"), "•")
            detail = s.get("detail") or s.get("error") or ""
            dur = s.get("seconds") or 0.0
            suffix = f" · {_elapsed(dur)}" if dur >= 1 else ""
            lines.append(f"{icon} {s.get('name', '?')}  {detail}{suffix}".rstrip())

    if sources:
        lines += ["", "*Sources* (fetched → new)"]
        ok_segs, warns = [], []
        for r in sources:
            fetched = r.get("rows_fetched", 0) or 0
            new = r.get("rows_upserted", 0) or 0
            if r.get("status") != "ok" or fetched == 0:
                extra = "" if r.get("status") == "ok" else f" ({r.get('status')})"
                warns.append(f"⚠ {r.get('source', '?')} {fetched:,} fetched{extra}")
            else:
                ok_segs.append(f"{r.get('source', '?')} {fetched:,} → +{new:,}")
        if ok_segs:
            lines.append(" · ".join(ok_segs))
        lines += warns

    growth = _growth(before, after)
    if growth:
        lines += ["", "*Growth*", " · ".join(growth)]

    lines += ["", f"export {'FAILED' if export_bytes is None else f'{export_bytes / 1_048_576:.1f} MiB'}"]

    if failures:
        lines += ["", f"*Failures ({len(failures)})*"]
        lines += [f"{name}: {error}" for name, error in failures]

    return "\n".join(lines)
```

Note the test `test_steps_section_renders_status_detail_and_skip` expects `"❌ ariba attachments  boom · 48m00s"` — `_elapsed(2880.0)` is `48m00s`; the test's `or` accepts it. Confirm `_elapsed` renders `192.0 → "3m12s"` and `2880.0 → "48m00s"`.

- [ ] **Step 4: Wire `_cmd_nightly` to build a `report` and call the new `summarize`**

In `_cmd_nightly`, replace the final summarize/post block:

```python
    text = notify.summarize(before, after, failures, len(pipeline.default_sources()),
                            export_bytes, time.monotonic() - started)
    print(text)
    for name, error in failures:
        print(f"FAILED  {name}: {error}", file=sys.stderr)
    notify.post(text, log=lambda m: print(m, file=sys.stderr))
    return 1 if failures else 0
```

with:

```python
    report = {
        "ok": not failures,
        "steps": steps,            # populated in Task 3; [] here
        "sources": sources,        # populated in Task 3; [] here
        "before": before,
        "after": after,
        "failures": failures,
        "export_bytes": export_bytes,
        "elapsed_s": time.monotonic() - started,
    }
    text = notify.summarize(report)
    print(text)
    for name, error in failures:
        print(f"FAILED  {name}: {error}", file=sys.stderr)
    notify.post(text, log=lambda m: print(m, file=sys.stderr))
    return 1 if failures else 0
```

And near the top of `_cmd_nightly` (with the other init vars `failures = []`, `before = {}`), add:

```python
    steps: list[dict] = []
    sources: list[dict] = []
```

- [ ] **Step 5: Adjust `test_nightly.py` message assertions**

Find any nightly test that asserts on the old one-line format (e.g. substrings like `"9/9 sources ok"` or `"toronto-bids —"`). Update them to the new header: a clean run's posted text starts with `"*✅ toronto-bids nightly*"`, a failed run with `"*❌ toronto-bids nightly*"`, and a failed step appears under `"*Failures"`. Do NOT weaken the exit-code assertions (still `== 0` / `== 1`). Run `grep -n "toronto-bids\|sources ok\|summarize" tests/test_nightly.py` to find them.

- [ ] **Step 6: Run tests**

Run: `cd scrapers && uv run pytest tests/test_notify.py tests/test_nightly.py -v`
Expected: PASS.

- [ ] **Step 7: Full suite + commit**

Run: `cd scrapers && uv run pytest -q` (all pass), then:

```bash
git add scrapers/toronto_bids/notify.py scrapers/toronto_bids/cli.py scrapers/tests/test_notify.py scrapers/tests/test_nightly.py
git commit -m "feat(nightly): multi-section Slack report (Growth + Failures sections)

summarize now takes a report dict and renders multi-line mrkdwn, degrading
gracefully over a partial report. _cmd_nightly assembles the report from the
data it already has; steps/sources sections arrive next.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 3: Structured per-step + per-source bookkeeping in `_cmd_nightly`

**Files:**
- Modify: `scrapers/toronto_bids/cli.py` (`_cmd_nightly`; add module-level `_run_step`)
- Test: `scrapers/tests/test_nightly.py`

**Interfaces:**
- Consumes: `db.sync_runs_since` (Task 1); the `report` assembly + `steps`/`sources` vars (Task 2); `summarize` already renders these sections (Task 2).
- Produces: `_run_step(steps, failures, name, fn) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `scrapers/tests/test_nightly.py`:

```python
def test_report_has_a_steps_section_naming_each_step(nightly, monkeypatch):
    posted = {}
    from toronto_bids import notify
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    nightly()
    t = posted["text"]
    assert "*Steps*" in t
    for name in ("sync", "award summaries", "ariba attachments", "agencies", "export"):
        assert name in t


def test_a_failed_step_appears_in_both_failures_and_steps(nightly, monkeypatch):
    posted = {}
    from toronto_bids import notify
    monkeypatch.setattr(notify, "post", lambda text, **k: posted.setdefault("text", text))
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(ariba_attachments, "capture_attachments",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("browser died")))
    assert nightly() == 1
    t = posted["text"]
    assert "*Failures (1)*" in t
    assert "browser died" in t
    assert "❌ ariba attachments" in t


def test_run_step_records_ok_and_isolates_failure():
    from toronto_bids import cli
    steps, failures = [], []
    cli._run_step(steps, failures, "demo", lambda: "+3 things")
    assert steps[0]["status"] == "ok" and steps[0]["detail"] == "+3 things"
    cli._run_step(steps, failures, "boom", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert steps[1]["status"] == "fail" and steps[1]["error"] == "x"
    assert failures == [("boom", "x")]   # failure mirrored for the exit-code contract
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_nightly.py -k "steps or failed_step or run_step" -v`
Expected: FAIL — `_run_step` doesn't exist; no Steps section yet.

- [ ] **Step 3: Add `_run_step`**

In `cli.py`, module-level (near `_is_first_of_month`):

```python
def _run_step(steps: list, failures: list, name: str, fn) -> None:
    """Run one nightly step, timing it and recording a step record. On failure the error is
    ALSO appended to `failures` — the authoritative list the exit code and the report's Failures
    section read — so the exit-code contract is untouched while the Steps section gains status +
    timing. `fn` returns a short detail string (or None)."""
    import time
    t = time.monotonic()
    try:
        detail = fn()
        steps.append({"name": name, "status": "ok", "detail": detail or "",
                      "seconds": time.monotonic() - t, "error": None})
    except Exception as exc:  # isolation: never propagates
        steps.append({"name": name, "status": "fail", "detail": "",
                      "seconds": time.monotonic() - t, "error": str(exc)})
        failures.append((name, str(exc)))
```

- [ ] **Step 4: Convert `_cmd_nightly`'s steps to `_run_step` + record sources**

Rewrite the capture section of `_cmd_nightly`. Replace the inner `try: … finally: http.close()` block (the sync/award/portal/ariba/agencies/council sequence) so each step goes through `_run_step` and returns a detail string, and capture the sync_run cutoff around sync. Concretely:

Before `pipeline.sync`, record the cutoff:
```python
            try:
                sync_cutoff = conn.execute("SELECT COALESCE(MAX(id), 0) FROM sync_run").fetchone()[0]
            except Exception:
                sync_cutoff = 0
```

Sync step (special — pipeline.sync returns per-source failures, not an exception):
```python
                def _sync():
                    src_failures = pipeline.sync(conn, http)
                    failures.extend(src_failures)
                    n = len(pipeline.default_sources())
                    ok_n = n - len([f for f in src_failures if ":" not in f[0]])
                    return f"{ok_n}/{n} sources"
                _run_step(steps, failures, "sync", _sync)
```
(Note: `pipeline.sync` also runs linking passes whose failures land in `src_failures`; the `ok_n` count is a headline, not exact — the Sources section carries the precise per-source truth. Keep it simple: `return f"{n} sources"` is acceptable if the ok-count is awkward; the reviewer may simplify.)

Then read this-run sources right after sync:
```python
                try:
                    sources.extend(db.sync_runs_since(conn, sync_cutoff))
                except Exception as exc:
                    failures.append(("sync_detail", str(exc)))
```

Award summaries:
```python
                def _awards():
                    download_award_summaries(conn, http, log=out)
                    return f"{store_award_summary_bids(conn, log=out)} bids stored"
                _run_step(steps, failures, "award summaries", _awards)
```
(Confirm `store_award_summary_bids` returns an int count; if it returns None, use `store_award_summary_bids(conn, log=out); return ""`.)

Portal:
```python
                def _portal():
                    from toronto_bids.sources.bids_tenders import run_portal_capture
                    res = run_portal_capture(conn, log=out)
                    for slug, v in res.items():
                        if isinstance(v, str) and v.startswith("FAILED"):
                            failures.append((f"portal:{slug}", v))
                    total = sum(v for v in res.values() if isinstance(v, int))
                    return f"{total} listings" if total else "no open bids"
                _run_step(steps, failures, "portal", _portal)
```

Ariba attachments:
```python
                def _ariba():
                    from toronto_bids.sources import ariba_attachments as aa
                    n = aa.capture_attachments(conn, log=out, virtual_display=True)
                    return f"+{n} bundles"
                _run_step(steps, failures, "ariba attachments", _ariba)
```

Agencies (detail from the agency_* count delta around the step):
```python
                def _agencies():
                    from toronto_bids.buyers import seed_buyers
                    a0 = db.counts(conn)
                    ids = seed_buyers(conn)
                    failures.extend(_capture_agency_bodies(
                        conn, ids, bodies=["trca", "zoo", "ep"],
                        fetch=True, scrape=True, virtual_display=True, out=out))
                    a1 = db.counts(conn)
                    da = a1["agency_award"] - a0["agency_award"]
                    db_ = a1["agency_bid"] - a0["agency_bid"]
                    return f"+{da} awards, +{db_} bids"
                _run_step(steps, failures, "agencies", _agencies)
```

Council (skip records a step; run records via `_run_step`):
```python
                if _is_first_of_month():
                    def _council():
                        from functools import partial
                        from toronto_bids.sources.council import enrich_council, fetch_agenda_item
                        n = enrich_council(conn, http, fetch=partial(fetch_agenda_item, virtual_display=True))
                        return f"{n} items"
                    _run_step(steps, failures, "council", _council)
                else:
                    steps.append({"name": "council", "status": "skip",
                                  "detail": "not the 1st", "seconds": 0.0, "error": None})
```

Keep the `finally: http.close()` (still recording `http_close` failures into `failures` — that can stay a plain try/except; it is not a user-facing step).

Supplier rebuild and export become steps too (they run after `http.close()`):
```python
        def _supplier():
            from toronto_bids.linking.supplier import build_supplier_dimension
            return f"{build_supplier_dimension(conn)} suppliers"
        _run_step(steps, failures, "supplier rebuild", _supplier)

        def _export():
            nonlocal export_bytes
            written = export_json(conn, Path(config.DATA_DIR) / "export" / "bids.json")
            export_bytes = written.stat().st_size
            return f"{export_bytes / 1_048_576:.1f} MiB"
        _run_step(steps, failures, "export", _export)
```
(`export_bytes` must be declared `nonlocal`-able — it's a local of `_cmd_nightly`; since `_export` is a nested function, `nonlocal export_bytes` works. Keep the existing `after = db.counts(conn)` and `conn.close()` blocks as they are.)

- [ ] **Step 5: Run tests**

Run: `cd scrapers && uv run pytest tests/test_nightly.py -v`
Expected: PASS (new step/source tests green; existing isolation + exit-code tests still green — a raising step still yields exit 1 and the export still runs).

- [ ] **Step 6: Full suite + commit**

Run: `cd scrapers && uv run pytest -q` (all pass), then:

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_nightly.py
git commit -m "feat(nightly): per-step + per-source detail in the Slack report

_run_step times and records each step (status/detail/seconds), sync_runs_since
supplies per-source fetched/new, and the report's Steps/Sources sections light
up. Failures still mirrored to the authoritative list — exit-code contract
unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 4: `publish-data.sh` posts a one-line publish result

**Files:**
- Modify: `deploy/publish-data.sh`
- Modify: `deploy/README.md` (note the two-message design)

**Interfaces:** none (deploy-only).

- [ ] **Step 1: Add a guarded `slack_notify` helper**

In `deploy/publish-data.sh`, after the `gh_run()` definition, add:

```bash
# Post one line to Slack (best-effort). The webhook is a credential and this repo is public, so
# it is passed ONLY as the curl URL — never echoed or logged. No webhook -> no-op; dry-run echoes.
slack_notify() {
  local msg="$1"
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY-RUN slack: $msg"
    return 0
  fi
  [ -n "${TB_SLACK_WEBHOOK:-}" ] || return 0
  curl -s -o /dev/null --max-time 15 \
    -H 'Content-Type: application/json' \
    --data "$(printf '{"text": %s}' "$(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
    "$TB_SLACK_WEBHOOK" || echo "publish-data: WARNING — slack post failed" >&2
}
```

- [ ] **Step 2: Make `fail` post before exiting**

Change the existing `fail()` to notify then exit:

```bash
fail() { slack_notify "❌ toronto-bids publish — $*"; echo "publish-data: $*" >&2; exit 1; }
```
(`slack_notify` is defined above `fail` — confirm ordering; if `fail` is defined before `gh_run`/`slack_notify`, move `slack_notify` up so it precedes `fail`.)

- [ ] **Step 3: Post success at the very end**

Just before the final `echo "publish-data: done"`, add:

```bash
slack_notify "✅ toronto-bids publish — latest release updated · ${GENERATED_AT} · https://github.com/${DATA_REPO}/releases/tag/latest"
```

- [ ] **Step 4: Verify syntax + dry-run (plexbox)**

Run:
```bash
bash -n deploy/publish-data.sh && echo OK
cd /home/alex/toronto-bids && TB_PUBLISH_DRY_RUN=1 TB_DATA_DIR="$HOME/tb-data" TB_PUBLISH_DAY=15 deploy/publish-data.sh 2>&1 | grep -iE "DRY-RUN slack|generated_at|done"
```
Expected: `OK`; and the dry-run shows a `DRY-RUN slack: ✅ toronto-bids publish — latest release updated · …` line and `publish-data: done`, exit 0. Paste the output into your report. Do NOT run a real publish.

- [ ] **Step 5: Document in `deploy/README.md`**

In the "Publishing the export" section, add a sentence: the nightly posts two Slack messages — the rich archive report from `tb nightly`, then a one-line publish result from `publish-data.sh` (both use `TB_SLACK_WEBHOOK`; both no-op when it is unset).

- [ ] **Step 6: Commit**

```bash
git add deploy/publish-data.sh deploy/README.md
git commit -m "feat(deploy): publish-data.sh posts a one-line Slack publish result

Second of the two nightly messages: tb nightly posts the archive report,
publish-data.sh posts publish success (+release URL) or failure. Best-effort,
credential-safe (webhook only as the curl URL), no-op without a webhook.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Post-implementation: live verification on plexbox

Not a code task — after merge: run one real nightly (`systemctl --user start tb-nightly.service`), confirm the multi-section archive report renders in Slack and the separate publish line follows.

## Self-Review

**Spec coverage:** two-message design → Tasks 2/3 (report) + Task 4 (publish line) ✓; Steps section → Task 3 ✓; Sources + `⚠` zero-fetch → Tasks 1+3 ✓; Growth all-deltas → Task 2 ✓; Failures section + timing → Tasks 2/3 ✓; pure/total summarize → Task 2 ✓; heartbeat + exit-code unchanged → Task 2 (`ok`/return) + Task 3 (`_run_step` mirrors to `failures`) ✓; credential safety → Task 4 (`slack_notify` webhook-as-URL) ✓; degrade over partial report → Task 2 `summarize` `.get` everywhere ✓.

**Placeholder scan:** No TBD/TODO. Each code step carries full code. The one soft edge — the sync step's `ok_n` count — is called out with an acceptable fallback (`f"{n} sources"`), not left vague.

**Type consistency:** `report` dict keys and step/source dict shapes are identical between Task 2 (`summarize` reads them) and Task 3 (`_cmd_nightly` writes them). `_run_step(steps, failures, name, fn)` defined and used consistently. `sync_runs_since(conn, after_id) -> list[dict]` with keys `source/status/rows_fetched/rows_upserted/error` matches what the Sources renderer reads.
