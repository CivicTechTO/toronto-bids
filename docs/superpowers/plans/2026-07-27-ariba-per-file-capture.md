# Ariba Per-File Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture each Ariba solicitation document individually from the event's content tree and zip them into the canonical `Doc<n>.zip`, replacing the server-zipped-bundle path and retiring the 500 MB ceiling as a concern.

**Architecture:** All logic sits above a 3-method `FileSource` protocol in a new pure module (`sources/ariba_files.py`), unit-testable without a browser. One thin adapter (`AribaFileSource`, in `sources/ariba_attachments.py`) touches Playwright. The new acquisition path feeds the **existing** storage path — `store_bundle`, `index_zip`, the index, the export and the "already archived" check are unchanged.

**Tech Stack:** Python 3.12+, `zipfile`/`shutil` stdlib, pytest, Playwright (adapter only).

**Spec:** `docs/superpowers/specs/2026-07-27-ariba-per-file-capture-design.md`

## Global Constraints

- **Every individual file is small** — measured `Max Size 88.7 MB` across `Total Number 54`. No ceiling is ever in play on this path.
- **`download()` writes to exactly the path it is given.** `.part` staging and `os.replace` live in `capture_files`, never in the adapter — atomicity is the property most worth testing and the adapter cannot be unit-tested.
- **A file present on disk is complete by construction.** That is what makes resume "skip what is there" with no sidecar.
- **A failed download's `.part` is deleted immediately.** Never useful, can be 88 MB.
- **Zero files found → raise, no bundle written.** An empty `Doc<n>.zip` would mark the event archived forever. Empty is never success.
- **Count mismatch → record, do not refuse.** Respond dies at close; bytes beat strictness.
- **The count check is PROVISIONAL** — it is not established that the picker's attachment count and the tree's file count are commensurable. Task 5 validates the check itself against the known 54.
- **Flat zip names**, first occurrence unchanged, duplicates `_2`, `_3`, … deterministic in traversal order.
- **`ariba_batch.py` is NOT deleted** in this plan. It stays, tested and unused by the new path.
- No lint/format/typecheck exists in this repo. Run tests from `scrapers/`: `cd scrapers && uv run pytest`.

---

## File Structure

| file | responsibility |
|---|---|
| `scrapers/toronto_bids/sources/ariba_files.py` **(create)** | Pure: flat naming, atomic bundle build, the acquisition loop, resume, manifest, omitted record. No Playwright import. |
| `scrapers/tests/test_ariba_files.py` **(create)** | `FakeFileSource` + every offline test. |
| `scrapers/toronto_bids/sources/ariba_attachments.py` **(modify)** | `AribaFileSource` adapter; `capture_event` switched to the per-file path. |

**Deliberate duplication:** `ariba_files.py` defines its own `partial_dir` / manifest / `write_omitted` helpers rather than importing `ariba_batch`'s. They are a few lines each, and taking a dependency on a module slated for deletion would invert the retirement order. Note it in the module docstring so it reads as a choice, not an oversight.

---

### Task 1: Flat naming and the atomic bundle build

**Files:**
- Create: `scrapers/toronto_bids/sources/ariba_files.py`
- Test: `scrapers/tests/test_ariba_files.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `unique_names(names) -> list[str]`, `build_bundle(files, target) -> Path` where `files` is `[(disk_path, zip_name)]`.

- [ ] **Step 1: Write the failing test**

Create `scrapers/tests/test_ariba_files.py`:

```python
"""Per-file capture of Ariba solicitation documents (#174).

The bundle path could not reach Doc5713434353: row 3.1 is atomic in the picker and holds
787.71 MB of a 792 MB event. But every individual file is <= 88.7 MB, and the event page
exposes each one. This module captures them one at a time and builds the canonical zip
ourselves -- so WE choose its layout, which is why naming is a real concern here and was not
before.

Design: docs/superpowers/specs/2026-07-27-ariba-per-file-capture-design.md
"""
import json
import zipfile

import pytest

from toronto_bids.sources import ariba_files


def test_distinct_names_are_untouched():
    assert ariba_files.unique_names(["a.pdf", "b.pdf"]) == ["a.pdf", "b.pdf"]


