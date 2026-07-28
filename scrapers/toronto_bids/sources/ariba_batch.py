"""Batched capture for Ariba events over the 500 MB single-zip ceiling (#174).

Ariba hard-stops a single bundle at 500 MB and says so in the picker: "You must select
specific items and perform multiple downloads." This module is the "select specific items"
half -- pure logic over a 5-method Picker protocol, so it is testable without a browser.
The Playwright adapter lives in ariba_attachments.py.

**A batch's identity is the sidecar beside it, never its position in a list.** Three fix
rounds all traced back to the same thing: a batch file was named `batch-{len(done)+1:02d}.zip`,
so what a file *was* had to be inferred from a count held in memory. Everything that count
could disagree with disk went wrong in turn -- a parts list derived from the manifest's count
excluded an orphaned batch and then deleted it; a count restarting at 1 overwrote a good
orphan; a count-indexed done-list was trusted with no zip behind it. And the scheme blocked
the obvious repair: you cannot drop a bad batch from the list, because that shifts every later
index onto a *good* later batch's filename.

So `batch-NN.json` is written BEFORE `batch-NN.zip` and names exactly the rows that zip is
meant to hold. The directory then states what it contains instead of implying it, and every
question is answered by reading it (`scan_batches`): a batch is complete iff its sidecar and
its zip are both there and the zip opens; a sidecar with no valid zip is an interrupted batch
whose rows are simply owed; the next batch number is one past the highest on disk. There is no
in-memory count left to disagree with, and no second record of the same fact -- `manifest.json`
keeps only what disk cannot state: the fingerprint, and the rows found un-capturable.

**Live and salvage are different policies over that directory.** While the posting is open a
missing or unopenable batch is simply re-fetched, and the canonical `Doc<n>.zip` is written
only once every planned row is either captured or recorded un-capturable -- writing it around a
recoverable gap would mark an incomplete capture archived forever, which is the one outcome
this whole design exists to prevent. Once the posting closes nothing can be re-fetched, so the
policy inverts: skip what will not open, merge the rest, and record the gap durably.

Design: docs/superpowers/specs/2026-07-27-oversized-ariba-bundle-capture-design.md
"""
import json
import os
import shutil
import zipfile
from pathlib import Path

# Ariba's ceiling is 500 MB against its OWN computed total. 450 leaves headroom -- the one
# event we cannot re-run is not where we want to discover the boundary condition.
BATCH_THRESHOLD_MB = 450

MANIFEST_NAME = "manifest.json"
BATCH_STEM = "batch"


def partial_dir(dest_dir, document_number: str) -> Path:
    """Working directory for an in-progress capture.

    Deliberately OUTSIDE the canonical `Doc<n>.zip` namespace: capture_attachments decides
    what is already archived by testing for that file, so a partial capture must be invisible
    to it. That is what makes "resume next run" need no new caller state.
    """
    return Path(dest_dir) / ".partial" / f"Doc{document_number}"


def make_fingerprint(row_keys, file_count, total_mb) -> dict:
    """Identity of the event as we planned against it.

    The row-key list is the primary signal and is readable without selecting anything; the two
    counts are properties of the SELECTION (`Total Number` reads 0 with nothing ticked) and are
    captured during the select-all that detects the overflow.
    """
    return {"row_keys": list(row_keys), "file_count": file_count, "total_mb": total_mb}


