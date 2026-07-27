"""Batched capture for Ariba events over the 500 MB single-zip ceiling (#174).

Ariba hard-stops a single bundle at 500 MB and says so in the picker: "You must select
specific items and perform multiple downloads." This module is the "select specific items"
half -- pure logic over a 5-method Picker protocol, so it is testable without a browser.
The Playwright adapter lives in ariba_attachments.py.

Design: docs/superpowers/specs/2026-07-27-oversized-ariba-bundle-capture-design.md
"""
import json
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
    path = Path(pdir) / MANIFEST_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_manifest(pdir, fingerprint: dict, batches, omitted) -> Path:
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / MANIFEST_NAME
    path.write_text(json.dumps(
        {"fingerprint": fingerprint, "batches": [list(b) for b in batches],
         "omitted": list(omitted)}, indent=2))
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

    Returns (target, merged_member_count).
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:
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
                    with src.open(info) as fsrc, out.open(info.filename, "w") as fdst:
                        shutil.copyfileobj(fsrc, fdst, 1 << 20)

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