def test_a_duplicate_gets_a_suffix_and_the_first_keeps_its_name():
    assert ariba_files.unique_names(["a.pdf", "a.pdf"]) == ["a.pdf", "a_2.pdf"]


def test_three_of_a_kind_number_upward():
    assert ariba_files.unique_names(["a.pdf"] * 3) == ["a.pdf", "a_2.pdf", "a_3.pdf"]


def test_a_name_with_no_extension_still_disambiguates():
    assert ariba_files.unique_names(["README", "README"]) == ["README", "README_2"]


def test_a_suffix_that_would_itself_collide_keeps_searching():
    """`a_2.pdf` already exists, so the second `a.pdf` must not steal it."""
    assert ariba_files.unique_names(["a.pdf", "a_2.pdf", "a.pdf"]) == [
        "a.pdf", "a_2.pdf", "a_3.pdf"]


def test_naming_is_deterministic_in_traversal_order():
    once = ariba_files.unique_names(["x.pdf", "y.pdf", "x.pdf"])
    twice = ariba_files.unique_names(["x.pdf", "y.pdf", "x.pdf"])
    assert once == twice == ["x.pdf", "y.pdf", "x_2.pdf"]


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_build_bundle_zips_files_flat_under_their_given_names(tmp_path):
    a = _write(tmp_path / "src" / "a.pdf", b"aaa")
    b = _write(tmp_path / "src" / "b.pdf", b"bb")

    target = ariba_files.build_bundle([(a, "a.pdf"), (b, "b_2.pdf")], tmp_path / "Doc1.zip")

    with zipfile.ZipFile(target) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b_2.pdf"]
        assert zf.read("a.pdf") == b"aaa"


def test_build_bundle_is_atomic_leaving_no_target_on_failure(tmp_path):
    """A half-written Doc<n>.zip would be indistinguishable from a finished capture."""
    a = _write(tmp_path / "src" / "a.pdf", b"aaa")
    missing = tmp_path / "src" / "gone.pdf"
    target = tmp_path / "Doc2.zip"

    with pytest.raises(Exception):
        ariba_files.build_bundle([(a, "a.pdf"), (missing, "gone.pdf")], target)

    assert not target.exists()
    assert not target.with_suffix(".zip.tmp").exists()


