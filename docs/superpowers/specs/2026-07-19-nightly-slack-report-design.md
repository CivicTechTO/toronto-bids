# Expanded nightly Slack report

**Date:** 2026-07-19
**Status:** approved, not yet implemented
**Amends:** [`2026-07-17-deployment-design.md`](2026-07-17-deployment-design.md) §3.4 (the
one-line summary) and [`2026-07-19-unified-nightly-capture-design.md`](2026-07-19-unified-nightly-capture-design.md)
§3 ("Slack summary extended"). The nightly now runs ~8 steps (sync, award summaries, portal,
Ariba attachments, agency board reports, monthly council, supplier rebuild, export); one line
hides almost all of it, and the richest signal — `sync_run`'s per-source
`rows_fetched`/`rows_upserted`/status — is never surfaced.

## 1. Goal

Turn the single-line nightly notification into a scannable multi-section report that answers,
from Slack alone, "what ran, what each step did, what moved, and what broke" — so a bad night is
diagnosable without SSHing to read the journal. Keep the every-run heartbeat (silence = the timer
never fired) and the pure, offline-tested formatter.

## 2. The two-message design (decided)

Publishing happens in the `tb-nightly-run.sh` wrapper *after* `tb nightly` (which posts Slack)
returns, so a single message cannot natively report the publish outcome. Two messages, each tool
owning its own line:

1. **`tb nightly`** posts the **rich archive report** (sync → export) — the bulk of this spec.
2. **`publish-data.sh`** posts a **one-line publish result** (✅ + release URL, or ❌ + reason)
   after it runs.

Both read `TB_SLACK_WEBHOOK` from the same environment (the unit's `EnvironmentFile`), and both
no-op when it is unset (dev/CI stay silent with no separate code path). A failed post never fails
the run.

## 3. The rich report (`tb nightly`)

### 3.1 Structured bookkeeping in `_cmd_nightly`

Today `_cmd_nightly` records only `failures: list[(name, error)]`. Replace that with structured
per-step records so the report can show status, detail, and timing per step.

- A `steps: list[dict]` where each entry is `{"name": str, "status": "ok"|"fail"|"skip",
  "detail": str, "seconds": float, "error": str | None}`.
- A helper `_run_step(steps, name, fn)` that times `fn`, appends an `"ok"` record with
  `detail=fn()` (a short string the step returns, e.g. `"+48 bundles"`), or a `"fail"` record with
  `error=str(exc)` on exception — preserving today's isolation (a raising step never stops the
  ones behind it or the export). A skip is recorded explicitly (council when not the 1st) as
  `status="skip"`.
- Each capture step's `fn` returns its own short detail where it cheaply can (award summaries:
  bids added; Ariba attachments: bundles captured; supplier rebuild: dimension size; export:
  size). Where a step has no natural scalar (agencies), detail is derived from the relevant count
  delta snapshotted around that step (a couple of `COUNT(*)` reads), or left blank — the **Growth**
  section (§3.4) carries the full delta story regardless.

The overall run is **ok** iff no step failed and no source failed; `_cmd_nightly` still exits
non-zero otherwise (systemd marks the unit failed).

### 3.2 Per-source sync detail

`sync_run.id` is autoincrement. Capture `SELECT MAX(id) FROM sync_run` **before** `pipeline.sync`,
then after read every row with `id > that_max` — this run's per-source records
(`source, status, rows_fetched, rows_upserted, error`). Add a small read helper
`db.sync_runs_since(conn, after_id) -> list[dict]`. A source that ran with `rows_fetched == 0`, or
`status != "ok"`, is flagged `⚠` — the silent-upstream-break signal the current "9/9 ok" hides.

### 3.3 Counts

Full `db.counts` before and after the run (as today) drives the Growth section — every table whose
count moved, not just the four headline ones.

### 3.4 Message layout (multi-line Slack mrkdwn)

`post` is unchanged — it still sends `{"text": …}`; Slack renders mrkdwn in the `text` field, so no
Block Kit is needed. A pure `summarize(report) -> str` builds:

```
*✅ toronto-bids nightly* · 58m04s

*Steps*
✅ sync              9/9 sources · 3m12s
✅ award summaries   +12 bids
✅ portal            no open bids
❌ ariba attachments TimeoutError on Respond · 48m
✅ agencies          +7 awards, +15 bids · 6m
➖ council           skipped (not the 1st)
✅ supplier rebuild  8,022 suppliers
✅ export            31.1 MiB

*Sources* (fetched → new)
odata_solicitations 7,446 → +12 · ckan_awarded 14,165 → +8 · ariba_discovery 1,670 → +0
⚠ suspended_firms 0 fetched

*Growth*
solicitations +12 · awards +8 · bids +27 · ariba files +111 · agency awards +7 · agency bids +15

*Failures (1)*
ariba_attachments: TimeoutError on Respond
```

