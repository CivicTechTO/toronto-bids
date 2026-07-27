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