def test_build_bundle_overwrites_an_existing_target(tmp_path):
    target = tmp_path / "Doc3.zip"
    target.write_bytes(b"stale")
    a = _write(tmp_path / "src" / "a.pdf", b"aaa")

    ariba_files.build_bundle([(a, "a.pdf")], target)

    with zipfile.ZipFile(target) as zf:
        assert zf.namelist() == ["a.pdf"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_ariba_files.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'toronto_bids.sources.ariba_files'`

- [ ] **Step 3: Write minimal implementation**

Create `scrapers/toronto_bids/sources/ariba_files.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scrapers && uv run pytest tests/test_ariba_files.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_files.py scrapers/tests/test_ariba_files.py
git commit -m "feat(ariba): flat zip naming and atomic bundle build for per-file capture (#174)"
```

---

### Task 2: The acquisition loop

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_files.py`
- Test: `scrapers/tests/test_ariba_files.py`

**Interfaces:**
- Consumes: `unique_names`, `build_bundle` from Task 1.
- Produces: `partial_dir(dest_dir, document_number) -> Path`, `capture_files(source, document_number, dest_dir, log=...) -> Path | None`. Raises `RuntimeError` when the source lists zero files.

- [ ] **Step 1: Write the failing test**

Append to `scrapers/tests/test_ariba_files.py`:

```python
class FakeFileSource:
    """FileSource stand-in. `fail_on` names files whose download raises."""

    def __init__(self, names, expected=None, fail_on=(), contents=None):
        self.names = list(names)
        self.expected = expected if expected is not None else len(names)
        self.fail_on = set(fail_on)
        self.contents = contents or {}
        self.downloaded = []

    def list_files(self):
        return [{"key": str(i), "name": n, "row": n} for i, n in enumerate(self.names)]

    def expected_count(self):
        return self.expected

    def download(self, file, dest):
        if file["name"] in self.fail_on:
            raise RuntimeError(f"boom: {file['name']}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.contents.get(file["name"], file["name"].encode()))
        self.downloaded.append(file["name"])
        return dest


def test_a_clean_run_captures_every_file_and_builds_the_bundle(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"])

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    assert bundle == tmp_path / "Doc5713434353.zip"
    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b.pdf"]
    assert not ariba_files.partial_dir(tmp_path, "5713434353").exists()
    assert not (tmp_path / "Doc5713434353.omitted.json").exists()


def test_zero_files_raises_and_writes_no_bundle(tmp_path):
    """An event withholding its content must not be marked archived forever."""
    source = FakeFileSource([])

    with pytest.raises(RuntimeError, match="no files"):
        ariba_files.capture_files(source, "5713434353", tmp_path)

    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_one_failed_file_does_not_abort_the_others(tmp_path):
    source = FakeFileSource(["a.pdf", "bad.pdf", "c.pdf"], fail_on=["bad.pdf"])

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "c.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["bad.pdf"]
    assert body["expected_files"] == 3 and body["actual_files"] == 2


def test_a_failed_download_leaves_no_part_file_behind(tmp_path):
    source = FakeFileSource(["a.pdf", "bad.pdf"], fail_on=["bad.pdf"])

    ariba_files.capture_files(source, "5713434353", tmp_path)

    assert list(tmp_path.rglob("*.part")) == []


def test_resume_skips_files_already_complete_on_disk(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"])
    fdir = ariba_files.partial_dir(tmp_path, "5713434353") / "files"
    fdir.mkdir(parents=True)
    (fdir / "a.pdf").write_bytes(b"already here")
    ariba_files.write_manifest(
        ariba_files.partial_dir(tmp_path, "5713434353"),
        ariba_files.make_fingerprint(["a.pdf", "b.pdf"], 2))

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    assert source.downloaded == ["b.pdf"]           # a.pdf was NOT re-fetched
    with zipfile.ZipFile(bundle) as zf:
        assert zf.read("a.pdf") == b"already here"


def test_a_changed_event_discards_the_partials(tmp_path):
    """An addendum landed between runs -- partials describe a different version."""
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "stale.pdf").write_bytes(b"old")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(["stale.pdf"], 1))

    source = FakeFileSource(["a.pdf", "b.pdf"])
    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b.pdf"]   # stale.pdf gone


def test_a_count_mismatch_records_rather_than_refusing(tmp_path):
    """Respond dies at close, so bytes beat strictness."""
    source = FakeFileSource(["a.pdf"], expected=54)

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    assert bundle.exists()
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["expected_files"] == 54 and body["actual_files"] == 1


def test_an_unknown_expected_count_is_recorded_as_unknown_not_zero(tmp_path):
    source = FakeFileSource(["a.pdf"], expected=None)

    ariba_files.capture_files(source, "5713434353", tmp_path)

    path = tmp_path / "Doc5713434353.omitted.json"
    if path.exists():
        assert json.loads(path.read_text())["expected_files"] is None


def test_duplicate_names_across_the_tree_are_both_kept(tmp_path):
    source = FakeFileSource(["dup.pdf", "dup.pdf"],
                            contents={"dup.pdf": b"first"})

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["dup.pdf", "dup_2.pdf"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_ariba_files.py -q`
Expected: FAIL — `AttributeError: module 'toronto_bids.sources.ariba_files' has no attribute 'partial_dir'`

- [ ] **Step 3: Write minimal implementation**

Append to `scrapers/toronto_bids/sources/ariba_files.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scrapers && uv run pytest tests/test_ariba_files.py -q`
Expected: PASS (18 passed)

- [ ] **Step 5: Run the full suite**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS, no failures (704 existing + 18 new)

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_files.py scrapers/tests/test_ariba_files.py
git commit -m "feat(ariba): per-file acquisition loop with resume and count check (#174)"
```

---

### Task 3: The Playwright adapter

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_attachments.py`

**Interfaces:**
- Produces: `class AribaFileSource` with `list_files()`, `download(file, dest)`, `expected_count()`.

**No unit tests for this task** — the repo does not unit-test browser-driving code (`login`, `capture_event`, `discover_report_urls`, `AribaPicker` are all untested by design). Task 5's live run is its verification. Keep it thin: every decision that can be made without a browser already lives in `ariba_files.py`.

**Measured facts about the surface** (from probes — do not re-derive):
- The event page's `All Content` view lists rows `1`, `1.1`, `2`, `2.1`, … Attachment rows carry the filename as an `<a>`, e.g. `Part 1 - RFT Process.pdf`.
- Clicking that filename opens a small menu containing `Download this attachment` and `Download all attachments`.
- **A probe timed out clicking `Download this attachment` via `.first`** — there are three such elements (one per attachment row) and `.first` matched a hidden one. Scope to the *visible* one.
- Rows `2.1` and `3.1` carry a `References⌄` toggle; the bulk of the files are behind it (row `3.1` alone totals 787.71 MB across many files, max 88.7 MB each).
- The page itself scrolls (`page range ~277 px`), so the tree may not render fully at once.

- [ ] **Step 1: Add the adapter**

Append to `scrapers/toronto_bids/sources/ariba_attachments.py`, after `AribaPicker`:

```python
_FILENAME = re.compile(r"\.(pdf|zip|docx?|xlsx?|dwg|rtf|txt|jpe?g|png|csv|pptx?)$", re.I)


class AribaFileSource:
    """Playwright adapter satisfying ariba_files' FileSource protocol (#174).

    The bundle path could not reach this event's documents: picker row 3.1 is atomic and holds
    787.71 MB. The event page's `All Content` view exposes the same documents INDIVIDUALLY,
    each with its own "Download this attachment" menu, and no file exceeds 88.7 MB -- so no
    ceiling is ever in play here.

    Thin on purpose. Naming, resume, atomicity and the count check all live in ariba_files,
    which is unit-tested; this class only traverses and clicks.
    """

    def __init__(self, page, log=lambda _m: None):
        self.page = page
        self.log = log

    # --- traversal -----------------------------------------------------------------------
    def _expand_references(self) -> int:
        """Open every `References` toggle; the bulk of the files live behind them."""
        opened = 0
        for _ in range(20):
            links = self.page.get_by_text("References", exact=False)
            n = links.count()
            progressed = False
            for i in range(n):
                item = links.nth(i)
                try:
                    if not item.is_visible():
                        continue
                    item.click(timeout=5000)
                    self.page.wait_for_timeout(600)
                    opened += 1
                    progressed = True
                except Exception:                 # noqa: BLE001 — one toggle, not the run
                    continue
            if not progressed:
                break
        return opened

    def list_files(self) -> list:
        """Every downloadable document in the content tree, in traversal order.

        Scrolls and expands until repeated passes find nothing new, then LOGS the count. A
        traversal that quietly sees 6 rows instead of 60 is the failure that matters here, and
        #174 spent six live runs learning that a silent short read looks exactly like success.
        """
        self.page.keyboard.press("Home")
        self.page.wait_for_timeout(500)
        opened = self._expand_references()

        seen, out = set(), []
        for _ in range(30):
            before = len(seen)
            for entry in self.page.evaluate(
                """() => Array.from(document.querySelectorAll('a'))
                     .map(e => ({name: (e.innerText || '').trim(),
                                 row: ((e.closest('tr') || {}).innerText || '')
                                        .replace(/\\s+/g, ' ').trim().slice(0, 120)}))
                     .filter(x => x.name)"""
            ):
                name = entry["name"]
                if not _FILENAME.search(name) or name.startswith("http"):
                    continue
                key = (name, entry["row"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({"key": str(len(out)), "name": name, "row": entry["row"]})
            self.page.mouse.wheel(0, 1200)
            self.page.wait_for_timeout(400)
            if len(seen) == before:
                break

        self.log(f"    content tree: {len(out)} file(s), {opened} References section(s) expanded")
        return out

    # --- download ------------------------------------------------------------------------
    def download(self, file: dict, dest) -> Path:
        """Save ONE document to exactly `dest`, or raise.

        `.part` staging and the rename are the caller's job (ariba_files) -- this writes where
        it is told. Opening the filename's menu is required: the "Download this attachment"
        entries exist for every attachment row and only one is visible at a time, so a `.first`
        locator picks a hidden one and times out (observed).
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        link = self.page.get_by_text(file["name"], exact=True).first
        link.scroll_into_view_if_needed(timeout=10000)
        link.click(timeout=15000)
        self.page.wait_for_timeout(600)
        item = self.page.get_by_text("Download this attachment", exact=False)
        visible = None
        for i in range(item.count()):
            if item.nth(i).is_visible():
                visible = item.nth(i)
                break
        if visible is None:
            raise RuntimeError(
                f"{file['name']}: the menu did not open (no visible "
                f"'Download this attachment' among {item.count()} candidates)")
        with self.page.expect_download(timeout=300000) as dl:
            visible.click()
        dl.value.save_as(str(dest))
        return dest

    # --- the picker's count, and nothing else --------------------------------------------
    def expected_count(self) -> int | None:
        """The picker's authoritative `Total Number`, read WITHOUT downloading anything.

        An independent ground truth: the content tree could hide a file behind a References
        section that never expanded, and the traversal would never know. PROVISIONAL -- it is
        not yet established that the picker's attachment count and the tree's file count are
        commensurable (see the spec); Task 5 validates the check itself against the known 54.
        """
        try:
            dc = self.page.get_by_role("button", name="Download Content")
            da = self.page.get_by_role("button", name="Download Attachments")
            dc.click()
            try:
                da.first.wait_for(state="visible", timeout=30000)
            except Exception:
                dc.click()
                da.first.wait_for(state="visible", timeout=30000)
            da.first.click()
            self.page.wait_for_selector(f"text={PICKER_HEADING}", timeout=45000)
            _select_all_attachments(self.page, log=self.log)
            count = AribaPicker(self.page, log=self.log).file_count()
            self.page.get_by_role("button", name="Done").first.click()
            self.page.wait_for_timeout(1500)
            return count
        except Exception as exc:                  # noqa: BLE001 — advisory, never blocks
            self.log(f"    could not read the picker's file count ({exc}) — recording unknown")
            return None
```

- [ ] **Step 2: Verify the module imports and the suite is unchanged**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS, same count as Task 2 (this adds no tests; it proves nothing broke)

- [ ] **Step 3: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_attachments.py
git commit -m "feat(ariba): AribaFileSource adapter over the event content tree (#174)"
```

---

### Task 4: Switch capture_event to the per-file path

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_attachments.py` (`capture_event`)

**Interfaces:**
- Consumes: `AribaFileSource` (Task 3), `ariba_files.capture_files` (Task 2).
- Produces: no new public names; `capture_event` keeps its signature and `Path | None` return.

**Sequencing, from reading the current function:** after `_wait_post_respond` returns `"event"` and the cookie banner is dismissed, the page is on the **event page**, which is where `All Content` lives. The picker is a detour reached via `Download Content` and returned from via `Done`. So read the count FIRST (cheap, fails early), then traverse and download.

- [ ] **Step 1: Replace the picker download with per-file capture**

In `capture_event`, replace everything from the `# Download Content -> the Export-to-Excel page ...` comment through the end of the batched/single download logic with:

```python
    # Per-file capture (#174). The bundle path is retired here: it server-zips a SELECTION and
    # is hard-stopped at 500 MB, which cannot reach an event whose row 3.1 is atomic at
    # 787.71 MB. Every individual file is <= 88.7 MB, so this path has no ceiling at all.
    source = AribaFileSource(page, log=log)
    # Count first: it is cheap, it fails early, and it returns to the event page via Done --
    # which is where the content tree we are about to traverse lives.
    expected = source.expected_count()
    if expected is not None:
        log(f"  Doc{document_number}: picker reports {expected} attachment(s)")
    return ariba_files.capture_files(source, document_number, dest_dir, log=log)
```

Add the import beside the other `toronto_bids` imports:

```python
from toronto_bids.sources import ariba_files
```

Update `capture_event`'s docstring: it no longer drives Download Attachments to fetch a bundle, and an event over 500 MB is no longer a special case.

**Note:** `expected_count()` is called here for logging and inside `capture_files` via the protocol. Calling it twice would drive the picker twice. Either cache it on the source (an instance attribute set on first read) or drop the log line — the implementer should pick one and say which; do NOT leave it driving the picker twice.

- [ ] **Step 2: Run the full suite**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS, no failures

- [ ] **Step 3: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_attachments.py
git commit -m "feat(ariba): capture_event captures documents per file, retiring the bundle path (#174)"
```

---

### Task 5: Live acceptance run — the definition of done

**Files:** none — this is verification, and **the work is not finished until it passes.**

- [ ] **Step 1: Run the capture live**

```bash
cd scrapers
set -a && . ~/.config/toronto-bids/tb.env && set +a
TB_DATA_DIR="$HOME/tb-data" nohup uv run tb enrich-ariba-attachments --capture --virtual-display \
  > /tmp/per-file-run.log 2>&1 &
sleep 20 && tail -5 /tmp/per-file-run.log      # confirm it actually STARTED before waiting on it
```

Expected, roughly:

```
  Doc5713434353: picker reports 54 attachment(s)
    content tree: 54 file(s), 2 References section(s) expanded
  Doc5713434353: captured 54 file(s) -> Doc5713434353.zip
  bundles captured: 1
```

- [ ] **Step 2: Verify the artifacts on disk, not the log**

```bash
ls -la ~/tb-data/ariba/attachments/Doc5713434353.zip
python3 -c "import zipfile; z=zipfile.ZipFile('$HOME/tb-data/ariba/attachments/Doc5713434353.zip'); print(len(z.namelist()),'entries'); print(z.testzip())"
ls -d ~/tb-data/ariba/attachments/.partial/Doc5713434353 2>/dev/null || echo "partials cleaned up"
cat ~/tb-data/ariba/attachments/Doc5713434353.omitted.json 2>/dev/null || echo "no omissions"
```

**Done means all of:** the zip exists and `testzip()` returns `None`; its entry count matches the tree; Part 3's drawings are present; `.partial/` is gone. Anything less is reported as unfinished regardless of how the tests look.

- [ ] **Step 3: Validate the PROVISIONAL count check**

Compare the picker's `Total Number` against the tree's file count in the log. If they match (both 54), the check is sound — record that in the spec. **If they differ, the check is comparing incommensurable things:** fix the comparison or remove it, and do not leave it emitting false gap records.

- [ ] **Step 4: Confirm the index picked it up**

```bash
cd scrapers && TB_DATA_DIR="$HOME/tb-data" uv run tb status | head -20
```

Expected: `ariba_attachment` rises by the bundle's leaf count; the bundle appears under 50 solicitations rather than 49.

- [ ] **Step 5: Record the result and commit any doc updates**

Update CLAUDE.md's Ariba attachments section: capture is per-file, the 500 MB ceiling no longer applies, and note the measured shape of this event (row 3.1 atomic at 787.71 MB, 54 files, max 88.7 MB).

```bash
git add CLAUDE.md docs/superpowers/specs/2026-07-27-ariba-per-file-capture-design.md
git commit -m "docs: record per-file capture verified live (#174)"
```

---

## Self-Review

**Spec coverage:** per-file always (Tasks 3-4) · flat names + `_2` suffix (Task 1) · atomic bundle (Task 1) · `download` writes to exactly `dest`, staging in `capture_files` (Tasks 2-3) · resume skips complete files (Task 2) · `.part` deleted on failure (Task 2) · one failure does not abort (Task 2) · zero files raises, no bundle (Task 2) · count recorded not refused (Task 2) · fingerprint discards on change (Task 2) · picker reduced to `expected_count` only (Task 3) · existing storage path unchanged (no task touches `store_bundle`/`index_zip`/models) · `ariba_batch.py` not deleted (no task removes it) · count check validated live (Task 5).

**Placeholder scan:** every step carries the code or the exact command. The one open decision — caching `expected_count` versus dropping the log line — is called out explicitly with both options and a requirement to state which was chosen, rather than left silent.

**Type consistency:** `capture_files(source, document_number, dest_dir, log)` returns `Path | None` in Tasks 2 and 4 · `build_bundle(files, target)` takes `[(disk_path, zip_name)]` in Tasks 1 and 2 · `partial_dir(dest_dir, document_number)` argument order consistent · `download(file, dest)` writes to `dest` in Tasks 2 and 3 · `AribaFileSource` implements exactly the three protocol methods `FakeFileSource` does.
