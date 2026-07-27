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
| **The row list is VIRTUALISED as a sliding window.** The DOM holds a fixed 51 checkboxes, but *which* rows they are changes with scroll position — at the top of the list row 9 is `4.1 Form A`, after scrolling to the end it is `5 Part 5 - Pricing Form`. `Selected Items: 85` exceeds the 51 rendered. | **Row indices are not stable** — not across runs, not even across scrolls within a run. Rows must be keyed by their **outline number** (`5.2.1.2.1.3`), and enumeration requires scrolling the whole list and de-duplicating. A naive "did the checkbox count grow?" test reports NO and is misleading. |
| **Only the top `Title` checkbox cascades.** Ticking `5 Part 5 - Pricing Form` gave `checked 0→1, total 0.0→0.0`. | Part rows select only themselves and carry no size. Per-Part batching was never viable; row-by-row is the only option. Attachments hang off specific leaf rows. |
| Element handles **detach** after every selection — the picker re-renders | Never hold a handle across a selection. Re-resolve the locator each time, and scroll the target into view before clicking. |
| A single-row toggle settles in **~1.5 s**; the select-all cascade takes ~10 s | An ~85-row pass costs 2–3 minutes. Affordable, and only for events over the ceiling. Poll for settling; never sleep a guess (#174). |
| `Total Number` reads **0 when nothing is selected** | It is a property of the *selection*, not of the event. It is only meaningful at full selection, which constrains what the fingerprint can be built from. |
| Respond dies at close | Partial progress must survive between runs. |

## Architecture

The batching loop is inherently interactive, and the browser half of this module has no test
coverage — which is exactly how #174 survived eight nights. So all the logic goes above one
narrow interface, and only a thin adapter touches Playwright.

```
Picker (protocol, 5 methods)
    row_keys()               -> list[str]   # outline numbers, in order; scrolls to enumerate
    set_selected(key, bool)  -> None        # scrolls into view, ticks/unticks, waits to settle
    total_mb()               -> float | None
    file_count()             -> int         # the picker's 'Total Number' for the SELECTION
    download_to(path)        -> Path
```

**Keys, not indices.** The list is virtualised, so `nth(i)` addresses the rendered window rather
than the logical row. Every method that names a row names it by outline number (`5.2.1.2.1.3`),
and `AribaPicker` resolves that to a live locator at the moment of use — re-resolving each time,
because the picker re-renders and detaches handles after every selection.

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
run 1:  enumerate row keys (scrolling the virtualised list) -> measure row-by-row
        -> plan = [["1.1","2.1",...], ["5.2.1.3.1",...], ...]   (outline keys, NEVER indices)
        -> manifest.json, then download each batch -> batch-01.zip, batch-02.zip, ...
run 2:  fingerprint matches -> load plan -> download only batches whose zip is absent
        all present -> merge -> Doc<n>.zip -> store_bundle -> delete .partial/
```

Working state lives outside the canonical namespace so nothing else sees it:

```
<ARIBA_ATTACHMENTS_DIR>/.partial/Doc<n>/
    manifest.json     {fingerprint, batches: [{rows, mb}], omitted: [...]}
    batch-01.zip
```

**Fingerprint** = `(ordered list of row keys, file_count-at-full-selection, total_mb-at-full-selection)`.
The two counts are properties of the *selection*, not the event — `Total Number` reads 0 with
nothing ticked — so they are captured during the initial select-all that detects the overflow in
the first place, which the flow already performs. The row-key list is the primary signal and is
readable without selecting anything.

A mismatch means the City changed the event (this one gained four addenda while we were looking at
it — `6.1` through `6.4`), so the plan is stale: discard the partials and re-plan, rather than
merge batches from two different versions of the same event.

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
- **Cascade — probed and resolved (2026-07-27).** The open question was whether unticking a Part
  header fully unticks its children. It does not arise: **only the top `Title` checkbox cascades
  at all.** Ticking `5 Part 5 - Pricing Form` moved `checked 0→1` and left `total` at `0.0`, so an
  intermediate row selects only itself and contributes no size. No clear-and-reselect step is
  needed, and per-Part batching (approach B) is not merely unoptimised but unavailable.
- **Enumeration must scroll.** Because the list is virtualised, `row_keys()` scrolls from top to
  bottom accumulating outline numbers and de-duplicating. Enumerating only what is rendered would
  silently plan over ~51 of ~85 rows — the failure would look like a successful capture that is
  quietly missing files, which is the worst shape of bug for an archive.

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
| stale fingerprint (row keys changed) discards and re-plans | manifest |
| **plan is keyed by outline number, never row index** | `FakePicker` whose index→key mapping shifts between calls, mimicking the sliding window |
| merge preserves CRCs; collision refuses; count check fires | fixture zips |

The virtualisation test matters most: a `FakePicker` that deliberately reshuffles which index maps
to which key is the only thing standing between us and a capture that silently plans over the
wrong rows.

One live step remains: an **end-to-end capture of `Doc5713434353`**, which doubles as the
acceptance test — a 792 MB event in roughly two batches is precisely the case. (The cascade probe
is done; its findings are folded into the constraints table above.)

## Out of scope

- **Coarse (per-Part) measurement**, approach B in the brainstorm: ~8 measurements instead of ~85.
  Left unbuilt until row-by-row proves too slow. Its fallback for an oversized Part *is* the
  row-by-row loop, so nothing here is wasted if it is added later.
- **Any schema change.** `omitted`/incomplete state lives in JSON beside the bundle. No new column,
  no new table — consistent with `ariba_attachment` being a derived index of the on-disk zips.
- **Per-file (rather than per-subset) download.** The picker downloads a selection; there is no
  per-file href to exploit.
