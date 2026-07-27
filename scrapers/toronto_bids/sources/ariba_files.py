"""Per-file capture of Ariba solicitation documents (#174).

The bundle path drives Download Content -> Download Attachments, which server-zips a SELECTION
and is hard-stopped at 500 MB. That cannot reach Doc5713434353: row 3.1 is atomic within the
picker (nothing expands it) and holds 787.71 MB of the event's 792 MB. Batching splits a
selection, not a row.

But the event page's `All Content` view exposes every document individually, each with a
"Download this attachment" menu, and Max Size is 88.7 MB across 54 files. Nothing about the
content is too large -- only the bundle path's granularity was wrong.

So this captures files one at a time and builds the canonical zip ourselves. Because WE build
it, its layout is our choice, which is why naming matters here and never did before.

This module is PURE -- no Playwright import. The adapter lives in ariba_attachments.py, above
a 3-method FileSource protocol:

    list_files()          -> [{key, name, row}]   traversal, including expanding References
    download(file, dest)  -> Path                 saves to EXACTLY dest, or raises
    expected_count()      -> int | None           the picker's authoritative Total Number

`download` writes to the path it is handed and nothing else: the `.part` staging and the
os.replace live here, because atomicity is the property most worth testing and the adapter is
the half that cannot be unit-tested.

The small path/manifest helpers below are deliberately NOT imported from ariba_batch. They are
a few lines each, and depending on a module slated for deletion would invert the retirement
order (see the spec's "out of scope").

Design: docs/superpowers/specs/2026-07-27-ariba-per-file-capture-design.md
"""
import json
import os
import shutil
import zipfile
from pathlib import Path


def unique_names(names) -> list:
    """Flat zip names, disambiguating duplicates as `name_2.ext`, `name_3.ext`.

    The first occurrence keeps its name and later ones are numbered, deterministically in
    traversal order -- so the same event always produces the same bundle. A candidate that
    would itself collide (a real `a_2.pdf` already present) keeps searching rather than
    stealing the name: silently overwriting one document with another is the one outcome an
    archive cannot have.
    """
    used, out = set(), []
    for name in names:
        if name not in used:
            used.add(name)
            out.append(name)
            continue
        stem, dot, ext = name.rpartition(".")
        i = 2
        while True:
            candidate = f"{stem}_{i}.{ext}" if dot else f"{name}_{i}"
            if candidate not in used:
                used.add(candidate)
                out.append(candidate)
                break
            i += 1
    return out


def build_bundle(files, target) -> Path:
    """Zip `[(disk_path, zip_name)]` flat into `target`, atomically.

    Built into a sibling `.tmp` and moved into place with os.replace, so `target` either does
    not exist or is complete. A half-written Doc<n>.zip is worse than none: a sibling function
    treats that filename's existence as proof the solicitation is archived, forever.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:
            for src, zip_name in files:
                with open(src, "rb") as fsrc, out.open(zip_name, "w") as fdst:
                    shutil.copyfileobj(fsrc, fdst, 1 << 20)   # 88 MB files: stream, never slurp
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


MANIFEST_NAME = "manifest.json"


def partial_dir(dest_dir, document_number: str) -> Path:
    """Working directory for an in-progress capture.

    Outside the canonical `Doc<n>.zip` namespace on purpose: capture_attachments decides what
    is already archived by testing for that file, so a partial capture must be invisible to it.
    """
    return Path(dest_dir) / ".partial" / f"Doc{document_number}"


def make_fingerprint(names, expected_count) -> dict:
    """Identity of the event as we traversed it.

    Sorted names plus the expected count: if the City adds an addendum between runs the
    partials describe a different version of the solicitation, and mixing two versions into one
    bundle would be worse than re-fetching.
    """
    return {"names": sorted(names), "expected_count": expected_count}


def read_manifest(pdir):
    path = Path(pdir) / MANIFEST_NAME
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None            # missing OR corrupt degrades to "no manifest" -- re-traverse


def write_manifest(pdir, fingerprint: dict) -> Path:
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"fingerprint": fingerprint}, indent=2))
    os.replace(tmp, path)
    return path


def write_omitted(bundle_path, omitted, expected_files, actual_files):
    """Durable record of what a bundle does NOT contain, beside the bundle itself.

    Written only when something is actually missing, so its ABSENCE is meaningful evidence that
    nothing is. It outlives the capture (the .partial manifest does not), so a gap is greppable
    later without reading logs.
    """
    if not omitted and expected_files == actual_files:
        return None
    path = Path(bundle_path).with_suffix(".omitted.json")
    path.write_text(json.dumps(
        {"omitted": list(omitted), "expected_files": expected_files,
         "actual_files": actual_files}, indent=2))
    return path


def capture_files(source, document_number: str, dest_dir, log=lambda _m: None):
    """Download every document individually, then build the canonical bundle.

    Returns the bundle path, or None if nothing usable was captured (the event stays pending).
    Raises when the source lists NO files -- that is an event withholding its content, and an
    empty Doc<n>.zip would mark it archived forever. Empty is never success.
    """
    dest_dir = Path(dest_dir)
    files = source.list_files()
    if not files:
        raise RuntimeError(
            f"Doc{document_number}: the content tree listed no files — refusing to write an "
            f"empty bundle, which would mark this event archived permanently")

    expected = source.expected_count()
    fingerprint = make_fingerprint([f["name"] for f in files], expected)
    pdir = partial_dir(dest_dir, document_number)
    manifest = read_manifest(pdir)
    if manifest and manifest.get("fingerprint") != fingerprint:
        log(f"  Doc{document_number}: event changed since the last run — discarding partials")
        shutil.rmtree(pdir, ignore_errors=True)
    fdir = pdir / "files"
    fdir.mkdir(parents=True, exist_ok=True)
    write_manifest(pdir, fingerprint)

    captured, omitted = [], []
    for entry, zip_name in zip(files, unique_names([f["name"] for f in files])):
        dest = fdir / zip_name
        if dest.exists():                       # complete by construction -- see the .part rule
            captured.append((dest, zip_name))
            continue
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            source.download(entry, part)
            os.replace(part, dest)
        except Exception as exc:                # noqa: BLE001 — one dead file, not the batch
            part.unlink(missing_ok=True)        # an incomplete download is never useful
            omitted.append(entry["name"])
            log(f"    {entry['name']}: download failed ({exc}) — omitted")
            continue
        captured.append((dest, zip_name))

    if not captured:
        log(f"  Doc{document_number}: no file downloaded — leaving the event pending")
        return None

    target = build_bundle(captured, dest_dir / f"Doc{document_number}.zip")
    write_omitted(target, omitted, expected, len(captured))
    shutil.rmtree(pdir, ignore_errors=True)
    log(f"  Doc{document_number}: captured {len(captured)} file(s) -> {target.name}")
    return target
