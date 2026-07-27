# Capturing Ariba events over the 500 MB single-zip ceiling (#174)

**Delivers:** attachment capture for solicitations whose bundle exceeds Ariba's single-zip limit,
by selecting subsets of the picker, downloading each as its own zip, and merging them into the
one canonical `Doc<n>.zip` the rest of the pipeline already understands.

**Status:** design approved 2026-07-27. Deadline-bound — the event that motivated it closes
**2026-08-07**, after which Respond is disabled and its documents are unrecoverable.

## Why

`tb enrich-ariba-attachments --capture` downloads one server-zipped bundle per event. Ariba
hard-stops that at 500 MB and says so itself, in the picker:

> The file size of the selected attachments exceeds the limit of 500 MB. **You must select
> specific items and perform multiple downloads.**

`Doc5713434353` (Overlea Blvd Bridge RFT) is the first event to hit it: **792.41 MB, 54 files,
largest single file 88.7 MB**. Until now `capture_event` logged it and skipped — the `# ponytail:`
note in that function marked per-Part download as the upgrade path, unbuilt until an event needed
one. One does.

This is not a backfill. **Respond is disabled the moment a posting closes**, so an event missed
before its close date is gone permanently. That single fact drives most of the decisions below.

### What this is NOT fixing

Two separate #174 defects were fixed first and are already merged/verified; neither is part of
this design:

- `_select_all_attachments` slept a flat 3000 ms against a cascade measured at **~10.3 s**, so a
  working click read as a dead checkbox and the retry loop re-clicked mid-cascade, restarting it.
- `_selected_total_mb` matched a literal space in `Total Size (MB)` while the DOM carries
  **non-breaking spaces**, so the 500 MB guard read `None` and never fired.

With both fixed, the event now skips cleanly (`bundle 792 MB > 500 MB single-zip limit`). This
design turns that clean skip into a capture.

## Constraints, measured not assumed