def read_manifest(pdir) -> dict | None:
    """Read the manifest, treating unreadable/corrupt JSON the same as a missing manifest.

    A torn write (a crash mid `write_manifest`, before the atomic replace below lands, or
    corruption at rest) must degrade to "no manifest" rather than raising -- an uncaught
    JSONDecodeError here would abort the whole capture. This degradation is safe, not lossy:
    the manifest no longer records which batches exist (the sidecars do, on disk), so
    `finalise_partial` can still salvage every downloaded batch with no manifest at all. Only
    the `omitted` / `expected_files` bookkeeping is lost, never bytes that can no longer be
    re-fetched.
    """
    path = Path(pdir) / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_manifest(pdir, fingerprint: dict, omitted) -> Path:
    """Write the manifest atomically.

    Holds ONLY what the directory cannot state about itself: the fingerprint the partials were
    planned against, and the rows found un-capturable while planning. It deliberately does NOT
    list the batches -- the sidecars are the record of what exists, and a second copy of that
    fact is exactly what the first three fix rounds kept disagreeing with.

    Atomic all the same (temp file beside the target, then `os.replace()`, the pattern
    `merge_bundles` uses for the bundle itself) so a reader never observes a partial write.
    """
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / MANIFEST_NAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(
        {"fingerprint": fingerprint, "omitted": list(omitted)}, indent=2))
    os.replace(tmp_path, path)
    return path


def manifest_is_current(manifest, fingerprint: dict) -> bool:
    """Whether a manifest was written against this same version of the event.

    A mismatch means the City changed it (an addendum landed) and the partials describe a
    different event: discard and re-plan rather than merge batches from two versions.
    """
    if not manifest:
        return False
    return manifest.get("fingerprint") == fingerprint


def accumulate_batches(picker, row_keys, threshold_mb: float = BATCH_THRESHOLD_MB,
                       skip_keys=(), log=lambda _m: None):
    """Group `row_keys` into selections that each stay under `threshold_mb`.

    **`row_keys` is the plan, and it is given, never fetched.** It is the list the fingerprint
    was built from -- enumerated ONCE, while the select-all was still in place, under
    `row_keys(expected_count=...)`'s short-read guard. This function used to call
    `picker.row_keys()` itself, which could only ever diverge from that list: the guard's
    `expected_count` is unreadable from an empty picker, the page has been through a
    clear_selection since, and a live run duly enumerated 84 rows the first time and 50 the
    second -- a plan over less than two-thirds of the solicitation. Everything downstream (the
    completeness gate in `_finalise_live`, the resume logic, `finalise_partial`'s
    never-attempted diff) is written against the fingerprint's list, so this must be too.

    There is no per-row size in the picker -- the only way to learn a selection's size is to
    select it and measure -- so this ticks one row at a time and reads the summary. Rows are
    addressed by OUTLINE KEY, never by index: the list is virtualised and indices are unstable
    across runs and even across scrolls.

    Returns (batches, omitted). `omitted` names rows that exceed the threshold ALONE and can
    therefore never be captured; they are recorded rather than retried, or the event would be
    permanently un-completable and we would re-drive a browser over it nightly forever.
    """
    skip = set(skip_keys)
    batches, omitted, current = [], [], []

    def measure():
        total = picker.total_mb()
        if total is None:
            raise RuntimeError(
                "could not read the picker's Total Size -- refusing to guess at batch sizes")
        return total

    def omit(key):
        """A row over the threshold ALONE can never be captured, in any batch."""
        omitted.append(key)
        log(f"    row {key}: exceeds {threshold_mb:.0f} MB alone — omitted")

    # `row_keys` is the caller's list, enumerated once under the guard -- there is no second
    # enumeration here to under-read (see the docstring). The picker is only written to and
    # measured from this point on.
    for key in row_keys:
        if key in skip:
            continue
        picker.set_selected(key, True)
        total = measure()
        if total <= threshold_mb:
            current.append(key)
            continue

        # Over the line. Back this row out and decide what it means.
        picker.set_selected(key, False)
        if not current:
            omit(key)
            continue

        batches.append(current)
        for done in current:
            picker.set_selected(done, False)
        current = [key]
        picker.set_selected(key, True)
        if measure() > threshold_mb:          # alone it still does not fit
            picker.set_selected(key, False)
            omit(key)
            current = []

    if current:
        batches.append(current)
        for done in current:
            picker.set_selected(done, False)
    return batches, omitted


