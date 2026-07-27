"""Batched capture for Ariba events over the 500 MB single-zip ceiling (#174).

Ariba hard-stops a single bundle at 500 MB and says so in the picker: "You must select
specific items and perform multiple downloads." This module is the "select specific items"
half -- pure logic over a 5-method Picker protocol, so it is testable without a browser.
The Playwright adapter lives in ariba_attachments.py.

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
    `_finalise` reads batch zips from DISK, not from this manifest, so `finalise_partial` can
    still salvage every downloaded batch even with no manifest at all. Only the `omitted` /
    `expected_files` bookkeeping is lost, never bytes that can no longer be re-fetched.
    """
    path = Path(pdir) / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_manifest(pdir, fingerprint: dict, batches, omitted) -> Path:
    """Write the manifest atomically.

    `capture_in_batches` calls this after every batch, so the window for a torn write (a
    crash mid-write leaving truncated JSON) is no longer negligible. Build the temp file
    beside the target and `os.replace()` it into position -- the same pattern `merge_bundles`
    uses for the bundle itself -- so a reader never observes a partial write.
    """
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / MANIFEST_NAME
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(
        {"fingerprint": fingerprint, "batches": [list(b) for b in batches],
         "omitted": list(omitted)}, indent=2))
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


def accumulate_batches(picker, threshold_mb: float = BATCH_THRESHOLD_MB,
                       skip_keys=(), log=lambda _m: None):
    """Group the picker's rows into selections that each stay under `threshold_mb`.

    There is no per-row size in the picker -- the only way to learn a selection's size is to
    select it and read the summary -- so this ticks one row at a time and measures. Rows are
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

    for key in picker.row_keys():
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


def write_omitted(bundle_path, omitted, expected_files, actual_files) -> Path | None:
    """Durable record of what a bundle does NOT contain, beside the bundle itself.

    Written only when something is actually missing. It outlives the capture (the .partial
    manifest does not), so a gap is greppable later without reading logs.
    """
    if not omitted and expected_files == actual_files:
        return None
    bundle_path = Path(bundle_path)
    path = bundle_path.with_suffix(".omitted.json")
    path.write_text(json.dumps(
        {"omitted": list(omitted), "expected_files": expected_files,
         "actual_files": actual_files}, indent=2))
    return path


def _batch_path(pdir, n: int) -> Path:
    return Path(pdir) / f"batch-{n:02d}.zip"


def capture_in_batches(picker, document_number: str, dest_dir, fingerprint: dict,
                       threshold_mb: float = BATCH_THRESHOLD_MB, log=lambda _m: None):
    """Download an oversized event in batches and merge them into Doc<n>.zip.

    Resumable: each completed batch is appended to the manifest as it lands, so a failure
    keeps its bytes and the next run continues from there. Returns the canonical bundle path
    once every batch is in hand, else None -- and because the canonical zip is only written on
    completion, an incomplete event simply stays in capture_attachments' pending list.
    """
    dest_dir = Path(dest_dir)
    pdir = partial_dir(dest_dir, document_number)
    manifest = read_manifest(pdir)

    if manifest is None:
        # Missing OR corrupt -- either way we cannot trust what's already recorded, and a good
        # orphaned batch may already occupy batch-01.zip: restarting numbering at 1 below would
        # silently overwrite it. Discard and restart clean, exactly like the stale-fingerprint
        # branch. This is safe PRECISELY HERE: capture_in_batches only ever runs while the
        # posting is still open (its caller checks the portal's Respond control first), so
        # anything discarded can simply be re-downloaded. The closed-posting path goes through
        # finalise_partial / _finalise instead, which read batches from disk and never discard.
        if pdir.exists():
            log(f"  Doc{document_number}: missing/corrupt manifest — discarding partials and "
                f"restarting")
            shutil.rmtree(pdir, ignore_errors=True)
    elif not manifest_is_current(manifest, fingerprint):
        log(f"  Doc{document_number}: event changed since the last run — discarding partials")
        shutil.rmtree(pdir, ignore_errors=True)
        manifest = None

    done_batches = list(manifest["batches"]) if manifest else []
    omitted = list(manifest["omitted"]) if manifest else []
    done_keys = {k for batch in done_batches for k in batch}
    if done_keys:
        log(f"  Doc{document_number}: resuming — {len(done_batches)} batch(es) already on disk")

    new_batches, new_omitted = accumulate_batches(
        picker, threshold_mb=threshold_mb, skip_keys=done_keys, log=log)
    omitted.extend(k for k in new_omitted if k not in omitted)

    pdir.mkdir(parents=True, exist_ok=True)
    write_manifest(pdir, fingerprint, done_batches, omitted)

    for batch in new_batches:
        for key in batch:
            picker.set_selected(key, True)
        path = _batch_path(pdir, len(done_batches) + 1)
        try:
            picker.download_to(path)
        except Exception as exc:                  # noqa: BLE001 — keep what we already have
            # download_to can fail partway through writing path, leaving a truncated
            # batch-NN.zip. To _finalise's glob that debris is indistinguishable from a good
            # batch and would abort the merge (BadZipFile) for every sibling batch too --
            # remove it here rather than let it accumulate. Earlier, already-completed batches
            # are untouched.
            path.unlink(missing_ok=True)
            log(f"  Doc{document_number}: batch {len(done_batches) + 1} failed ({exc}) — "
                f"earlier batches kept, will resume")
            return None
        finally:
            for key in batch:
                picker.set_selected(key, False)
        done_batches.append(batch)
        write_manifest(pdir, fingerprint, done_batches, omitted)
        log(f"    batch {len(done_batches)}: {len(batch)} row(s) -> {path.name}")

    return _finalise(pdir, document_number, dest_dir, omitted,
                     fingerprint.get("file_count"), log)


def _openable_zip(path) -> bool:
    """Whether `path` opens as a zip with a readable central directory.

    Deliberately NOT `testzip()`: that decompresses every member to verify its CRC, which is
    ruinous on a bundle whose members reach 88.7 MB. `zipfile.ZipFile()` already parses the
    central directory on open (it seeks from EOF to find it), so a truncated or corrupt file
    fails right here -- that is enough to catch the debris finding 2 is about without paying
    for a full decompress pass.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            zf.infolist()
        return True
    except (zipfile.BadZipFile, OSError):
        return False


