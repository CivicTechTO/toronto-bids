# Per-file capture of Ariba solicitation documents (#174)

**Delivers:** attachment capture that downloads each document individually from the Sourcing
event's content tree, replacing the server-zipped-bundle path and retiring the 500 MB ceiling as
a concern entirely.

**Status:** design approved 2026-07-27. The event that motivated it closes **2026-08-07**.

## Why the bundle path is not enough

`tb enrich-ariba-attachments --capture` drives **Download Content → Download Attachments**, which
server-zips a *selection*. Ariba hard-stops that at 500 MB, and #174 built batched capture to
split an oversized selection into sub-ceiling groups. Measured live, that cannot work here:

```
row 3.1 "Part 3 - Specifications and Drawings are attached."
    selected alone:  Total Size 787.71 MB   Max Size 88.7 MB
    Download Attachments button: DISABLED
    "The file size of the selected attachments exceeds the limit of 500 MB."
```

**Row `3.1` is atomic within the picker** — no disclosure control expands it (probed: the six
candidates are help-centre chrome and a hint box) — and it holds **99.4% of the bundle's bytes**.
Everything else in the solicitation totals 4.7 MB. Batching splits a selection; it cannot split a
row. So the batched path can deliver at most 0.6% of this event, and that 0.6% excludes the
specifications and drawings, which are the substance of a bridge-rehabilitation tender.

### The route that does work

That was briefly recorded as a structural impossibility. **It is not**, and the correction matters
enough to state plainly: it was a claim about the one surface we had ever driven. The event page's
`All Content` view exposes every document individually:

```
1.1  Part 1 - RFT Process is attached.        [Part 1 - RFT Process.pdf ▾]
       ▾ menu: "Download this attachment" / "Download all attachments"
2.1  ...                                       [Part 2 - Construction Agreement_A1.pdf ▾]  References⌄
3.1  ...                                       [Part 3 - Drawings and Specifications.pdf ▾] References⌄
```

`Max Size (MB): 88.7` across `Total Number: 54` — **every individual file is small.** Nothing about
the content is too large; only the bundle path's granularity was wrong. Row `3.1`'s 787.71 MB is
many files, and `References⌄` is where they live.

This is the same error the codebase already warns about in #117 ("*a claim about where we looked*",
and the retracted "largest content gain"). Recording it here so the next reader inherits the
correction rather than the claim.

## Decisions taken

| decision | choice | why |
|---|---|---|
| scope | **per-file always**, bundling retired from the capture path | one acquisition path, exercised nightly; the ceiling stops mattering. Cost is bounded: capture is incremental (`Doc<n>.zip` exists → skipped), so per-file only runs on newly-opened solicitations |
| completeness | **cross-check, record mismatch, do not refuse** | Respond dies at close, so bytes beat strictness |
| zip layout | **flat filenames**, `_2`/`_3` suffix on collision | keeps the corpus uniform with the 49 Ariba-built bundles and cannot lose a file. **Accepted tradeoff: Part attribution is lost** |
| acquisition | **per-file via each attachment's ▾ menu** | correctness does not depend on an untested property. Max file 88.7 MB is measured |

## Architecture

The browser half is where every defect in #174 lived, so all logic sits above a narrow interface
and only a thin adapter touches Playwright — the split that made the batching work testable.

```
FileSource (protocol, 3 methods)
    list_files()          -> [{key, name, row}]   # traversal, including expanding References
    download(file, dest)  -> Path                 # saves to EXACTLY dest, or raises
    expected_count()      -> int | None           # the picker's authoritative Total Number
```

**`download` writes to the path it is given and nothing else.** The `.part` staging and the
`os.replace` live in `capture_files`, not in the adapter — atomicity is the property most worth
testing, and the adapter is the half that cannot be unit-tested. So `capture_files` passes
`<name>.part`, and does the rename itself once `download` returns.

- **`AribaFileSource`** — real, over `page`. Tree traversal, menu open, click the **visible**
  `Download this attachment`, catch the download.
- **`FakeFileSource`** — tests, with a known file list. No browser.
- **`capture_files(source, document_number, dest_dir, log)`** — acquisition loop, resume, count
  cross-check. Pure over the protocol.
- **`build_bundle(files, target)`** — zips flat with the collision suffix.

**A new acquisition path feeding the existing storage path.** `build_bundle` produces exactly the
artifact `store_bundle` already takes, so `index_zip`, the `ariba_attachment` index,
`reindex_bundles`, the export's `documents` array and the "already archived" check are **unchanged**.
Nothing downstream learns the bytes arrived differently.

`capture_event`:

```
respond disabled  -> salvage partials, else skip     (unchanged)
otherwise         -> capture_files(...)               (replaces the picker download)
```

**The picker survives in one reduced role.** `expected_count()` opens it solely to read
`Total Number` and never downloads. That preserves an independent ground truth — the page tree
could hide a file behind an unexpanded `References` and the traversal would never know.