def merge_bundles(part_paths, target, expected_files: int | None = None,
                  log=lambda _m: None):
    """Merge each batch zip's TOP-LEVEL members into one canonical bundle.

    Streams member-to-member rather than reading whole files into memory -- a single
    attachment here reaches 88.7 MB. Bytes are unchanged, so CRCs are unchanged, so index_zip
    yields exactly what a single download would have; Ariba's nested `Appendix *.zip` members
    pass through untouched. Merging top-level members REPRODUCES the layout a single download
    would have produced, which is why store_bundle / reindex_bundles / the export need no
    change at all.

    Builds into a temporary path beside `target` and moves it into place only on success, so
    `target` is never observed in a partial state. Without this, a collision on a later batch
    (or a disk error, or an interrupted run) would still leave Python closing the ZipFile
    during stack unwind -- writing a proper central directory over whatever was copied so far.
    That result is readable and testzip()-clean, which is indistinguishable from a finished
    capture to capture_attachments' "does Doc<n>.zip exist" check, so an aborted merge would
    never be retried: silent, permanent data loss.

    Returns (target, merged_member_count).
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_suffix(target.suffix + ".tmp")
    seen: dict[str, str] = {}

    try:
        with zipfile.ZipFile(tmp_target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:
            for part in part_paths:
                part = Path(part)
                with zipfile.ZipFile(part) as src:
                    for info in src.infolist():
                        if info.is_dir():
                            continue
                        if info.filename in seen:
                            raise RuntimeError(
                                f"name collision merging {part.name}: {info.filename!r} already "
                                f"came from {seen[info.filename]} — refusing to overwrite")
                        seen[info.filename] = part.name
                        # Pass the source ZipInfo, not the bare filename, so date_time and
                        # compress_type carry across instead of resetting to the archive
                        # default -- bytes/CRC are untouched either way.
                        with src.open(info) as fsrc, out.open(info, "w") as fdst:
                            shutil.copyfileobj(fsrc, fdst, 1 << 20)
    except BaseException:
        tmp_target.unlink(missing_ok=True)
        raise

    os.replace(tmp_target, target)

    count = len(seen)
    # One-sided is not enough here: we know exactly how many files Ariba said it had. Compare
    # against the picker's `Total Number`, NOT index_zip's leaf count, which is larger because
    # it recurses into nested zips.
    if expected_files is not None and count != expected_files:
        log(f"    merged {count} files, picker reported {expected_files} — shortfall recorded")
    return target, count


def write_omitted(bundle_path, omitted, expected_files, actual_files,
                  unreadable=()) -> Path | None:
    """Durable record of what a bundle does NOT contain, beside the bundle itself.

    `omitted` is homogeneous: **row outline keys throughout** (`5.2.1.3.1`), never filenames.
    A dead batch used to be recorded as `"batch-02.zip"` in this same array, which made it
    unreadable as data -- the sidecars now say which rows a dead batch held, so those rows are
    what gets recorded.

    `unreadable` is the residue that names no rows at all: a batch file left behind by
    something that wrote no sidecar (a pre-sidecar partial directory, or foreign debris) and
    that will not open. We cannot say which rows it held, so it is recorded separately rather
    than smuggled into `omitted` as a filename -- the gap stays durable without the array
    losing its meaning.

    Written only when something is actually missing. It outlives the capture (the .partial
    manifest does not), so a gap is greppable later without reading logs.
    """
    unreadable = list(unreadable)
    if not omitted and not unreadable and expected_files == actual_files:
        return None
    bundle_path = Path(bundle_path)
    path = bundle_path.with_suffix(".omitted.json")
    body = {"omitted": list(omitted), "expected_files": expected_files,
            "actual_files": actual_files}
    if unreadable:
        body["unreadable_batches"] = unreadable
    path.write_text(json.dumps(body, indent=2))
    return path


# --- batch identity: the sidecar, not a position ------------------------------------------

def _batch_path(pdir, n: int) -> Path:
    return Path(pdir) / f"{BATCH_STEM}-{n:02d}.zip"


def _sidecar_path(pdir, n: int) -> Path:
    return Path(pdir) / f"{BATCH_STEM}-{n:02d}.json"


def _batch_number(path) -> int | None:
    """The NN in `batch-NN.zip` / `batch-NN.json`, or None for anything else.

    Deliberately strict: `batch-01.json.tmp` (a torn sidecar write) has stem `batch-01.json`,
    whose digit part is not a number, so it is not mistaken for a batch.
    """
    stem = Path(path).stem
    head, _, digits = stem.partition("-")
    if head != BATCH_STEM or not digits.isdigit():
        return None
    return int(digits)


def write_sidecar(pdir, n: int, row_keys) -> Path:
    """Record which rows batch `n` is about to hold -- BEFORE the zip is downloaded.

    Sidecar-first is what makes a hard crash recoverable rather than ambiguous: a sidecar with
    no valid zip beside it says exactly which rows are still owed, where a bare orphaned zip
    says only that *something* was downloaded once.
    """
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    path = _sidecar_path(pdir, n)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps({"row_keys": list(row_keys)}, indent=2))
    os.replace(tmp_path, path)
    return path


def read_sidecar(path) -> list[str] | None:
    """The row keys a sidecar names, or None if it does not name any readably.

    None is "this file identifies nothing", which is treated exactly like no sidecar at all --
    never like an empty batch, which would silently mark rows captured that are not.
    """
    try:
        body = json.loads(Path(path).read_text())
    except (json.JSONDecodeError, OSError):
        return None
    rows = body.get("row_keys") if isinstance(body, dict) else body
    if not isinstance(rows, list) or not all(isinstance(k, str) for k in rows):
        return None
    return rows


def _openable_zip(path) -> bool:
    """Whether `path` opens as a zip with a readable central directory.

    Deliberately NOT `testzip()`: that decompresses every member to verify its CRC, which is
    ruinous on a bundle whose members reach 88.7 MB. `zipfile.ZipFile()` already parses the
    central directory on open (it seeks from EOF to find it), so a truncated or corrupt file
    fails right here -- that is enough to catch debris without paying for a full decompress
    pass.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            zf.infolist()
        return True
    except (zipfile.BadZipFile, OSError):
        return False


