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

    list_files()          -> [{key, name, row, ordinal}]  traversal, References expanded
    download(file, dest)  -> Path                 TRUNCATES and saves to EXACTLY dest, or raises
    expected_count()      -> int | None           the picker's authoritative Total Number

**Every PURE decision in that traversal lives here, not in the adapter** -- `is_document_name`
(which labels name a document), `anchor_key` (what a listed file IS), `listing_from_anchors`
(which repeats are duplicates, and what was discarded) and `order_listing`. They were written
inside the Playwright class, which is untestable by convention, and two of them are what the
archive's integrity rests on. The adapter reads the DOM and clicks; it decides nothing.

`key` CONTRACT: it must be STABLE across repeated traversals of the same event and INDEPENDENT
of position -- never derived from a file's index in the listing, and never from a counter that
increments in traversal order, which is the same thing wearing a different name (see
`anchor_key`). `make_fingerprint` below keys on the ordered `(key, name)` pairs precisely so a
resumed run can tell whether the same document is still in the same slot; a positional key
(`str(index)`) trivially has the right shape while carrying zero identity information, which
silently defeats that fingerprint the moment two files share a name -- see `make_fingerprint`'s
docstring for the exact reproduction.

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
import re
import shutil
import zipfile
from pathlib import Path

# Extensions a City solicitation actually publishes. NOT anchored to end-of-string: a content-tree
# anchor routinely renders its own size after the filename ("Appendix C2.pdf (1.2 MB)"), and the
# `$`-anchored pattern this replaces silently skipped every one of those -- a document missing
# from an archive that cannot be re-fetched. `(?![A-Za-z0-9])` keeps `notes.pdfx` out while
# letting anything non-alphanumeric (space, comma, bracket, end) follow the extension.
#
# The list is deliberately wider than "what we have seen": drawings arrive as .dwf/.dxf beside
# .dwg, pricing forms as macro-enabled .xlsm, scans as .tif, correspondence as .msg/.eml, and
# bundles as .7z/.gz/.tar/.rar/.kmz. Every omission here is a silent drop, so err wide -- a
# non-document that slips through fails loudly at download, which is the recoverable direction.
_DOCUMENT_EXTENSION = re.compile(
    r"\.(7z|bmp|csv|dgn|docx?|dwf|dwg|dxf|eml|gif|gz|jpe?g|kmz|msg|odt|pdf|png|pptx?|rar|rtf|"
    r"tar|tiff?|txt|xlsm|xlsx?|xml|zip)(?![A-Za-z0-9])", re.I)

# A row that LEADS with an outline number ("3.1 Drawings Package ...") -- the content tree's own
# address for that row, and the most stable identity a row can offer. Sub-rows behind a
# `References` toggle carry none, which is exactly the case the adapter's old key got wrong.
_OUTLINE = re.compile(r"^(\d+(?:\.\d+)*)(?:\s|$)")


def is_document_name(name) -> bool:
    """Whether an anchor's label names a downloadable document. PURE.

    A URL is not a document label: the content tree carries ordinary hyperlinks beside its
    attachments, and one ending in `.pdf` would otherwise be planned as a file to download.
    """
    text = " ".join((name or "").replace("\xa0", " ").split())
    if not text or text.lower().startswith(("http://", "https://", "www.")):
        return False
    return bool(_DOCUMENT_EXTENSION.search(text))


def row_identity(row_text) -> str:
    """The tree row an anchor sits in, as a stable string. PURE.

    The row's outline number when it has one -- the tree's own address, unchanged by anything
    the traversal does -- and the row's flattened text otherwise. Both are properties of the
    document's PLACE; neither is a fact about when a traversal reached it.
    """
    text = " ".join((row_text or "").replace("\xa0", " ").split())
    m = _OUTLINE.match(text)
    return m.group(1) if m else text


