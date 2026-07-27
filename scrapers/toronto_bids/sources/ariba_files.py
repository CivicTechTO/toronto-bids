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
    download(file, dest)  -> Path                 TRUNCATES and saves to EXACTLY dest, or raises
    expected_count()      -> int | None           the picker's authoritative Total Number

`download` writes to the path it is handed and nothing else: the `.part` staging and the
os.replace live here, because atomicity is the property most worth testing and the adapter is
the half that cannot be unit-tested. It must TRUNCATE that path rather than append to or
range-resume it -- this side unlinks a stale `.part` before every call so the contract holds
from both ends, because an adapter that appended would turn a killed transfer's leftover bytes
into a corrupt file that then becomes canonical.

`key` is a file's IDENTITY in the listing and `name` is only its label: two different documents
routinely share a name, so the fingerprint below keys on the ORDERED (key, name) pairs.

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


PARTIAL_DIRNAME = ".partial-files"


def partial_dir(dest_dir, document_number: str) -> Path:
    """Working directory for an in-progress per-file capture.

    Outside the canonical `Doc<n>.zip` namespace on purpose: capture_attachments decides what
    is already archived by testing for that file, so a partial capture must be invisible to it.

    And deliberately NOT `.partial/`, which the batched capture (ariba_batch) uses: the two held
    the same path, with the same `manifest.json` name and incompatible schemas, so whichever
    module looked first self-healed by discarding the other's work -- and the closed-posting
    branch in capture_event ran the BATCH salvage over a per-file directory, found no
    `batch-*.zip`, logged "skipped", and abandoned downloaded files that can never be re-fetched.
    Separate namespaces remove the whole class rather than arbitrating it.
    """
    return Path(dest_dir) / PARTIAL_DIRNAME / f"Doc{document_number}"