def scan_batches(pdir):
    """Read the partial directory and say what is actually in it. Disk is the only authority.

    Returns `(complete, interrupted, unidentified)`:

    - **complete** -- sidecar names its rows AND the zip is there AND the zip opens. These are
      the only batches whose rows may be counted as captured.
    - **interrupted** -- sidecar names its rows but the zip is missing or will not open. Its
      rows are owed: re-downloadable while the posting is open, a durable gap once it closes.
    - **unidentified** -- paths naming no rows: an orphaned `batch-NN.zip` with no readable
      sidecar (a pre-sidecar partial directory, or foreign debris), or a sidecar too corrupt to
      read. Bytes we cannot attribute, which each policy handles differently.

    Batch entries are dicts: `{number, row_keys, sidecar, zip}`.
    """
    pdir = Path(pdir)
    complete, interrupted, unidentified = [], [], []
    claimed: set[int] = set()

    for sidecar in sorted(pdir.glob(f"{BATCH_STEM}-*.json")):
        n = _batch_number(sidecar)
        if n is None:
            continue
        rows = read_sidecar(sidecar)
        if rows is None:
            unidentified.append(sidecar)      # names nothing; its zip stays unclaimed too
            continue
        claimed.add(n)
        zip_path = _batch_path(pdir, n)
        entry = {"number": n, "row_keys": rows, "sidecar": sidecar, "zip": zip_path}
        if zip_path.exists() and _openable_zip(zip_path):
            complete.append(entry)
        else:
            interrupted.append(entry)

    for zip_path in sorted(pdir.glob(f"{BATCH_STEM}-*.zip")):
        n = _batch_number(zip_path)
        if n is not None and n not in claimed:
            unidentified.append(zip_path)

    complete.sort(key=lambda b: b["number"])
    interrupted.sort(key=lambda b: b["number"])
    return complete, interrupted, unidentified


