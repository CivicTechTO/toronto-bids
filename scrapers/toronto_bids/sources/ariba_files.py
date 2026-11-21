"""Per-file capture of Ariba solicitation documents (#174).

Pure module (no Playwright): builds canonical `Doc<n>.zip` bundles from
individually downloaded files, avoiding Ariba's 500 MB server-zip ceiling.
Adapters implement a 2-method FileSource protocol:
  - `list_files() -> [{key, name, row, ordinal}]` (`key` stable across runs, non-positional)
  - `download(file, dest) -> Path` (truncates and writes to `dest`, or raises)

Docs: docs/ariba-attachments.md
"""

import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

# Unanchored so trailing size metadata ("Appendix.pdf (1.2 MB)") still matches,
# while negative lookahead rejects glued suffixes like "notes.pdfx".
_DOCUMENT_EXTENSION = re.compile(
    r"\.(7z|bmp|csv|dgn|docx?|dwf|dwg|dxf|eml|gif|gz|jpe?g|kmz|msg|odt|pdf|png|pptx?|rar|rtf|"
    r"tar|tiff?|txt|xlsm|xlsx?|xml|zip)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Leading outline number ("3.1 Drawings Package ..."); sub-rows under References omit it.
_OUTLINE = re.compile(r"^(\d+(?:\.\d+)*)(?:\s|$)")

# Minimum fraction of listed files required to archive; lower counts keep partials pending.
_MIN_CAPTURE_RATIO = 0.5


def is_document_name(name) -> bool:
    """Return True if an anchor label names a downloadable document (excluding URLs)."""
    text = " ".join((name or "").replace("\xa0", " ").split())
    if not text or text.lower().startswith(("http://", "https://", "www.")):
        return False
    return bool(_DOCUMENT_EXTENSION.search(text))


def row_identity(row_text) -> str:
    """Return a stable row identifier: its outline number if present, else normalized row text."""
    text = " ".join((row_text or "").replace("\xa0", " ").split())
    m = _OUTLINE.match(text)
    return m.group(1) if m else text


def is_outline_row(row_text) -> bool:
    """Return True if row text starts with a content-tree outline number (e.g. '3.1 ...')."""
    text = " ".join((row_text or "").replace("\xa0", " ").split())
    return bool(_OUTLINE.match(text))


def anchor_key(anchor) -> str:
    """Durable cross-run file identity: `row_identity#ordinal#name`.

    Stable across pristine pre-download traversals, not mid-run: opening an AribaWeb menu
    reparents containers and shifts `closest('tr')`/ordinals, so mid-run clicks use DOM `handle`.
    """
    ordinal = anchor.get("ordinal")
    try:
        ordinal = int(ordinal)
    except (TypeError, ValueError):
        ordinal = 0
    name = " ".join((anchor.get("name") or "").replace("\xa0", " ").split())
    return f"{row_identity(anchor.get('row'))}#{ordinal}#{name}"


MENU_ITEM = "menu-item"
TREE_LINK = "tree-link"


def anchor_kind(anchor) -> str:
    """Classify an anchor as `MENU_ITEM` (AribaWeb PMI, direct click) or `TREE_LINK` (PML, opens menu).

    Defaults to `TREE_LINK` so unrecognized anchors fail loudly waiting for the download menu.
    """
    role = (anchor.get("role") or "").strip().lower()
    bh = (anchor.get("bh") or "").strip().upper()
    cls = (anchor.get("cls") or "").strip().lower()
    if role == "menuitem" or bh == "PMI" or "w-pmi-item" in cls:
        return MENU_ITEM
    return TREE_LINK


def pick_unclaimed(anchors, name, claimed):
    """Fallback mid-run match: first anchor matching `name` whose DOM id is not in `claimed`."""
    wanted = " ".join((name or "").replace("\xa0", " ").split())
    for anchor in anchors:
        text = " ".join((anchor.get("name") or "").replace("\xa0", " ").split())
        if text != wanted:
            continue
        handle = anchor.get("id") or ""
        if handle and handle in claimed:
            continue
        return anchor
    return None


def listing_from_anchors(anchors) -> dict:
    """Convert raw DOM anchor dicts into `{files, rejected, collided}` for one read.

    Trusts each anchor's `ordinal` (scoped to document siblings in its row by `_ANCHORS_JS`)
    and records duplicate keys in `collided` rather than silently dropping them.
    """
    files, rejected, collided, seen = [], [], [], set()
    for anchor in anchors:
        name = " ".join((anchor.get("name") or "").replace("\xa0", " ").split())
        if not is_document_name(name):
            if name:
                rejected.append(name)
            continue
        key = anchor_key(anchor)
        if key in seen:
            collided.append({"key": key, "name": name})
            continue
        seen.add(key)
        # `handle` is the per-session DOM id for mid-run clicks; excluded from `make_fingerprint`.
        files.append(
            {
                "key": key,
                "name": name,
                "row": anchor.get("row") or "",
                "ordinal": anchor.get("ordinal") or 0,
                "handle": anchor.get("id") or "",
                "kind": anchor_kind(anchor),
            }
        )
    return {"files": files, "rejected": rejected, "collided": collided}


def _listing_sort_key(entry) -> tuple:
    identity = row_identity(entry.get("row"))
    m = _OUTLINE.match(identity)
    if m and identity == m.group(1):
        return (0, [int(p) for p in identity.split(".")], "", entry.get("ordinal") or 0)
    return (1, [], identity, entry.get("ordinal") or 0)


def order_listing(files) -> list:
    """Sort listed files into deterministic tree order (numeric outline, text, ordinal)
    so fingerprints do not depend on scroll-sweep direction."""
    return sorted(files, key=_listing_sort_key)


def unique_names(names) -> list:
    """Disambiguate duplicate flat zip names as `name_2.ext`, skipping any name already taken."""
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


def build_bundle(files, target, *, before_replace=None) -> Path:
    """Write `[(disk_path, zip_name)]` flat into `target` atomically via a sibling `.tmp` file.

    Runs `before_replace(tmp)` before `os.replace` and removes `tmp` on any failure.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:
            for src, zip_name in files:
                with open(src, "rb") as fsrc, out.open(zip_name, "w") as fdst:
                    shutil.copyfileobj(
                        fsrc, fdst, 1 << 20
                    )
        if before_replace is not None:
            before_replace(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


MANIFEST_NAME = "manifest.json"


PARTIAL_DIRNAME = ".partial-files"


def partial_dir(dest_dir, document_number: str) -> Path:
    """Working directory for in-progress per-file captures, outside `Doc<n>.zip` and legacy `.partial/`."""
    return Path(dest_dir) / PARTIAL_DIRNAME / f"Doc{document_number}"


def _retire_partials(pdir):
    """Never reuse stale slots, but retain their bytes and original identity evidence."""
    if not pdir.exists():
        return
    history = pdir.parent / ".retired" / pdir.name
    history.mkdir(parents=True, exist_ok=True)
    snapshot = Path(tempfile.mkdtemp(prefix="capture-", dir=history))
    pdir.rename(snapshot / "payload")


def make_fingerprint(files) -> dict:
    """Ordered `[key, name]` pairs identifying a listing for resume checks.

    Order matters because disk names are assigned positionally via `unique_names`, so any reorder
    or key substitution invalidates partials. Uses lists to match JSON round-tripping.
    """
    return {"files": [[f["key"], f["name"]] for f in files]}


def read_manifest(pdir):
    path = Path(pdir) / MANIFEST_NAME
    try:
        record = json.loads(path.read_text())
        pairs = record["fingerprint"]["files"]
        if (not isinstance(pairs, list) or any(not isinstance(pair, list) or len(pair) != 2
                or any(not isinstance(value, str) or not value for value in pair) for pair in pairs)):
            return None
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_manifest(pdir, fingerprint: dict) -> Path:
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"fingerprint": fingerprint}, indent=2))
    os.replace(tmp, path)
    return path


def read_omitted(bundle_path):
    """Read durable gap evidence; an unreadable record is never evidence of completeness."""
    path = Path(bundle_path).with_suffix(".omitted.json")
    if not path.exists():
        return {"omitted": [], "collided": 0}
    try:
        record = json.loads(path.read_text())
        omitted, collided = record["omitted"], record.get("collided", 0)
        if (not isinstance(omitted, list) or
                any(not isinstance(name, str) or not name for name in omitted) or
                type(collided) is not int or collided < 0):
            raise ValueError("invalid gap fields")
        return {"omitted": omitted, "collided": collided}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"{path.name}: unreadable omission record ({exc})") from exc


def write_omitted(bundle_path, omitted, collided: int = 0):
    """Atomically write `<bundle>.omitted.json` when files were omitted or links collided.

    No-op when nothing is missing or collided; stale records are cleared separately after bundle write.
    """
    if not omitted and not collided:
        return None
    path = Path(bundle_path).with_suffix(".omitted.json")
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = {"omitted": list(omitted)}
    if collided:
        body["collided"] = collided
    tmp.write_text(json.dumps(body, indent=2))
    os.replace(tmp, path)
    return path


def clear_omitted_when_complete(bundle_path, omitted, collided: int = 0):
    """Remove a stale `.omitted.json` after `build_bundle` succeeds with no omissions or collisions."""
    if omitted or collided:
        return
    Path(bundle_path).with_suffix(".omitted.json").unlink(missing_ok=True)


class _BundleWouldShrink(ValueError):
    pass


def _publish_capture(captured, target, omitted, *, collided=0, listing=None, log=lambda _m: None):
    """Check actual candidate leaves before changing the canonical zip or gap record."""
    from toronto_bids.sources.zip_index import index_zip

    if listing is not None:
        return _publish_amendment(captured, target, omitted, listing, collided=collided, log=log)

    previous = read_omitted(target)
    retrying = target.exists() and bool(previous["omitted"] or previous["collided"])
    captured_names = {name for _, name in captured}
    unresolved = list(dict.fromkeys([
        *omitted, *(name for name in previous["omitted"] if name not in captured_names)]))
    # Carry forward prior collision counts since a count alone cannot verify resolution.
    collisions = max(collided, previous["collided"])
    gap = target.with_suffix(".omitted.json")
    old_gap = gap.read_bytes() if gap.exists() else None
    had_bundle = target.exists()

    def validate_and_record(candidate):
        if target.exists():
            existing_count = len(index_zip(target, strict=True))
            candidate_count = len(index_zip(candidate, strict=True))
            if candidate_count < existing_count:
                raise _BundleWouldShrink(
                    f"{target.stem}: re-capture has {candidate_count} leaf file(s) but "
                    f"existing bundle has {existing_count} — refusing to shrink; "
                    f"keeping existing bundle and partials")
            if retrying:
                with zipfile.ZipFile(target) as held:
                    held_names = {i.filename for i in held.infolist() if not i.is_dir()}
                lost = held_names - captured_names
                if lost:
                    raise _BundleWouldShrink(
                        f"{target.stem}: retry would lose previously archived files "
                        f"{sorted(lost)!r} — keeping existing bundle and partials")
        write_omitted(target, unresolved, collided=collisions)

    try:
        build_bundle(captured, target, before_replace=validate_and_record)
    except _BundleWouldShrink as exc:
        log(f"  {exc}")
        return False
    except BaseException:
        # Restore previous gap sidecar if atomic bundle replacement failed.
        current_gap = gap.read_bytes() if gap.exists() else None
        if had_bundle and current_gap != old_gap:
            if old_gap is None:
                gap.unlink(missing_ok=True)
            else:
                tmp = gap.with_suffix(gap.suffix + ".tmp")
                tmp.write_bytes(old_gap)
                os.replace(tmp, gap)
        raise
    clear_omitted_when_complete(target, unresolved, collided=collisions)
    return True


def _publish_amendment(captured, target, omitted, listing, *, collided=0, log=lambda _m: None):
    from toronto_bids.sources.ariba_versions import merge_capture, verify_preserved, write_provenance
    from toronto_bids.sources.zip_index import index_zip

    previous = read_omitted(target)
    collisions = max(collided, previous["collided"])
    planned = [{"key":entry["key"], "name":entry["name"], "archive_name":name}
               for entry,name in zip(listing, unique_names([e["name"] for e in listing]))]
    captured_names = {name for _,name in captured}
    missing = [entry["archive_name"] for entry in planned
               if entry["archive_name"] not in captured_names]
    if set(missing) != set(omitted):
        raise ValueError("Current omission evidence disagrees with the verified listing")
    gap = target.with_suffix(".omitted.json")
    old_gap = gap.read_bytes() if gap.exists() else None
    had_bundle = target.exists()
    retired = []
    retired_root = partial_dir(target.parent, target.stem.removeprefix("Doc")).parent / ".retired" / target.stem
    for snapshot in sorted(retired_root.glob("*/payload")):
        prior = read_manifest(snapshot)
        try:
            pairs = prior["fingerprint"]["files"]
            keys = dict(zip(unique_names([p[1] for p in pairs]), [p[0] for p in pairs]))
        except (TypeError, KeyError, IndexError):
            keys = {}  # preserve bytes without inventing keys for unreadable manifests
        for path in sorted((snapshot / "files").glob("*")):
            if path.is_file() and path.stat().st_size and path.suffix != ".part":
                retired.append((path, path.name, keys.get(path.name)))
    with merge_capture(captured, target, planned, retired=retired) as (files, manifest, old_hashes):
        def validate(candidate):
            write_provenance(candidate, manifest)
            verify_preserved(candidate, old_hashes)
            candidate_count = len(index_zip(candidate, strict=True))
            if target.exists() and candidate_count < len(index_zip(target, strict=True)):
                raise _BundleWouldShrink("Version merge would reduce the recursive leaf count")
            write_omitted(target, missing, collided=collisions)
        try:
            build_bundle(files, target, before_replace=validate)
        except BaseException:
            # Restore previous gap sidecar if atomic bundle replacement failed.
            if had_bundle:
                if old_gap is None:
                    gap.unlink(missing_ok=True)
                else:
                    temp = gap.with_suffix(gap.suffix + ".tmp")
                    temp.write_bytes(old_gap)
                    os.replace(temp, gap)
            raise
    clear_omitted_when_complete(target, missing, collided=collisions)
    return True


def capture_files(source, document_number: str, dest_dir, log=lambda _m: None):
    """Download listed documents individually into `.part` staging files, then build the bundle.

    Returns the bundle path or None if too few files succeeded; raises if `list_files()` is empty.
    """
    dest_dir = Path(dest_dir)
    files = source.list_files()
    if not files:
        raise RuntimeError(
            f"Doc{document_number}: the content tree listed no files — refusing to write an "
            f"empty bundle, which would mark this event archived permanently"
        )

    # Optional adapter hook reporting indistinguishable links collapsed during traversal.
    collided_count = getattr(source, "collided_count", lambda: 0)() or 0
    fingerprint = make_fingerprint(files)
    pdir = partial_dir(dest_dir, document_number)
    manifest = read_manifest(pdir)
    if manifest is None:
        # Retire partials on missing or corrupt manifest to avoid positional mismatch.
        if pdir.exists():
            log(
                f"  Doc{document_number}: missing/unreadable manifest — retiring partials "
                f"and restarting"
            )
        _retire_partials(pdir)
    elif manifest.get("fingerprint") != fingerprint:
        log(
            f"  Doc{document_number}: event changed since the last run — retiring partials"
        )
        _retire_partials(pdir)
    fdir = pdir / "files"
    fdir.mkdir(parents=True, exist_ok=True)
    write_manifest(pdir, fingerprint)

    captured, omitted = [], []
    for entry, zip_name in zip(files, unique_names([f["name"] for f in files])):
        dest = fdir / zip_name
        # Skip non-empty finalized files; 0-byte files are re-fetched.
        if dest.is_file() and dest.stat().st_size:
            captured.append((dest, zip_name))
            continue
        part = dest.with_suffix(dest.suffix + ".part")
        # Drop any leftover .part from a killed run so downloads always start clean.
        part.unlink(missing_ok=True)
        try:
            source.download(entry, part)
            if not part.stat().st_size:
                raise RuntimeError("downloaded 0 bytes")
            os.replace(part, dest)
        except BaseException as exc:
            part.unlink(missing_ok=True)
            if not isinstance(exc, Exception):
                # Let interrupts propagate without recording the file as missing.
                raise
            # Record by disambiguated zip name so duplicate labels remain distinguishable.
            omitted.append(zip_name)
            log(f"    {entry['name']}: download failed ({exc}) — omitted")
            continue
        captured.append((dest, zip_name))

    if not captured:
        log(f"  Doc{document_number}: no file downloaded — leaving the event pending")
        return None

    # Keep partials pending on wholesale failures instead of archiving a nearly empty bundle.
    if len(captured) < _MIN_CAPTURE_RATIO * len(files):
        log(
            f"  Doc{document_number}: only {len(captured)} of {len(files)} listed document(s) "
            f"downloaded — too few to archive (a bundle would mark this event captured "
            f"permanently); keeping the partials and leaving the event pending"
        )
        return None

    # Write gap sidecar before publishing the bundle; clear stale sidecars only after.
    target = dest_dir / f"Doc{document_number}.zip"

    if not _publish_capture(captured, target, omitted, collided=collided_count, listing=files, log=log):
        return None
    shutil.rmtree(pdir, ignore_errors=True)
    log(f"  Doc{document_number}: captured {len(captured)} file(s) -> {target.name}")
    return target


def finalise_partial(
    document_number: str, dest_dir, *, posting_open: bool, log=lambda _m: None
):
    """Bundle complete partial files for a closed posting (`posting_open=False` required).

    Records missing planned files in `.omitted.json` and preserves leftover `.part` files
    in place since closed postings cannot be re-fetched. Returns the bundle path or None.
    """
    if posting_open is not False:
        raise ValueError(
            f"finalise_partial is for a CLOSED posting (posting_open=False), got "
            f"{posting_open!r} — canonicalising a capture that could still complete would mark "
            f"it archived and stop it ever being retried. Use capture_files instead."
        )

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
            omitted.append(zip_name)

    leftover = []
    if fdir.is_dir():
        for path in sorted(fdir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix == ".part":
                leftover.append(path.name)
            elif path.name not in taken and path.stat().st_size:
                # Include any complete files the manifest could not account for.
                captured.append((path, path.name))
                taken.add(path.name)

    if not captured:
        return None

    target = dest_dir / f"Doc{document_number}.zip"
    if not _publish_capture(captured, target, omitted, log=log):
        return None
    if leftover:
        log(
            f"  Doc{document_number}: {len(leftover)} interrupted transfer(s) kept in {pdir} — "
            f"bytes that can never be re-fetched are not deleted"
        )
    else:
        shutil.rmtree(pdir, ignore_errors=True)
    log(
        f"  Doc{document_number}: closed mid-capture — salvaged {len(captured)} file(s) -> "
        f"{target.name}"
    )
    return target