def make_fingerprint(files, expected_count) -> dict:
    """Identity of the event as we traversed it: the ORDERED (key, name) pairs it listed.

    Order is load-bearing, and a sorted name multiset was silently wrong. `capture_files`
    assigns a resumed file's disk name by POSITION -- it zips the listing against
    `unique_names(...)` -- so position IS identity here. Two documents can share a name
    (`report.pdf` twice is ordinary), and a listing that comes back in the other order maps the
    same disk names onto different documents. Fingerprinting `sorted(names)` cannot see that:
    run 1 saves X as `report.pdf` and dies, run 2 lists Y first, the fingerprint matches, the
    partial is kept as already-complete, and the bundle ends up holding X twice and Y never --
    with the counts matching, so no gap is recorded. Silently wrong, permanently.

    So the pairs are compared in order and any reorder or identity substitution invalidates the
    partials, which costs a re-download and nothing else. Lists (not tuples) because this is
    compared against a manifest read back from JSON. The expected count rides along for the same
    reason it always did: an addendum landing between runs is a different version of the
    solicitation, and mixing two versions into one bundle would be worse than re-fetching.
    """
    return {"files": [[f["key"], f["name"]] for f in files], "expected_count": expected_count}


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
    nothing is. It outlives the capture (the partial manifest does not), so a gap is greppable
    later without reading logs.

    Which is exactly why the complete case UNLINKS rather than merely returning: a capture that
    omitted a file and a later run that captured everything write the same bundle path, and a
    stale record left beside it would go on claiming a gap that no longer exists. "Absence means
    nothing is missing" only holds if absence is something this function actively produces.
    """
    path = Path(bundle_path).with_suffix(".omitted.json")
    if not omitted and expected_files == actual_files:
        path.unlink(missing_ok=True)
        return None
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
    fingerprint = make_fingerprint(files, expected)
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
        # Complete by construction: a file only reaches this name through the os.replace below,
        # which a 0-byte transfer never gets to. The size is checked all the same, so a 0-byte
        # file from anywhere else is re-fetched rather than silently promoted (see M11 below).
        if dest.is_file() and dest.stat().st_size:
            captured.append((dest, zip_name))
            continue
        part = dest.with_suffix(dest.suffix + ".part")
        # A `.part` left by a killed run is an INTERRUPTED transfer, never a head start: drop it
        # before handing the path over, so an adapter that appends or range-resumes cannot turn
        # those bytes into a corrupt file that then becomes canonical (see the FileSource
        # contract in the module docstring — both ends enforce truncation).
        part.unlink(missing_ok=True)
        try:
            source.download(entry, part)
            if not part.stat().st_size:
                # No attachment here is legitimately empty, so a 0-byte "success" is a failed
                # transfer. Promoting it would put a permanent silent hole in the bundle where
                # a document should be; recorded as an omission it is at least greppable.
                raise RuntimeError("downloaded 0 bytes")
            os.replace(part, dest)
        except BaseException as exc:            # noqa: BLE001 — one dead file, not the batch
            part.unlink(missing_ok=True)        # an incomplete download is never useful
            if not isinstance(exc, Exception):
                # Ctrl-C / SystemExit: clean up the `.part` like build_bundle cleans up its
                # `.tmp`, then let it through. Recording an interrupt as "this file is missing
                # from Ariba" would be a false durable claim about the event.
                raise
            omitted.append(entry["name"])
            log(f"    {entry['name']}: download failed ({exc}) — omitted")
            continue
        captured.append((dest, zip_name))

    if not captured:
        log(f"  Doc{document_number}: no file downloaded — leaving the event pending")
        return None

    # The gap record goes down BEFORE the bundle, the same way ariba_batch writes a batch's
    # sidecar before its zip. Once Doc<n>.zip exists, capture_attachments treats the event as
    # archived forever -- so a record that failed to write after it (ENOSPC is not hypothetical
    # when the next call writes 787 MB) would leave the gap undescribed permanently, and the
    # record whose ABSENCE means "nothing is missing" would be the thing that went missing. A
    # record with no bundle beside it is the harmless direction: nothing reads it, and the next
    # run rewrites it.
    target = dest_dir / f"Doc{document_number}.zip"
    write_omitted(target, omitted, expected, len(captured))
    build_bundle(captured, target)
    shutil.rmtree(pdir, ignore_errors=True)
    log(f"  Doc{document_number}: captured {len(captured)} file(s) -> {target.name}")
    return target


def finalise_partial(document_number: str, dest_dir, *, posting_open: bool,
                     log=lambda _m: None):
    """Bundle whatever files are already on disk, for a posting that closed mid-capture.

    **`posting_open` must be False and is validated, not assumed.** Respond is disabled the
    moment a posting closes, so the files still owed can never be fetched and keeping 30 of 54
    is permanently better than nothing -- that is the whole reason partials are retained. Run it
    while the posting is still OPEN and it canonicalises a capture that could have completed:
    `Doc<n>.zip` would exist, so `capture_attachments` would never come back to it. The caller
    has the answer already: `posting_open=not respond.is_disabled()`.

    Salvage policy, the mirror of `capture_files`: take every complete file, record the rest as
    omitted by NAME (the same way capture_files names them), and write the gap record before the
    bundle. The manifest's fingerprint supplies the names and the disk layout, so a lost or
    corrupt manifest costs the naming, never the bytes: anything sitting in `files/` is bundled
    under its own disk name regardless, with the shortfall left to the expected/actual counts.

    A leftover `.part` is a truncated transfer -- never a bundle member, and never deleted
    either, since those bytes can no longer be re-fetched. Its file is recorded missing and the
    working directory is kept so the residue sits beside the `.omitted.json` that says so.

    Returns the bundle path, or None if there is nothing on disk to finalise.
    """
    if posting_open is not False:
        raise ValueError(
            f"finalise_partial is for a CLOSED posting (posting_open=False), got "
            f"{posting_open!r} — canonicalising a capture that could still complete would mark "
            f"it archived and stop it ever being retried. Use capture_files instead.")

    dest_dir = Path(dest_dir)
    pdir = partial_dir(dest_dir, document_number)
    fdir = pdir / "files"
    fingerprint = (read_manifest(pdir) or {}).get("fingerprint") or {}
    planned = fingerprint.get("files") or []

    captured, omitted, taken = [], [], set()
    for pair, zip_name in zip(planned, unique_names([p[1] for p in planned])):
        path = fdir / zip_name
        if path.is_file() and path.stat().st_size:
            captured.append((path, zip_name))
            taken.add(zip_name)
        else:
            omitted.append(pair[1])

    leftover = []
    if fdir.is_dir():
        for path in sorted(fdir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix == ".part":
                leftover.append(path.name)
            elif path.name not in taken and path.stat().st_size:
                # Bytes the manifest cannot account for are still bytes we can never re-fetch.
                captured.append((path, path.name))
                taken.add(path.name)

    if not captured:
        return None

    target = dest_dir / f"Doc{document_number}.zip"
    # Gap record first, for the same reason capture_files does it: see the comment there.
    write_omitted(target, omitted, fingerprint.get("expected_count"), len(captured))
    build_bundle(captured, target)
    if leftover:
        log(f"  Doc{document_number}: {len(leftover)} interrupted transfer(s) kept in {pdir} — "
            f"bytes that can never be re-fetched are not deleted")
    else:
        shutil.rmtree(pdir, ignore_errors=True)
    log(f"  Doc{document_number}: closed mid-capture — salvaged {len(captured)} file(s) -> "
        f"{target.name}")
    return target