def _next_batch_number(pdir) -> int:
    """One past the highest batch number on disk -- allocated from the directory, not a count.

    Deriving it from `len(done_batches)` is what let a re-plan overwrite a good orphaned batch,
    and what made dropping a bad batch from the middle impossible (every later index would
    shift onto a good batch's filename). Reading disk makes a number, once used, stay used.
    """
    numbers = [n for p in Path(pdir).glob(f"{BATCH_STEM}-*")
               if (n := _batch_number(p)) is not None]
    return max(numbers, default=0) + 1


def _discard_batch(entry) -> None:
    """Remove both halves of a batch, so nothing is left half-describing itself."""
    Path(entry["zip"]).unlink(missing_ok=True)
    Path(entry["sidecar"]).unlink(missing_ok=True)


def capture_in_batches(picker, document_number: str, dest_dir, fingerprint: dict, *,
                       posting_open: bool, threshold_mb: float = BATCH_THRESHOLD_MB,
                       log=lambda _m: None):
    """Download an oversized event in batches and merge them into Doc<n>.zip.

    **`posting_open` must be True and is validated, not assumed.** This function discards
    anything it cannot identify -- a corrupt manifest, an interrupted batch, an orphaned zip --
    which is safe only because an open posting can re-download all of it. Run it against a
    closed posting and that discard is permanent loss, so it refuses outright. The caller has
    the answer already: `posting_open=not respond.is_disabled()`. The closed case belongs to
    `finalise_partial`.

    Resumable: each batch writes its sidecar before its zip, so a failure keeps the bytes it
    already has and the next run re-plans only the rows still owed. Returns the canonical
    bundle path once every planned row is either captured or recorded un-capturable, else None
    -- and because the canonical zip is only written on completion, an incomplete event simply
    stays in capture_attachments' pending list.
    """
    if posting_open is not True:
        raise ValueError(
            f"capture_in_batches requires an OPEN posting (posting_open=True), got "
            f"{posting_open!r} — it discards partials it cannot identify, which is only safe "
            f"while they can be re-downloaded. Use finalise_partial for a closed posting.")

    # The completeness gate in _finalise_live decides whether every PLANNED row is captured or
    # un-capturable by walking fingerprint["row_keys"]. A fingerprint missing that field (or
    # carrying an empty one) makes that walk a no-op -- `missing` comes back empty and the gate
    # degrades from "verify completeness" to "merge whatever is complete", writing the canonical
    # zip around a real gap. Refuse here, at the one place the fingerprint enters a capture,
    # rather than let it silently defang the gate at merge time. make_fingerprint always
    # populates this field, so nothing that plans a fingerprint correctly can trip this.
    if not fingerprint.get("row_keys"):
        raise ValueError(
            f"fingerprint has no row_keys ({fingerprint!r}) — a completeness decision cannot "
            f"be made without knowing what was planned; refusing rather than treating an "
            f"unplannable fingerprint as trivially complete.")

    dest_dir = Path(dest_dir)
    pdir = partial_dir(dest_dir, document_number)
    manifest = read_manifest(pdir)

    if manifest is None:
        # Missing OR corrupt -- either way we cannot trust the fingerprint the partials were
        # planned against, and merging batches from two versions of an event is worse than
        # re-downloading one. Discard and restart clean, exactly like the stale-fingerprint
        # branch below.
        if pdir.exists():
            log(f"  Doc{document_number}: missing/corrupt manifest — discarding partials and "
                f"restarting")
            shutil.rmtree(pdir, ignore_errors=True)
    elif not manifest_is_current(manifest, fingerprint):
        log(f"  Doc{document_number}: event changed since the last run — discarding partials")
        shutil.rmtree(pdir, ignore_errors=True)
        manifest = None

    omitted = list(manifest.get("omitted", [])) if manifest else []

    complete, interrupted, unidentified = scan_batches(pdir)
    for entry in interrupted:
        log(f"  Doc{document_number}: batch {entry['number']:02d} has no readable zip — "
            f"{len(entry['row_keys'])} row(s) returned to the pool")
        _discard_batch(entry)
    for path in unidentified:
        log(f"  Doc{document_number}: {path.name} names no rows — discarded, will re-download")
        path.unlink(missing_ok=True)

    done_keys = {k for b in complete for k in b["row_keys"]}
    if complete:
        log(f"  Doc{document_number}: resuming — {len(complete)} complete batch(es) on disk")

    # The fingerprint's row list is the plan -- the same list `_finalise_live` gates completeness
    # against below. Re-reading it off the picker here would be a second enumeration of a page
    # whose state has changed since (the select-all is gone, so the short-read guard's
    # `expected_count` is unreadable), and it could only ever diverge from what we are checked
    # against: live, it read 50 rows where the guarded enumeration read 84.
    new_batches, new_omitted = accumulate_batches(
        picker, fingerprint["row_keys"], threshold_mb=threshold_mb, skip_keys=done_keys, log=log)
    omitted.extend(k for k in new_omitted if k not in omitted)

    # The manifest carries only the fingerprint and the un-capturable rows, neither of which
    # changes as batches land, so it is written once rather than after every batch.
    write_manifest(pdir, fingerprint, omitted)

    for batch in new_batches:
        n = _next_batch_number(pdir)
        # Sidecar FIRST: after this line the directory can say what the zip about to be written
        # is meant to be, even if the process dies mid-download.
        sidecar = write_sidecar(pdir, n, batch)
        path = _batch_path(pdir, n)
        entry = {"number": n, "row_keys": batch, "sidecar": sidecar, "zip": path}
        for key in batch:
            picker.set_selected(key, True)
        try:
            picker.download_to(path)
        except Exception as exc:                  # noqa: BLE001 — keep what we already have
            # download_to can fail partway through writing path, leaving a truncated
            # batch-NN.zip. Drop both halves: the posting is open, so this batch is simply
            # re-planned next run. Earlier, complete batches are untouched.
            _discard_batch(entry)
            log(f"  Doc{document_number}: batch {n} failed ({exc}) — earlier batches kept, "
                f"will resume")
            return None
        finally:
            for key in batch:
                picker.set_selected(key, False)
        if not _openable_zip(path):
            # A download that "succeeded" but produced something that will not open is a
            # failed download. Validate here, while the posting is still open and re-fetching
            # is free -- NOT at merge time, where skipping it would finalise around a gap that
            # was recoverable all along.
            _discard_batch(entry)
            log(f"  Doc{document_number}: batch {n} downloaded but will not open as a zip — "
                f"discarded, will re-download")
            return None
        log(f"    batch {n}: {len(batch)} row(s) -> {path.name}")

    return _finalise_live(pdir, document_number, dest_dir, fingerprint, omitted, log)