Rules:
- **Header** stays `✅/❌ toronto-bids nightly` + total elapsed, so the at-a-glance heartbeat and
  the failed/ok signal survive the expansion. `❌` when the run is not ok.
- **Steps** — one line per step in run order, `✅` ok / `❌` fail / `➖` skip, the step's detail,
  and its duration when ≥ a small threshold (skip sub-second noise).
- **Sources** — this-run `sync_run` rows, compact, `fetched → +new`; a `⚠`-flagged line for any
  source that fetched 0 or whose status isn't ok.
- **Growth** — every non-zero count delta (reuses the existing `_seg`/`_count` delta style and
  `,`-thousands formatting). Omitted entirely if nothing moved.
- **Failures** — present only when there are failures; lists each failed step/source with its
  error. Errors are the same strings `_cmd_nightly` already records — no new leakage surface (the
  webhook-credential care in `post` is unchanged).
- An empty `before` (the before-count step itself failed) still suppresses fabricated deltas, as
  today.

The message can run ~15-25 lines; that is the point, and Slack handles it. Every-run posting is
retained (heartbeat).

### 3.5 Signature change

`summarize` moves from `summarize(before, after, failures, n_sources, export_bytes, elapsed_s)` to
`summarize(report: dict) -> str`, where `report = {"ok", "steps", "sources", "before", "after",
"export_bytes", "elapsed_s"}`. `_cmd_nightly` assembles `report` and passes it. This is a breaking
signature change to a purely-internal function — all callers are `_cmd_nightly` and the tests.

## 4. The publish line (`publish-data.sh`)

Add a guarded `slack_notify` bash helper and post once at the end:

- On success: `✅ toronto-bids publish — latest release updated · <generated_at> · <release URL>`.
- On failure (each existing `fail` path): `❌ toronto-bids publish — <reason>` before exiting.
- `slack_notify` no-ops when `TB_SLACK_WEBHOOK` is unset; under `TB_PUBLISH_DRY_RUN=1` it echoes
  instead of curling (mirrors `gh_run`). Posting uses `curl -s -o /dev/null --data-urlencode`
  style with the webhook as the URL only (never echoed/logged), so the credential does not leak —
  the same discipline `notify.post` follows.
- A failed Slack post never changes the publish exit code (the release upload is the deliverable).
- The agenda-archive best-effort warnings (§ from the unified-nightly work) are not publish
  failures and do not post.

## 5. Error handling

- The report is assembled defensively: if `db.counts` or `sync_runs_since` raises, that is recorded
  as a step failure and the report still posts with what it has (the summary must never be the
  thing that fails the run — its whole reason for existing is to make failure visible).
- `_run_step` catches every step exception (isolation preserved); `summarize` is pure and total
  over a partial `report` (missing keys degrade to omitted sections, never a `KeyError`).
- `post` and `slack_notify` swallow their own errors and log a type/status only, as `notify.post`
  already does.

## 6. Testing

- **`summarize` is pure and fully offline-tested** against fixture `report` dicts: a clean run
  (all sections), a run with a failed step (❌ header + Failures section), a run with a skipped
  council, a run with a `⚠` zero-fetch source, a run where nothing moved (no Growth section), and a
  run with an empty `before` (no fabricated deltas). Assert exact substrings, as the current
  `test_notify.py` does.
- **`sync_runs_since`** tested against an in-memory DB: seed two runs, assert only the newer run's
  rows return.
- **`_run_step`** tested: an ok step records ok+detail+seconds; a raising step records fail+error
  and does not propagate.
- **`_cmd_nightly`** existing isolation tests updated for the new `report`/`steps` plumbing (the
  `nightly` fixture already stubs the capture calls); assert the posted text contains the Steps
  header and a failed step surfaces in Failures.
- **`publish-data.sh`** `slack_notify`: dry-run shows the echoed post line on success; no webhook →
  no post. (Bash, verified by dry-run + inspection, as with the rest of the script.)
- **Live**, after merge on plexbox: one real nightly, confirm the multi-section message renders in
  Slack and the separate publish line follows.

## 7. Out of scope

- Slack Block Kit / attachments / threading — plain mrkdwn `text` only.
- Per-step timing precision beyond `Xm`/`Xs` (the existing `_elapsed` granularity).
- Posting anything about the frontend build trigger (that stays a `publish-data.sh` stderr warning;
  it is not archive state).
- Changing what counts as a failure or the exit-code contract — only the *presentation* expands.

## 8. Recording

On completion, note in the deployment design's lineage that §3.4's one-liner is superseded by the
multi-section report, and update `deploy/README.md`'s description of what the nightly posts.