## The acquisition loop

**Traversal.** Navigate to `All Content`, expand every `References⌄`, collect `{key, name}` per
file. It must not assume the tree renders fully without scrolling, nor that `References` expands
synchronously: scroll and expand until repeated passes find nothing new, and **log what was
found**, as `_log_geometry` now does for the picker. A traversal that quietly sees 6 rows instead
of 60 is the failure that matters.

**Download.** Open the file's ▾ and click the **visible** `Download this attachment`. A probe
timed out here by matching one of three hidden copies with `.first` — scope to the open menu.

**Atomicity.** Download to `<name>.part`, then `os.replace` to `<name>`. A file present on disk is
therefore complete *by construction*, so resume is simply "skip what is already there" — no
sidecar needed, because here the filename **is** the identity, unlike batching where a positional
count was not.

```
.partial/Doc<n>/
    files/Part 1 - RFT Process.pdf          complete
    files/Appendix C2 drawings.pdf.part     interrupted -- deleted, not resumed
    manifest.json                           fingerprint + expected_count
```

**Fingerprint** = the sorted list of file names from `list_files()` plus `expected_count`. A
mismatch means the City changed the event between runs (this one gained four addenda while we were
looking at it), so the partials describe a different version: discard and re-traverse rather than
mix two versions into one bundle. Same rule, and same reason, as the batching design.

**`capture_files` returns the canonical bundle path on completion, or `None`** when it captured
nothing usable and the event should stay pending. It raises only for the zero-files case, which is
a distinct condition — "the event withheld its content" is not "the event is not ready yet".

## Failure handling

- **One file failing does not abort the capture.** Files are independent: log it, record it, and
  continue. In batching a failed batch meant retry-the-batch; here it means lose-one-file, and
  losing one beats losing all.
- **A failed download's `.part` is deleted immediately.** An incomplete download is never useful
  (it will be re-fetched), it can be 88 MB, and this is the pattern the batching review already
  established for failed batch zips. No cleanup pass, nothing accumulates.
- **Respond closes mid-capture** -> build from what is on disk, record incomplete.
- **Zero files found -> raise, no bundle written.** This is the `Review Prerequisites` gate case:
  an event withholding its content would traverse to nothing, and an empty `Doc<n>.zip` would mark
  it archived forever. **Empty is never success.**
- **Count mismatch** -> build the bundle, write `Doc<n>.omitted.json` with expected, actual and the
  captured names.
- **`build_bundle` writes to `.tmp` then `os.replace`s**, so `Doc<n>.zip` either does not exist or
  is complete. A reviewer caught exactly this defect in the batching merge; inheriting the fix is
  cheaper than rediscovering it.

### The count check is PROVISIONAL until one live measurement

`Total Number` is the picker's count of *attachments*; the traversal counts *files found in the
tree*. **It is not yet established that these count the same things** — if the picker counts a
nested archive's members while the tree shows one file, the check reports a permanent phantom
mismatch. Since we record rather than refuse, a phantom cannot block a capture, but it would write
a **false gap record**, which in an archive whose value is honest gap records is worse than no
check at all.

So: the check ships, and the first live run validates *the check itself* against the known 54. If
the numbers prove incommensurable, fix the comparison or drop it — do not leave it emitting noise.

## Testing

All offline, no browser:

| target | via |
|---|---|
| resume skips files already complete on disk | `FakeFileSource` + tmp files |
| one file failing does not abort the rest | `FakeFileSource` raising on one |
| zero files found -> raises, no bundle written | `FakeFileSource`, empty list |
| count mismatch records rather than refuses | `FakeFileSource` + expected_count |
| flat naming, `_2`/`_3` suffix, deterministic in traversal order | duplicate names |
| bundle atomic; never partial at the target | interrupted `build_bundle` |
| bytes/CRCs unchanged so `index_zip` matches | fixture files |

Then one live capture of `Doc5713434353`, which doubles as the count-check validation.

## Out of scope

- **Retiring `ariba_batch.py`.** It stays on disk, tested, unused by the new path. Deleting the old
  mechanism in the change that introduces its replacement is how you end up with neither. A
  follow-up removes it once per-file has actually captured something.
- **The per-row `Download all attachments` optimisation.** ~3 clicks instead of ~54, and it slots
  in behind the same interface — but row `3.1`'s attachments total 787.71 MB, so it plausibly hits
  the identical ceiling. It renders enabled, which this project has learned is not evidence.
  Measure it later; do not trade a working capture for a convenient one.
- **Part attribution**, lost by the flat-naming decision above.

## Known risk

The traversal is new browser code against a widget that was wrong six times in one session. The
seam keeps the *logic* testable, but the traversal itself is verifiable only by running it — the
same position `AribaPicker` was in. Expect a handful of live runs, not one.