def is_outline_row(row_text) -> bool:
    """Whether a row's text leads with an outline number ('3.1 Drawings Package ...'). PURE.

    Used as a POSITIVE marker that the content tree specifically -- not merely "not the
    picker" -- is the page in front of us (#174 M4): an outline-numbered row is the tree's
    own address for a top-level row, and nothing on the export page (the other place the old,
    purely-negative check could land on) renders anything shaped like it.
    """
    text = " ".join((row_text or "").replace("\xa0", " ").split())
    return bool(_OUTLINE.match(text))


def anchor_key(anchor) -> str:
    """A listed file's IDENTITY: row identity + within-row ordinal + filename. PURE.

    **The one thing this must not be is positional.** The key the adapter used to build was
    `outline#name` plus an occurrence counter that incremented in TRAVERSAL ORDER, so two files
    sharing a base got `#2` for *when they were seen* rather than for *what they are*. That is
    a positional key wearing a stable key's clothes, and it defeats `make_fingerprint` exactly
    as the module docstring warns: the ordered (key, name) pairs come back byte-identical after
    a reorder, the resumed run adopts the partials, the listing is zipped against `unique_names`
    positionally, and the bundle holds one document twice and another never -- counts matching,
    so no gap is recorded. Silently wrong, permanently.

    Two reachable routes made that reproducible rather than theoretical. A `References` sub-row
    is its own `<tr>` whose text does not lead with a number, so `outline` was `""` for **every
    file behind every toggle** -- and the bulk of the files live there. And two rows can share
    an outline number's row text. Neither "degrades safely": the fallback WAS the positional
    counter.

    The ordinal fixes it because a row's DOM order is a property of the row, not of the sweep
    that read it -- the anchor is the 2nd link in row 3.1 no matter which pass sees it, which
    is precisely what a listing index can never say.
    """
    ordinal = anchor.get("ordinal")
    try:
        ordinal = int(ordinal)
    except (TypeError, ValueError):
        ordinal = 0
    name = " ".join((anchor.get("name") or "").replace("\xa0", " ").split())
    return f"{row_identity(anchor.get('row'))}#{ordinal}#{name}"