def _merge_and_record(parts, pdir, document_number, dest_dir, omitted, expected_files, log,
                      unreadable=()):
    """Merge `parts` into the canonical bundle, record the gap, drop the working directory.

    `unreadable` batch files are the exception to dropping the working directory: `_openable_zip`
    fails only because a zip's central directory is written last, at EOF, so a batch truncated
    partway through a download can still hold hundreds of intact members a lenient tool could
    recover later. The live path (capture_in_batches) never reaches here with anything
    unreadable -- it discards those immediately, while re-fetching is still free -- so this only
    ever fires from the salvage path, once the posting has closed and those bytes can never be
    re-fetched. Keeping the whole partial directory (rather than moving files one at a time)
    keeps the on-disk story simple: everything this run could not account for is exactly what is
    still sitting in `.partial/Doc<n>/` beside the `.omitted.json` that says so.
    """
    target = Path(dest_dir) / f"Doc{document_number}.zip"
    target, count = merge_bundles(parts, target, expected_files=expected_files, log=log)
    write_omitted(target, omitted, expected_files, count, unreadable=unreadable)
    if unreadable:
        log(f"  Doc{document_number}: {len(unreadable)} unreadable batch file(s) kept in "
            f"{pdir} — bytes that can never be re-fetched are not deleted")
    else:
        shutil.rmtree(pdir, ignore_errors=True)
    log(f"  Doc{document_number}: merged {len(parts)} batch(es) -> {target.name} ({count} files)")
    return target