def _finalise(pdir, document_number, dest_dir, omitted, expected_files, log):
    """Merge whatever batch zips are actually ON DISK in `pdir` -- but only the ones that
    survive validation.

    Deliberately reads the batch list from disk (`glob`), not from the manifest: a batch zip
    is written by `picker.download_to(path)` and only recorded by the following
    `write_manifest` call on the NEXT line, so a process-level crash in that window leaves a
    valid batch-NN.zip that the manifest never lists. Deriving `parts` from the manifest's
    batch count (as this used to) would silently exclude that file from the merge and then
    delete it via the `rmtree` below -- permanently losing bytes that, once a posting closes,
    can never be re-downloaded. `expected_files`/`omitted` still come from the manifest --
    they describe the whole event and aren't derivable from the zips alone. Zero-padded
    `batch-{n:02d}` naming makes the lexicographic glob sort correct up to 99 batches.

    Disk truth still needs to be VALIDATED truth: a batch left truncated by a crash before this
    fix's download-failure cleanup existed (or from any other source of debris) is, to a bare
    glob, indistinguishable from a good batch, and `merge_bundles` raises `BadZipFile` on it --
    which would abort the salvage of every sibling batch, turning "3 of 5 is better than
    nothing" into "0 of 5 and an exception". Each unopenable file is skipped and named in the
    durable `omitted` record instead, so the gap survives past this run's log.
    """
    all_parts = sorted(Path(pdir).glob("batch-*.zip"))
    parts = []
    omitted = list(omitted)
    for part in all_parts:
        if _openable_zip(part):
            parts.append(part)
        else:
            log(f"  Doc{document_number}: {part.name} will not open as a zip — skipped")
            omitted.append(part.name)
    if not parts:
        return None
    target = Path(dest_dir) / f"Doc{document_number}.zip"
    target, count = merge_bundles(parts, target, expected_files=expected_files, log=log)
    write_omitted(target, omitted, expected_files, count)
    shutil.rmtree(pdir, ignore_errors=True)
    log(f"  Doc{document_number}: merged {len(parts)} batch(es) -> {target.name} ({count} files)")
    return target


def finalise_partial(document_number: str, dest_dir, log=lambda _m: None):
    """Merge whatever batches exist, for a posting that closed mid-capture.

    Respond is disabled the moment a posting closes, so those batches can never be completed.
    Keeping 3 of 5 is permanently better than nothing -- this is the whole reason partial
    captures are retained. Returns the bundle path, or None if there is nothing to finalise.
    """
    dest_dir = Path(dest_dir)
    pdir = partial_dir(dest_dir, document_number)
    manifest = read_manifest(pdir)
    # A missing OR corrupt manifest must not give up on the batches -- _finalise reads them
    # from disk regardless, so this only loses the omitted/expected_files bookkeeping.
    omitted = manifest.get("omitted", []) if manifest else []
    # None here is a deliberate "we do not know", not a stand-in for zero: it flows into
    # merge_bundles' one-sided shortfall check (`expected_files is not None and ...`, which
    # simply skips the comparison) and then into write_omitted, where it lands as JSON
    # `"expected_files": null`. That reads as "count unknown" to anyone greping the .omitted.json
    # later, which is the honest answer when the manifest itself could not be read.
    expected_files = manifest.get("fingerprint", {}).get("file_count") if manifest else None
    return _finalise(pdir, document_number, dest_dir, omitted, expected_files, log)
