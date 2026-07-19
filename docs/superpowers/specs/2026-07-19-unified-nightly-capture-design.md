# Unified nightly capture (everything live, one run)

**Date:** 2026-07-19
**Status:** approved, not yet implemented
**Amends:** [`2026-07-17-deployment-design.md`](2026-07-17-deployment-design.md) §3 and
[`2026-07-19-publish-data-design.md`](2026-07-19-publish-data-design.md). Those established the
nightly as a **browser-free** plain-HTTP job with browser-bound captures on their own timers or
on-demand. This deliberately reverses that principle.

## 1. The principle

**The nightly run should capture everything that accrues live, every night, in one service.** The
archive's job is to keep the record current without anyone remembering to run anything; a capture
that only runs on-demand silently rots between manual runs. So every source that keeps producing
new records — including the browser-bound ones — moves onto the scheduled path.

This overturns the deployment design's "nothing browser-bound is on the scheduled path." That rule
bought robustness (a ~4-minute plain-HTTP job that rarely fails); the cost was that Ariba
attachments ran on a *separate* daily timer (a day-lagged pipeline) and the Zoo/TRCA/EP
board-report captures (#136/#139/#141) ran **only when someone remembered**. The maintainer's
call: completeness over the robustness margin, with per-step isolation carrying the safety.

## 2. What runs, and when

### Nightly (`tb nightly`, 05:30 America/Toronto), in order

1. `sync` — OData spine, CKAN backfill, Ariba Discovery *postings*, suspended firms, schema-check (plain HTTP)
2. Award Summary Forms — `download_award_summaries` + `store_award_summary_bids` (plain HTTP)
3. bids&tenders portal listings — `run_portal_capture` (plain HTTP; TRCA/Zoo, empty today → no-op)
4. **Ariba attachments** — `enrich-ariba-attachments --capture` (headed Chromium under Xvfb) — folded in from the retired noon timer
5. **Zoo / TRCA / EP board reports** — the `enrich-agencies` scrape+parse for all three bodies, previously on-demand only. **Zoo and EP** need headed Chromium under Xvfb (TMMIS is Akamai-gated); **TRCA** is plain HTTP (eSCRIBE `GetCalendarMeetings` JSON + plain-HTTP report PDFs), so its failure surface is smaller
6. **`enrich-council`** — *gated to the 1st of the month only* (browser; 3 suspended firms, rarely changes)
7. supplier-dimension rebuild — so suppliers newly introduced by steps 4–6 enter `supplier`
8. `export` — now reflects everything captured tonight
9. publish — the `tb-nightly-run.sh` wrapper runs `publish-data.sh` after `tb nightly` (unchanged from #146)

### Retired

- **`tb-ariba-attachments.timer`** — its capture is now step 4. (Accepted minor tradeoff: Respond
  reaches only *currently-open* events, so one capture/day catches marginally fewer than the old
  twice-a-day cadence would have. Unification wins.)

### Not scheduled, deliberately

- `enrich-titles --scrape` — **removed** (see §4). The Bid Award Panel was abolished 2025-10-01;
  the 891 cached agendas are the final corpus, so a BA re-scrape can never find a new agenda.
- Offline `enrich-titles` (no `--scrape`) — kept as a maintenance/provisioning command, but not
  on the timer: re-parsing the static cached agendas yields nothing new night to night.

## 3. Structure and isolation

Orchestration stays **inside `tb nightly` (Python)**, not the shell wrapper — the deployment
design's reasoning holds: the Slack summary needs real before/after `db.counts()` and per-source
`sync_run` status, and recovering those by parsing chatty CLI stdout is the failure mode #116
removed. The wrapper keeps doing exactly one thing: run `tb nightly`, then publish, failing the
unit if either failed.

**Every new step is isolated exactly as `pipeline.run_source` isolates a source** — the pattern
already in `_cmd_nightly` (each step in its own `try`, a failure appended to `failures` and
recorded, the steps behind it still run). Concretely:

- A browser crash, an Akamai block, or an Ariba login failure in steps 4–6 records to `sync_run`
  and **never stops sync, export, or publish**. The archive and its artifact outrank any single
  capture.
- The export runs off `conn` regardless of any capture's outcome (partial data is still data).
- The run exits non-zero if any step failed, so systemd marks the unit failed and the Slack line
  shows which step — but the good artifact still shipped.

**Browser steps run under a virtual display.** The browser-bound steps (4, Zoo/EP in 5, and 6)
pass `virtual_display=True` unconditionally — the nightly is the unattended server path, exactly
as the retired Ariba timer ran `--virtual-display`. Prerequisites are already installed on the box (`council` extra,
Playwright Chromium, Xvfb, Ariba credentials in `tb.env`); the retired attachments timer proved
the headless-browser path works here.

**Ordering rationale.** Plain-HTTP steps (1–3) first — cheap, reliable, and the export is valid
even if every browser step later fails. Browser captures (4–6) next. Supplier-dimension rebuild
(7) after 4–6 so agency winners/bidders and any attachment-introduced names are grouped. Export
(8) last so the published artifact is same-night complete — closing the day-lag that the separate
noon Ariba timer imposed.

### Operational

- **`TimeoutStartSec` 30m → 3h.** The Ariba attachments cold sweep is ~1–2h (#122); warm runs are
  minutes (a cached agenda/bundle is skipped). Agency scrapes are incremental against cached
  agendas. 3h is a generous ceiling, not an expected duration.
- **Slack summary extended** to surface the new captures (e.g. attachments added, agency
  awards/bids added) alongside the existing sync/export line. `notify.summarize` stays pure and
  unit-tested; the new fields come from `db.counts()` deltas already computed.
- **Honest expectation:** "all sources ok" will be rarer. A flaky browser step will routinely show
  a failed line while sync/export/publish stay green. That visibility is the point, not a
  regression.

## 4. Retiring the dead scrape path (surgical)

`bid_award_panel.py` is **not** "the dead Bid Award Panel scraper" — it is the shared TMMIS agenda
infrastructure. The dead slice is narrow and must be excised without touching the shared code.

**Dead — remove:**
- The **`--scrape` flag on `enrich-titles`** and the branch in `_cmd_enrich_titles` that calls
  `scrape_agendas(config.COUNCIL_AGENDAS_DIR, …)` to fetch Bid Award Panel agendas. Nothing it can
  fetch is new.
- The **BA/BD default for `term_starts`** on `scrape_agendas` / `discover_meetings`: make
  `term_starts` an explicit required argument. Every live caller already passes its own
  (`zoo_board`, `ep_board` pass their body's terms), so removing the default deletes only the dead
  BA/BD path while keeping the prober. The `TERM_STARTS` constant is removed with it.

**Shared / live — keep untouched:**
- `scrape_agendas`, `discover_meetings`, `cached_agendas`, `parse_agenda_pdfs` — imported by
  `zoo_board.py` and `ep_board.py` for the live agency captures (steps 5).
- All offline ingest — `store_items`, `parse_bid_tables`, `store_bids`, `match_pre_ariba_titles`,
  `parse_composite_appendices`, `store_composite_awards`, `match_composite_titles`, … — the code
  that turns the 891 cached agendas into titles, the 17,604 bids, background PDFs, and composite
  awards.
- The **offline `enrich-titles` command** (without `--scrape`) — retained for maintenance and for
  provisioning a fresh box from the preserved archive (§5).

Tests that exercise the `--scrape` path are removed or repointed to the offline path; the many
tests over the pure parsers are unaffected (they read fixtures, never scrape).

## 5. Preserving the 891 agendas as a data asset

Removing `--scrape` removes the only tool path that could re-fetch the Bid Award Panel agendas. On
plexbox they are on disk (`<DATA_DIR>/council/agendas/`, 891 files, 116 MB) and in the legacy
rescue, but a fresh machine would have no way to obtain them. They are **primary sources** the City
can remove at any time, so the archive preserves them — independent of the already-published
`bids.sqlite` (which holds only the *derived* rows).

- A dedicated, **immutable** GitHub release `council-agendas` on `CivicTechTO/toronto-bids-data`
  carries `council-agendas.zip` (the full cached corpus). Not the rolling `latest` release — this
  is a one-time archival artifact, not nightly-churning data.
- **`publish-data.sh` gains an idempotent guard:** if the `council-agendas` release already carries
  the bundle (`gh release view council-agendas` shows the asset), skip entirely — one cheap API
  check per night, no 116 MB re-upload. Build the zip from `<DATA_DIR>/council/agendas/` and upload
  only when the asset is absent (first run, or a fresh data repo). The corpus is final, so after
  the first upload this is always a no-op.
- **README documents the provisioning path** as the replacement for `--scrape`: download and unpack
  `council-agendas.zip` into `<DATA_DIR>/council/agendas/`, then run offline `tb enrich-titles` to
  re-derive titles/bids/composite — no browser, no scrape.

Scope note: this preserves the **final BA/BD corpus**. The live Zoo/TRCA/EP agendas keep accruing
on disk (a rolling corpus); archiving those is a possible later extension, not part of this change.

## 6. Data flow

`tb nightly` → sync/award-summaries/portal (HTTP) → Ariba attachments (browser) → Zoo/TRCA/EP board
reports (browser) → [1st only] enrich-council (browser) → build_supplier_dimension → export →
wrapper runs publish-data.sh → rolling `latest` release (+ idempotent `council-agendas` archive).

## 7. Error handling

- Per-step isolation in `_cmd_nightly` (a browser step failing never stops sync/export/publish),
  recorded to `sync_run`, surfaced in the Slack line, and making the unit exit non-zero.
- Browser prerequisites missing (Xvfb/Chromium/creds) degrade to a recorded step failure, not a
  crash — the plain-HTTP archive and its export still ship.
- `publish-data.sh` keeps its existing guards (artifact exists, valid JSON, `gh auth status`); the
  new agenda-archive guard failing is non-fatal to the data publish (best-effort, like the frontend
  trigger) — the nightly export is the deliverable, the one-time archive is not.

## 8. Testing

- `_cmd_nightly` step ordering and isolation: a unit test that stubs each capture and asserts a
  raised failure in a browser step still reaches export and is recorded (extends the existing
  nightly isolation tests; browser code is stubbed, never driven).
- `enrich-titles` no longer accepts `--scrape`: assert the flag is gone and the offline path still
  ingests cached-agenda fixtures.
- `scrape_agendas` / `discover_meetings` require `term_starts`: the Zoo/EP callers still pass
  theirs; a call without it is a `TypeError` (covered by the existing agency tests exercising the
  real signature).
- `publish-data.sh` agenda-archive guard: dry-run shows the `council-agendas` upload only when the
  release lacks the asset, and skips when present.
- **Live verification (per the #136/#138 discipline):** after wiring, run the unit once on plexbox
  and confirm from the journal that every step ran, failures (if any) were isolated, the export and
  publish still completed, and the `council-agendas` archive exists. Browser discovery stays
  untested by unit tests, as elsewhere.

## 9. Out of scope

- `enrich-titles --scrape` resurrection or any BA re-scrape (dead by history).
- Archiving the live Zoo/TRCA/EP agenda corpus (rolling; a later extension).
- Shell-side orchestration of the capture steps (kept in Python for the summary's sake).
- Any change to the publish destination or the CORS matter (frontend concern, `toronto-bids-frontend#2`).

## 10. Recording

On completion: update `deploy/README.md` (the new nightly scope, the retired Ariba timer, the
monthly council gating, the agenda-archive provisioning path) and note the reversed principle in
the deployment design's lineage. The retired `deploy/tb-ariba-attachments.{service,timer}` are
removed from the repo.