| observation | consequence |
|---|---|
| The picker table has **only a Title column** — no per-row size | Batches cannot be pre-computed. The only way to learn a selection's size is to select it and read the summary. |
| `Total Size (MB): 792.41`, `Max Size (MB): 88.7`, `Total Number: 54`, `Selected Items: 85` | Rows ≠ files: Part-header rows tick their children. Bookkeeping must count **files**, not ticked rows. |
| The select-all cascade takes ~10 s and scales with item count | Every selection change is an async recompute. Poll for it; never sleep a guess (#174). |
| Respond dies at close | Partial progress must survive between runs. |

## Architecture

The batching loop is inherently interactive, and the browser half of this module has no test
coverage — which is exactly how #174 survived eight nights. So all the logic goes above one
narrow interface, and only a thin adapter touches Playwright.

```
Picker (protocol, 5 methods)
    row_count()            -> int
    set_selected(i, bool)  -> None     # ticks/unticks, waits for the recompute to settle
    total_mb()             -> float | None
    file_count()           -> int      # the picker's 'Total Number', NOT ticked rows
    download_to(path)      -> Path
```

- **`AribaPicker`** — the real implementation over `page`. Thin: locator calls plus the
  poll-don't-sleep discipline.
- **`FakePicker`** — tests, with a known per-row size table. No browser.
- **`download_in_batches(picker, ceiling_mb, dest_dir, log)`** — the greedy loop. All real logic,
  fully unit-testable because it only touches those five methods.
- **`merge_bundles(part_paths, target)`** — pure file operation over zips.

This is the split the module already uses elsewhere: `form_rows` does I/O and
`parse_award_summary(rows)` is pure over them (#116); `index_zip` is pure over bytes (#123).

`capture_event` gains one branch; the existing path is untouched:

```
total <= ceiling  -> existing single download        (unchanged)
total >  ceiling  -> capture_oversized_event(...)     (new)
```

**`capture_attachments`' skip check stays exactly as it is** (`Doc<n>.zip` exists). The canonical
zip is written only on completion, so a partially-captured event simply remains in the pending
list and resumes next run. No new state for the caller to understand.

## Plan-then-replay

Measuring is the expensive part, so the first run measures and writes a *plan*; later runs execute
only what is missing.

```
run 1:  measure row-by-row -> plan = [[rows 0-8], [rows 9-14], ...] -> manifest.json
        download each batch -> batch-01.zip, batch-02.zip, ...
run 2:  fingerprint matches -> load plan -> download only batches whose zip is absent
        all present -> merge -> Doc<n>.zip -> store_bundle -> delete .partial/
```

Working state lives outside the canonical namespace so nothing else sees it:

```
<ARIBA_ATTACHMENTS_DIR>/.partial/Doc<n>/
    manifest.json     {fingerprint, batches: [{rows, mb}], omitted: [...]}
    batch-01.zip
```

**Fingerprint** = `(row_count, file_count, total_mb)`. A mismatch means the City changed the event
(an addendum) and the plan is stale: discard the partials and re-plan, rather than merge batches
from two different versions of the same event.

Two files hold "what is missing", and they are not the same thing:

- `.partial/Doc<n>/manifest.json` is **working state** — the plan, which batches are done, and any
  items found un-capturable while planning. It is deleted when the bundle completes.
- `Doc<n>.omitted.json`, beside the canonical bundle, is the **durable record** — written at
  completion (or at forced completion on close), naming what the bundle does not contain and why.
  It outlives the capture, so a gap is greppable later without reading logs.

Batches merge in plan order (`batch-01`, `batch-02`, …), which is row order, so the merged bundle's
member order matches what a single download would have produced.

### The loop

Tick a row, read the total; if it crosses the threshold, untick it, flush the batch, and retry
that row as the start of the next one.

**Threshold is 450 MB, not 500.** Ariba's limit applies to its own computed total, and the one
event we cannot re-run is not where we want to discover the boundary condition.

### Edge cases — refuse rather than invent

- **A single item over the ceiling** can never be captured. Log it by name, record it in
  `omitted`, and let the bundle complete without it — otherwise the event is permanently
  un-completable and we re-drive a browser over it nightly forever. The gap is durable in
  `Doc<n>.omitted.json` beside the bundle, not just a log line. (`Max Size: 88.7 MB` today, so
  this does not bite yet.)
- **`total_mb()` returning `None`** aborts the batched capture outright. #174 was a guard that
  had gone blind; a batcher that cannot measure must not guess.
- **Part-header cascade asymmetry — an open question, resolved by probe before implementation.**
  Header rows tick their children (85 selected vs 54 files). It is **not yet known whether
  unticking a header fully unticks its children**; if it does not, a flushed batch leaks into the
  next. Probe: tick a Part header, read the total, untick it, confirm the total returns to zero.
  If asymmetric, the loop does a full `clear_selection()` after each flush and re-selects the next
  batch explicitly — costlier, but known in advance rather than discovered mid-implementation.

## Merge

`merge_bundles` streams every top-level member of each batch zip into one `Doc<n>.zip` via
`shutil.copyfileobj` through `zipfile.open(..., 'w')`, rather than reading 88 MB files into
memory. Leaf bytes are unchanged, so CRCs are unchanged, so `index_zip` yields exactly what a
single download would have. Ariba's nested `Appendix *.zip` members pass through untouched.

Merging the batches' top-level members **reproduces the layout a single download would have
produced**, which is why the result is indistinguishable from the other 49 bundles and needs no
change to `store_bundle`, `reindex_bundles`, or the export.

A **name collision between batches refuses** and leaves the partials rather than overwriting.
Batches are disjoint row sets so it should not happen; if it does, that must surface, not silently
cost a file.

**Count check:** compare merged top-level members against the picker's `Total Number` (54) — *not*
against `index_zip`'s leaf count, which is larger because it recurses into nested zips. A shortfall
is logged loudly and recorded in `Doc<n>.omitted.json`, not silently accepted.

## Failure handling

- **A batch download fails** → partials stay, log, return `None`. `Doc<n>.zip` is absent, so the
  event stays pending and resumes next run.
- **The posting closes with batches in hand → merge and store what we have, marked incomplete.**
  This is the entire point of keeping partials: Respond dies at close, so 3 of 5 batches is
  permanently better than nothing. Without this rule the partial-capture design buys nothing.
  The trigger is the existing `respond.is_disabled()` check at the top of `capture_event`: today
  that logs "Respond disabled (closed) — skipped" and returns `None`. It gains one step — if
  `.partial/Doc<n>/` holds any batch zip, merge and store those before returning, then record the
  shortfall. This is the only place the closed-event path changes.
- **Merge collision, or an unmeasurable total** → refuse, keep partials, log.
- Transient disk cost is ~2× the bundle during merge.

## Testing

All offline, no browser:

| target | via |
|---|---|
| batch composition; threshold never exceeded | `FakePicker` with a known size table |
| single item over ceiling → omitted, not infinitely retried | `FakePicker` |
| `total_mb()` → `None` aborts rather than guesses | `FakePicker` |
| plan replay skips batches already on disk | manifest + tmp zips |
| stale fingerprint discards and re-plans | manifest |
| merge preserves CRCs; collision refuses; count check fires | fixture zips |

Two live steps, in order: the **cascade-symmetry probe** before writing the loop, and one
**end-to-end capture of `Doc5713434353`**, which doubles as the acceptance test — a 792 MB event
in roughly two batches is precisely the case.

## Out of scope

- **Coarse (per-Part) measurement**, approach B in the brainstorm: ~8 measurements instead of ~85.
  Left unbuilt until row-by-row proves too slow. Its fallback for an oversized Part *is* the
  row-by-row loop, so nothing here is wasted if it is added later.
- **Any schema change.** `omitted`/incomplete state lives in JSON beside the bundle. No new column,
  no new table — consistent with `ariba_attachment` being a derived index of the on-disk zips.
- **Per-file (rather than per-subset) download.** The picker downloads a selection; there is no
  per-file href to exploit.