def _finalise_live(pdir, document_number, dest_dir, fingerprint, omitted, log):
    """Write the canonical bundle ONLY if nothing recoverable is still missing.

    The posting is open, so every gap except an `omitted` row is re-downloadable next run, and
    a bundle written now would be indistinguishable from a finished capture to
    capture_attachments' "does Doc<n>.zip exist" check -- nothing would ever retry it. So the
    gate is the fingerprint's own row list: every planned row must sit in a complete batch or
    be recorded un-capturable. Anything else keeps the partials and returns None, which simply
    leaves the event pending.

    This is the half that must NOT behave like `finalise_partial`. Skipping a broken batch is
    right when it can never be re-fetched and wrong while it can.
    """
    complete, interrupted, unidentified = scan_batches(pdir)
    captured = {k for b in complete for k in b["row_keys"]}
    covered = captured | set(omitted)
    # capture_in_batches refuses a fingerprint without row_keys before this is ever reached, so
    # indexing directly (rather than `.get(...) or []`) is deliberate: a degraded fingerprint
    # here would be a bug upstream, not a case to quietly tolerate.
    missing = [k for k in fingerprint["row_keys"] if k not in covered]

    if missing or interrupted or unidentified:
        log(f"  Doc{document_number}: {len(missing)} row(s) still uncaptured — partials kept, "
            f"not finalising an incomplete bundle")
        return None
    if not complete:
        return None

    parts = [b["zip"] for b in complete]
    return _merge_and_record(parts, pdir, document_number, dest_dir, omitted,
                             fingerprint.get("file_count"), log)