def listing_from_anchors(anchors) -> dict:
    """One DOM read of every `<a>` -> `{files, rejected, collided}`. PURE.

    `anchors` are `{name, row, ordinal}` dicts in DOM order; any other field they carry (the
    adapter's own `index`, which addresses an element within ONE read and must never leave it)
    is read here and not passed on. Returns the FileSource listing plus what it threw away,
    because both discards
    are ways a document goes missing from an archive that cannot be re-fetched, and neither may
    be silent:

    * `rejected` -- labels `is_document_name` refused. The adapter logs them, so a widened
      extension list is a data question with evidence rather than a guess.
    * `collided` -- entries whose key another entry in the SAME read already took. With the
      ordinal in the key this can no longer happen within one row (the old `(name, row)` rule
      dropped a second same-named link there with no log and no count); it survives only for
      two rows whose flattened text is genuinely identical, which we cannot tell apart. That
      collapse is recorded rather than left as a hole nothing mentions.

    Repeats ACROSS reads are ordinary -- the traversal re-reads the whole DOM on every scroll
    pass -- and the caller merges by key.

    `ordinal` on each entry is trusted as given: the reader that built `anchors` (`_ANCHORS_JS`
    in the adapter) is what scopes it to document-named siblings within the anchor's own row
    element (#174 M3) -- that scoping cannot be redone here from the flattened `row` TEXT
    alone, because two genuinely different row elements can render identical text (see
    `test_an_indistinguishable_duplicate_is_reported_not_silently_dropped`), and grouping by
    that text would wrongly treat them as one row's two anchors instead of two rows' one each.
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
        files.append({"key": key, "name": name, "row": anchor.get("row") or "",
                      "ordinal": anchor.get("ordinal") or 0})
    return {"files": files, "rejected": rejected, "collided": collided}


def _listing_sort_key(entry) -> tuple:
    identity = row_identity(entry.get("row"))
    m = _OUTLINE.match(identity)
    if m and identity == m.group(1):
        return (0, [int(p) for p in identity.split(".")], "", entry.get("ordinal") or 0)
    return (1, [], identity, entry.get("ordinal") or 0)


def order_listing(files) -> list:
    """Put a merged listing into TREE order, not sweep order. PURE.

    The traversal sweeps up and then down, so first-seen order depends on where the list
    happened to be sitting -- and that order goes straight into `make_fingerprint`, where a
    difference between two runs discards every partial on disk. Ordering by the row's own
    address instead makes the fingerprint a fact about the event.

    Outline rows sort numerically (`4.10` after `4.9`, the same trap `_outline_sort_key`
    exists for on the picker side), then unnumbered rows by their text, then by within-row
    ordinal so two links in one row keep their DOM order.
    """
    return sorted(files, key=_listing_sort_key)


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
        return None            # missing OR corrupt: callers discard partials and re-traverse


def write_manifest(pdir, fingerprint: dict) -> Path:
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"fingerprint": fingerprint}, indent=2))
    os.replace(tmp, path)
    return path


def write_omitted(bundle_path, omitted, expected_files, actual_files, collided: int = 0):
    """Durable record of what a bundle does NOT contain, beside the bundle itself.

    Written only when something is actually missing, so its ABSENCE is meaningful evidence that
    nothing is. It outlives the capture (the partial manifest does not), so a gap is greppable
    later without reading logs. Atomic (tmp + os.replace), the same way the ephemeral
    `write_manifest` is -- this is the durable artifact and deserves at least as much care.

    `collided` is the count of links the traversal found INDISTINGUISHABLE from one another
    and collapsed (`listing_from_anchors`'s `collided`, #174 Low) -- a gap that never shows up
    as a shortfall against `expected_files`, since the collapsed entry still counts once. It
    used to reach only the log, on the PROVISIONAL count-mismatch line; folding it in here
    makes it greppable on its own terms rather than inferred from a log a human has to have
    been watching.

    A no-op (returns None, touches nothing on disk) when there is nothing to record. Clearing a
    STALE record from an EARLIER run is a different decision with a different timing requirement
    -- see `clear_omitted_when_complete`, which must run only after the bundle itself exists.
    """
    if not omitted and not collided and expected_files == actual_files:
        return None
    path = Path(bundle_path).with_suffix(".omitted.json")
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = {"omitted": list(omitted), "expected_files": expected_files,
            "actual_files": actual_files}
    if collided:
        body["collided"] = collided
    tmp.write_text(json.dumps(body, indent=2))
    os.replace(tmp, path)
    return path


def clear_omitted_when_complete(bundle_path, omitted, expected_files, actual_files,
                                collided: int = 0):
    """Unlink a stale gap record left by an EARLIER run, now that this run is complete.

    Call this only AFTER `build_bundle` has returned successfully. A capture that once omitted
    a file and a later run that captures everything write the same bundle path, and a stale
    record left beside it would go on claiming a gap that no longer exists -- but "absence means
    nothing is missing" only holds if the bundle backing that claim actually landed. Clearing
    the record before `build_bundle` runs (or while it might still fail) would leave an
    incomplete or entirely absent `Doc<n>.zip` standing next to a gap record that was just
    erased -- exactly the state this whole mechanism exists to prevent.

    `collided` gates this the same way `omitted` does: counts matching is not evidence nothing
    is wrong when a collision was silently collapsed this run too.
    """
    if omitted or collided or expected_files != actual_files:
        return
    Path(bundle_path).with_suffix(".omitted.json").unlink(missing_ok=True)


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
    # The picker's `Total Number` counts ATTACHMENTS; `files` counts what the tree traversal
    # found. It is NOT yet established live that these count the same thing -- a nested archive
    # could render as one tree file but several picker attachments, which would make either
    # direction of mismatch a permanent phantom. So a shortfall is LOGGED loudly here and folded
    # into the durable `.omitted.json` below (via expected/actual), never refused: Respond dies
    # the instant a posting closes, so bytes beat strictness, and an unverified check must not be
    # able to block the only path that gets them. Do NOT tighten this into a raise without first
    # confirming live that the two counts are commensurable -- that is a distinct condition from
    # the zero-files case above, which stays fatal because it means the event withheld its
    # content outright, not that two counters merely disagree.
    if expected is not None and len(files) < expected:
        log(f"  Doc{document_number}: the traversal found {len(files)} file(s) against the "
            f"picker's {expected} — SHORT by {expected - len(files)}; recording the gap in "
            f"Doc{document_number}.omitted.json rather than refusing the capture")
    # Optional: a source may report how many links it found INDISTINGUISHABLE from one
    # another and collapsed (#174 Low). That gap never shows up as a shortfall against
    # `expected` -- the collapsed entry still counts once -- so it is read here, defensively
    # (the FileSource protocol's other three methods stay a hard requirement; this one is not),
    # and folded into the durable record below rather than living only in the traversal's log.
    collided_count = getattr(source, "collided_count", lambda: 0)() or 0
    fingerprint = make_fingerprint(files, expected)
    pdir = partial_dir(dest_dir, document_number)
    manifest = read_manifest(pdir)
    if manifest is None:
        # Missing OR corrupt: `read_manifest` cannot tell "no partials yet" from "a manifest we
        # cannot trust", so both must discard rather than fall through and adopt whatever is
        # already in `files/` POSITIONALLY -- the sibling batched capture (ariba_batch.py's
        # `capture_in_batches`) makes the identical call for the identical reason. Discarding an
        # empty or nonexistent directory costs nothing.
        if pdir.exists():
            log(f"  Doc{document_number}: missing/unreadable manifest — discarding partials "
                f"and restarting")
        shutil.rmtree(pdir, ignore_errors=True)
    elif manifest.get("fingerprint") != fingerprint:
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
            # Recorded by its DISAMBIGUATED zip name, not the raw label: `unique_names` exists
            # precisely because names collide, so a bare label ("report.pdf") cannot say WHICH
            # of two same-named documents this is. `zip_name` is already in scope for exactly
            # this entry.
            omitted.append(zip_name)
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
    #
    # Clearing a STALE record from an earlier run runs in the OPPOSITE order: only after
    # `build_bundle` has actually landed the new bundle, never before it or around a failure --
    # an incomplete or absent `Doc<n>.zip` standing beside a just-erased gap record would make
    # "absence means nothing is missing" false.
    target = dest_dir / f"Doc{document_number}.zip"
    write_omitted(target, omitted, expected, len(captured), collided=collided_count)
    build_bundle(captured, target)
    clear_omitted_when_complete(target, omitted, expected, len(captured),
                                collided=collided_count)
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
            # Disambiguated zip name, not the raw label -- see the identical comment in
            # capture_files.
            omitted.append(zip_name)

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
    # Gap record first, and the stale-clear only after `build_bundle` succeeds -- for the same
    # reasons capture_files does both: see the comments there.
    write_omitted(target, omitted, fingerprint.get("expected_count"), len(captured))
    build_bundle(captured, target)
    clear_omitted_when_complete(target, omitted, fingerprint.get("expected_count"), len(captured))
    if leftover:
        log(f"  Doc{document_number}: {len(leftover)} interrupted transfer(s) kept in {pdir} — "
            f"bytes that can never be re-fetched are not deleted")
    else:
        shutil.rmtree(pdir, ignore_errors=True)
    log(f"  Doc{document_number}: closed mid-capture — salvaged {len(captured)} file(s) -> "
        f"{target.name}")
    return target