def finalise_partial(document_number: str, dest_dir, *, posting_open: bool,
                     log=lambda _m: None):
    """Merge whatever batches exist, for a posting that closed mid-capture.

    **`posting_open` must be False and is validated, not assumed.** Respond is disabled the
    moment a posting closes, so those batches can never be completed and keeping 3 of 5 is
    permanently better than nothing -- that is the whole reason partials are retained. Run
    while a posting is still OPEN, though, and this canonicalises a capture that could have
    completed: `Doc<n>.zip` would exist, so `capture_attachments` would never come back to it.
    Hence the refusal. The caller has the answer already:
    `posting_open=not respond.is_disabled()`.

    Salvage policy, the mirror of the live one: skip what will not open, merge the rest, and
    record the gap durably. A batch whose sidecar survives but whose zip does not contributes
    its ROW KEYS to `Doc<n>.omitted.json` -- what is missing, named the same way everything
    else in that array is named. Returns the bundle path, or None if there is nothing to
    finalise.
    """
    if posting_open is not False:
        raise ValueError(
            f"finalise_partial is for a CLOSED posting (posting_open=False), got "
            f"{posting_open!r} — canonicalising a capture that could still complete would "
            f"mark it archived and stop it being retried. Use capture_in_batches instead.")

    dest_dir = Path(dest_dir)
    pdir = partial_dir(dest_dir, document_number)
    manifest = read_manifest(pdir)
    # A missing OR corrupt manifest must not give up on the batches -- they are read from disk
    # regardless, so this only loses the omitted/expected_files bookkeeping.
    omitted = list(manifest.get("omitted", [])) if manifest else []
    # None here is a deliberate "we do not know", not a stand-in for zero: it flows into
    # merge_bundles' one-sided shortfall check (`expected_files is not None and ...`, which
    # simply skips the comparison) and then into write_omitted, where it lands as JSON
    # `"expected_files": null`. That reads as "count unknown" to anyone greping the
    # .omitted.json later, which is the honest answer when the manifest could not be read.
    expected_files = (manifest.get("fingerprint") or {}).get("file_count") if manifest else None

    complete, interrupted, unidentified = scan_batches(pdir)
    parts = [b["zip"] for b in complete]
    unreadable = []
    # Whether an unidentified-but-openable zip went into `parts`. Its bytes are merged, but
    # nothing names which rows it holds -- see the guard on the never-attempted diff below.
    anonymous_merged = False

    for entry in interrupted:
        log(f"  Doc{document_number}: batch {entry['number']:02d} never completed — its "
            f"{len(entry['row_keys'])} row(s) recorded as missing")
        omitted.extend(k for k in entry["row_keys"] if k not in omitted)

    for path in unidentified:
        # `unidentified` mixes two different kinds of residue: a sidecar too corrupt to read
        # (names no rows, but its own batch-NN.zip -- if any -- is examined on its own below,
        # since scan_batches leaves that number unclaimed) and an orphaned zip with no sidecar
        # at all. Skipping anything that is not itself a `.zip` here is what keeps a corrupt
        # `batch-02.json` from being reported as an "unreadable batch" beside a `batch-02.zip`
        # that opened and merged just fine -- one real batch must not produce two entries.
        if path.suffix != ".zip":
            continue
        # Bytes we cannot attribute are still bytes we can never re-fetch: keep any zip that
        # opens, and merge it in batch-number order with the rest.
        if _openable_zip(path):
            log(f"  Doc{document_number}: {path.name} has no sidecar — merged anyway, its rows "
                f"are unknown")
            parts.append(path)
            anonymous_merged = True
        else:
            log(f"  Doc{document_number}: {path.name} will not open — skipped, rows unknown")
            unreadable.append(path.name)

    # A row the fingerprint planned but that never got a sidecar at all (the run stopped, or the
    # posting closed, before that row was ever attempted) is a gap `interrupted` cannot see --
    # there is no batch number to iterate. Reuse _finalise_live's own expression so salvage names
    # the specific rows lost instead of only shrinking `expected_files` vs `actual_files`.
    #
    # But this diff is only trustworthy when NOTHING went into `parts` anonymously: it credits a
    # row as "attempted" only by finding it in a sidecar-named `complete` batch, so a row whose
    # bytes rode in on an unidentified-but-openable zip looks identical to one truly never
    # attempted -- the diff cannot tell "unknown identity" from "never happened". Flagging it as
    # omitted would be a false claim of data loss for bytes that are sitting right there in the
    # bundle this run just wrote. When that ambiguity exists, fall back to the weaker
    # expected_files/actual_files comparison `write_omitted` already does -- which exists
    # precisely for cases like this one, where we cannot say more than "the counts don't match".
    fingerprint = manifest.get("fingerprint") if manifest else None
    if fingerprint and not anonymous_merged:
        captured = {k for b in complete for k in b["row_keys"]}
        covered = captured | set(omitted)
        never_attempted = [k for k in (fingerprint.get("row_keys") or []) if k not in covered]
        omitted.extend(k for k in never_attempted if k not in omitted)

    if not parts:
        return None
    parts = sorted(parts)     # zero-padded batch-NN, so lexicographic IS batch order
    return _merge_and_record(parts, pdir, document_number, dest_dir, omitted, expected_files,
                             log, unreadable=unreadable)
