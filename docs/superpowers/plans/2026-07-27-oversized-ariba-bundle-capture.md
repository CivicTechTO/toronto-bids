# Oversized Ariba Bundle Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture Ariba solicitation attachments for events whose bundle exceeds Ariba's 500 MB single-zip limit, by selecting subsets of the picker, downloading each as its own zip, and merging them into the one canonical `Doc<n>.zip` the rest of the pipeline already understands.

**Architecture:** All batching logic sits above a 5-method `Picker` protocol in a new pure module (`sources/ariba_batch.py`), so it is unit-testable without a browser. Only a thin adapter (`AribaPicker`, in the existing `sources/ariba_attachments.py`) touches Playwright. `capture_event` gains one branch; `capture_attachments` is unchanged.

**Tech Stack:** Python 3.12+, `zipfile`/`shutil` stdlib, pytest, Playwright (adapter only).

**Spec:** `docs/superpowers/specs/2026-07-27-oversized-ariba-bundle-capture-design.md`

## Global Constraints

- **Batch threshold is 450 MB**, not 500 — Ariba's limit applies to its own computed total.
- **Rows are keyed by outline number** (`5.2.1.2.1.3`), **never by DOM index**. The picker list is virtualised: a fixed 51 checkboxes render as a sliding window over ~85 logical rows, so indices are unstable across runs *and* across scrolls within a run.
- **Only the top `Title` checkbox cascades.** Intermediate rows select only themselves and contribute no size.
- **Poll, never sleep a guess.** Every selection change is an async recompute (~1.5 s per row toggle, ~10 s for select-all). This is the #174 root cause; do not reintroduce fixed waits.
- **Refuse rather than invent.** An unmeasurable total aborts; a merge collision refuses; a shortfall is recorded, never silently accepted.
- **No schema change.** Incomplete/omitted state lives in JSON on disk, not in a new column.
- **No browser in any unit test.**
- Run tests from `scrapers/`: `cd scrapers && uv run pytest`.

---

## File Structure

| file | responsibility |
|---|---|
| `scrapers/toronto_bids/sources/ariba_batch.py` **(create)** | Pure: manifest I/O, the greedy batching loop over the `Picker` protocol, zip merging, the omitted record. No Playwright import. |
| `scrapers/tests/test_ariba_batch.py` **(create)** | `FakePicker` + every offline test. |
| `scrapers/toronto_bids/sources/ariba_attachments.py` **(modify)** | `AribaPicker` browser adapter; `capture_event` branch; closed-with-partials rule. Already 585 lines — new logic goes in `ariba_batch.py`, not here. |

---

### Task 1: Manifest and fingerprint

**Files:**
- Create: `scrapers/toronto_bids/sources/ariba_batch.py`
- Test: `scrapers/tests/test_ariba_batch.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `BATCH_THRESHOLD_MB: int`, `partial_dir(dest_dir, document_number) -> Path`, `make_fingerprint(row_keys, file_count, total_mb) -> dict`, `read_manifest(pdir) -> dict | None`, `write_manifest(pdir, fingerprint, batches, omitted) -> Path`, `manifest_is_current(manifest, fingerprint) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `scrapers/tests/test_ariba_batch.py`:

```python
"""Batched capture for events over Ariba's 500 MB single-zip ceiling (#174).

Design: docs/superpowers/specs/2026-07-27-oversized-ariba-bundle-capture-design.md
"""
import json
import zipfile

import pytest

from toronto_bids.sources import ariba_batch


def test_partial_dir_is_outside_the_canonical_namespace(tmp_path):
    """A partial capture must never look like a finished bundle to capture_attachments."""
    p = ariba_batch.partial_dir(tmp_path, "5713434353")
    assert p == tmp_path / ".partial" / "Doc5713434353"
    assert not str(p).endswith("Doc5713434353.zip")


def test_manifest_round_trips(tmp_path):
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    fp = ariba_batch.make_fingerprint(["1.1", "2.1"], file_count=54, total_mb=792.41)

    ariba_batch.write_manifest(pdir, fp, batches=[["1.1"]], omitted=["9.9"])
    got = ariba_batch.read_manifest(pdir)

    assert got["fingerprint"] == fp
    assert got["batches"] == [["1.1"]]
    assert got["omitted"] == ["9.9"]


def test_read_manifest_is_none_when_absent(tmp_path):
    assert ariba_batch.read_manifest(tmp_path / "nope") is None


def test_a_matching_fingerprint_is_current(tmp_path):
    fp = ariba_batch.make_fingerprint(["1.1", "2.1"], 54, 792.41)
    assert ariba_batch.manifest_is_current({"fingerprint": fp}, fp) is True


def test_added_addenda_make_the_plan_stale(tmp_path):
    """This event gained four addenda mid-capture; a stale plan must not merge two versions."""
    old = ariba_batch.make_fingerprint(["1.1", "2.1"], 54, 792.41)
    new = ariba_batch.make_fingerprint(["1.1", "2.1", "6.4"], 55, 801.0)
    assert ariba_batch.manifest_is_current({"fingerprint": old}, new) is False


def test_a_manifest_without_a_fingerprint_is_not_current():
    fp = ariba_batch.make_fingerprint(["1.1"], 1, 1.0)
    assert ariba_batch.manifest_is_current({}, fp) is False
    assert ariba_batch.manifest_is_current(None, fp) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_ariba_batch.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'toronto_bids.sources.ariba_batch'`

- [ ] **Step 3: Write minimal implementation**

Create `scrapers/toronto_bids/sources/ariba_batch.py`:

```python
"""Batched capture for Ariba events over the 500 MB single-zip ceiling (#174).

Ariba hard-stops a single bundle at 500 MB and says so in the picker: "You must select
specific items and perform multiple downloads." This module is the "select specific items"
half -- pure logic over a 5-method Picker protocol, so it is testable without a browser.
The Playwright adapter lives in ariba_attachments.py.

Design: docs/superpowers/specs/2026-07-27-oversized-ariba-bundle-capture-design.md
"""
import json
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scrapers && uv run pytest tests/test_ariba_batch.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_batch.py scrapers/tests/test_ariba_batch.py
git commit -m "feat(ariba): partial-capture manifest and fingerprint (#174)"
```

---

### Task 2: The greedy batching loop

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_batch.py`
- Test: `scrapers/tests/test_ariba_batch.py`

**Interfaces:**
- Consumes: `BATCH_THRESHOLD_MB` from Task 1.
- Produces: `accumulate_batches(picker, threshold_mb=BATCH_THRESHOLD_MB, skip_keys=(), log=...) -> tuple[list[list[str]], list[str]]` — yields `(batches, omitted)` where each batch is a list of outline keys. Raises `RuntimeError` when `picker.total_mb()` returns `None`.

**Picker protocol** (structural; both `AribaPicker` and `FakePicker` satisfy it):

```
row_keys()               -> list[str]
set_selected(key, bool)  -> None
total_mb()               -> float | None
file_count()             -> int
download_to(path)        -> Path
```

- [ ] **Step 1: Write the failing test**

Append to `scrapers/tests/test_ariba_batch.py`:

```python
class FakePicker:
    """Picker stand-in with a known size table. Keys only -- an int key is a bug.

    The real list is virtualised (a fixed 51 rendered checkboxes sliding over ~85 logical
    rows), so any code that addresses a row by index is wrong. This fake enforces that by
    refusing non-string keys outright.
    """

    def __init__(self, sizes: dict, unmeasurable=False):
        self.sizes = dict(sizes)          # {outline key: MB}
        self.unmeasurable = unmeasurable
        self.selected = set()
        self.downloads = []

    def row_keys(self):
        return list(self.sizes)

    def set_selected(self, key, value):
        if not isinstance(key, str):
            raise AssertionError(f"row addressed by {type(key).__name__}, not outline key")
        if key not in self.sizes:
            raise KeyError(key)
        self.selected.add(key) if value else self.selected.discard(key)

    def total_mb(self):
        if self.unmeasurable:
            return None
        return round(sum(self.sizes[k] for k in self.selected), 2)

    def file_count(self):
        return len([k for k in self.selected if self.sizes[k] > 0])

    def download_to(self, path):
        path.write_bytes(b"zip")
        self.downloads.append(sorted(self.selected))
        return path


def test_batches_never_exceed_the_threshold():
    picker = FakePicker({"1.1": 200, "1.2": 200, "1.3": 200, "1.4": 100})

    batches, omitted = ariba_batch.accumulate_batches(picker, threshold_mb=450)

    assert omitted == []
    for batch in batches:
        assert sum(picker.sizes[k] for k in batch) <= 450
    assert [k for b in batches for k in b] == ["1.1", "1.2", "1.3", "1.4"]


def test_it_packs_greedily_in_row_order():
    picker = FakePicker({"1.1": 300, "1.2": 100, "1.3": 400})

    batches, _ = ariba_batch.accumulate_batches(picker, threshold_mb=450)

    assert batches == [["1.1", "1.2"], ["1.3"]]


def test_a_single_row_over_the_ceiling_is_omitted_not_retried_forever():
    """It can never be captured; the bundle must still be able to complete."""
    picker = FakePicker({"1.1": 100, "big": 600, "1.2": 100})

    batches, omitted = ariba_batch.accumulate_batches(picker, threshold_mb=450)

    assert omitted == ["big"]
    assert [k for b in batches for k in b] == ["1.1", "1.2"]


def test_an_unmeasurable_total_aborts_rather_than_guessing():
    """#174 was a guard gone blind; a batcher that cannot measure must not proceed."""
    picker = FakePicker({"1.1": 10}, unmeasurable=True)

    with pytest.raises(RuntimeError, match="could not read"):
        ariba_batch.accumulate_batches(picker, threshold_mb=450)


def test_rows_are_addressed_by_key_never_by_index():
    """FakePicker raises on a non-str key; this passing proves no index addressing."""
    picker = FakePicker({"5.2.1.2.1.3": 10, "6.2.1": 20})

    batches, _ = ariba_batch.accumulate_batches(picker, threshold_mb=450)

    assert batches == [["5.2.1.2.1.3", "6.2.1"]]


def test_already_completed_keys_are_skipped_on_resume():
    picker = FakePicker({"1.1": 100, "1.2": 100, "1.3": 100})

    batches, _ = ariba_batch.accumulate_batches(
        picker, threshold_mb=450, skip_keys={"1.1", "1.2"})

    assert batches == [["1.3"]]


def test_it_leaves_nothing_selected_between_batches():
    """A flushed batch must not leak into the next one."""
    picker = FakePicker({"1.1": 300, "1.2": 300})

    ariba_batch.accumulate_batches(picker, threshold_mb=450)

    assert picker.selected == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_ariba_batch.py -q`
Expected: FAIL — `AttributeError: module 'toronto_bids.sources.ariba_batch' has no attribute 'accumulate_batches'`

- [ ] **Step 3: Write minimal implementation**

Append to `scrapers/toronto_bids/sources/ariba_batch.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scrapers && uv run pytest tests/test_ariba_batch.py -q`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_batch.py scrapers/tests/test_ariba_batch.py
git commit -m "feat(ariba): greedy row-by-row batching under the 450 MB threshold (#174)"
```

---

### Task 3: Merging batch zips into the canonical bundle

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_batch.py`
- Test: `scrapers/tests/test_ariba_batch.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `merge_bundles(part_paths, target, expected_files=None, log=...) -> tuple[Path, int]` returning `(target, merged_member_count)`; `write_omitted(bundle_path, omitted, expected_files, actual_files) -> Path | None`.

- [ ] **Step 1: Write the failing test**

Append to `scrapers/tests/test_ariba_batch.py`:

```python
def _make_zip(path, files: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def test_merge_reproduces_a_single_download(tmp_path):
    """Merging top-level members yields the layout one download would have produced."""
    a = _make_zip(tmp_path / "batch-01.zip", {"Part1.pdf": b"aaa", "Part2.pdf": b"bb"})
    b = _make_zip(tmp_path / "batch-02.zip", {"Drawings.zip": b"PK-not-really"})

    target, count = ariba_batch.merge_bundles([a, b], tmp_path / "Doc1.zip")

    assert count == 3
    with zipfile.ZipFile(target) as zf:
        assert sorted(zf.namelist()) == ["Drawings.zip", "Part1.pdf", "Part2.pdf"]
        assert zf.read("Part1.pdf") == b"aaa"


def test_merge_preserves_crc_so_the_index_is_unchanged(tmp_path):
    a = _make_zip(tmp_path / "batch-01.zip", {"A.pdf": b"hello world"})
    before = {i.filename: i.CRC for i in zipfile.ZipFile(a).infolist()}

    target, _ = ariba_batch.merge_bundles([a], tmp_path / "Doc2.zip")

    after = {i.filename: i.CRC for i in zipfile.ZipFile(target).infolist()}
    assert after == before


def test_a_name_collision_refuses_rather_than_overwriting(tmp_path):
    a = _make_zip(tmp_path / "batch-01.zip", {"Same.pdf": b"one"})
    b = _make_zip(tmp_path / "batch-02.zip", {"Same.pdf": b"two"})

    with pytest.raises(RuntimeError, match="collision"):
        ariba_batch.merge_bundles([a, b], tmp_path / "Doc3.zip")


def test_directory_entries_are_not_counted_as_files(tmp_path):
    path = tmp_path / "batch-01.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("folder/", b"")
        zf.writestr("folder/A.pdf", b"a")

    _, count = ariba_batch.merge_bundles([path], tmp_path / "Doc4.zip")

    assert count == 1


def test_a_shortfall_against_the_expected_count_is_reported(tmp_path):
    a = _make_zip(tmp_path / "batch-01.zip", {"A.pdf": b"a"})
    lines = []

    _, count = ariba_batch.merge_bundles(
        [a], tmp_path / "Doc5.zip", expected_files=54, log=lines.append)

    assert count == 1
    assert any("54" in line and "1" in line for line in lines)


def test_write_omitted_records_the_gap_durably(tmp_path):
    bundle = tmp_path / "Doc5713434353.zip"
    bundle.write_bytes(b"z")

    path = ariba_batch.write_omitted(bundle, ["big.pdf"], expected_files=54, actual_files=53)

    assert path == tmp_path / "Doc5713434353.omitted.json"
    body = json.loads(path.read_text())
    assert body["omitted"] == ["big.pdf"]
    assert body["expected_files"] == 54
    assert body["actual_files"] == 53


def test_write_omitted_writes_nothing_when_the_capture_is_complete(tmp_path):
    bundle = tmp_path / "Doc1.zip"
    bundle.write_bytes(b"z")

    assert ariba_batch.write_omitted(bundle, [], expected_files=3, actual_files=3) is None
    assert not (tmp_path / "Doc1.omitted.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_ariba_batch.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'merge_bundles'`

- [ ] **Step 3: Write minimal implementation**

Append to `scrapers/toronto_bids/sources/ariba_batch.py` (add `import shutil` and `import zipfile` to the module's imports):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scrapers && uv run pytest tests/test_ariba_batch.py -q`
Expected: PASS (20 passed)

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_batch.py scrapers/tests/test_ariba_batch.py
git commit -m "feat(ariba): merge batch zips into one canonical bundle (#174)"
```

---

### Task 4: The capture driver — plan, download, resume

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_batch.py`
- Test: `scrapers/tests/test_ariba_batch.py`

**Interfaces:**
- Consumes: `accumulate_batches`, `merge_bundles`, `write_omitted`, manifest helpers.
- Produces: `capture_in_batches(picker, document_number, dest_dir, fingerprint, *, posting_open, threshold_mb=BATCH_THRESHOLD_MB, log=...) -> Path | None` — returns the canonical bundle path once every batch is in hand, else `None` (partials kept for the next run); `finalise_partial(document_number, dest_dir, *, posting_open, log=...) -> Path | None` — merge whatever batches exist, for the posting-closed case.

**Design note:** the spec describes "plan then replay". This implements the equivalent *incremental* form — each completed batch is appended to the manifest as it lands, and a resume skips those keys and continues measuring the rest. Functionally the same, and strictly more correct: run 1 never measured the rows it did not reach, so there is no plan to replay for them.

**Amended by the root fix (#174, follow-up):** a batch's identity is now the `batch-NN.json` sidecar written *before* its zip, not its position in a list, and `manifest.json` no longer carries a `batches` array (`write_manifest(pdir, fingerprint, omitted)`). The live and salvage policies are split — the live path re-downloads a missing/unopenable batch and refuses to write `Doc<n>.zip` while any planned row is uncaptured; only the salvage path skips what will not open. Both functions take a **required keyword-only `posting_open: bool`** and refuse the wrong state: `capture_in_batches(..., posting_open=True)`, `finalise_partial(..., posting_open=False)`. The code blocks below in Tasks 4 and 6 predate that and are kept as a record of the original task; **follow the shipped module, and the amended Task 6 snippets.**

- [ ] **Step 1: Write the failing test**

Append to `scrapers/tests/test_ariba_batch.py`:

```python
def test_a_clean_run_downloads_every_batch_and_merges(tmp_path):
    picker = FakePicker({"1.1": 300, "1.2": 300})
    fp = ariba_batch.make_fingerprint(picker.row_keys(), 2, 600.0)

    bundle = ariba_batch.capture_in_batches(
        picker, "5713434353", tmp_path, fp, threshold_mb=450)

    assert bundle == tmp_path / "Doc5713434353.zip"
    assert bundle.exists()
    assert picker.downloads == [["1.1"], ["1.2"]]
    assert not ariba_batch.partial_dir(tmp_path, "5713434353").exists()


def test_an_interrupted_run_keeps_its_batches(tmp_path):
    """Respond dies at close, so downloaded bytes must survive a failure."""
    class Flaky(FakePicker):
        def download_to(self, path):
            if len(self.downloads) == 1:
                raise RuntimeError("network died")
            return super().download_to(path)

    picker = Flaky({"1.1": 300, "1.2": 300})
    fp = ariba_batch.make_fingerprint(picker.row_keys(), 2, 600.0)

    result = ariba_batch.capture_in_batches(
        picker, "5713434353", tmp_path, fp, threshold_mb=450)

    assert result is None
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    assert (pdir / "batch-01.zip").exists()
    assert ariba_batch.read_manifest(pdir)["batches"] == [["1.1"]]


def test_a_resumed_run_skips_batches_already_on_disk(tmp_path):
    picker = FakePicker({"1.1": 300, "1.2": 300})
    fp = ariba_batch.make_fingerprint(picker.row_keys(), 2, 600.0)
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    _make_zip(pdir / "batch-01.zip", {"A.pdf": b"a"})
    ariba_batch.write_manifest(pdir, fp, batches=[["1.1"]], omitted=[])

    bundle = ariba_batch.capture_in_batches(
        picker, "5713434353", tmp_path, fp, threshold_mb=450)

    assert bundle.exists()
    assert picker.downloads == [["1.2"]]          # 1.1 was NOT re-downloaded


def test_a_stale_fingerprint_discards_the_partials_and_replans(tmp_path):
    picker = FakePicker({"1.1": 300, "1.2": 300})
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    _make_zip(pdir / "batch-01.zip", {"A.pdf": b"a"})
    ariba_batch.write_manifest(
        pdir, ariba_batch.make_fingerprint(["1.1"], 1, 300.0), batches=[["1.1"]], omitted=[])

    fresh = ariba_batch.make_fingerprint(picker.row_keys(), 2, 600.0)
    bundle = ariba_batch.capture_in_batches(
        picker, "5713434353", tmp_path, fresh, threshold_mb=450)

    assert bundle.exists()
    assert picker.downloads == [["1.1"], ["1.2"]]  # everything re-downloaded


def test_an_omitted_row_still_lets_the_bundle_complete(tmp_path):
    picker = FakePicker({"1.1": 100, "big": 600})
    fp = ariba_batch.make_fingerprint(picker.row_keys(), 2, 700.0)

    bundle = ariba_batch.capture_in_batches(
        picker, "5713434353", tmp_path, fp, threshold_mb=450)

    assert bundle.exists()
    assert json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())["omitted"] == ["big"]


def test_finalise_partial_merges_what_we_have_when_the_posting_closes(tmp_path):
    """3 of 5 batches is permanently better than nothing once Respond is disabled."""
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    _make_zip(pdir / "batch-01.zip", {"A.pdf": b"a"})
    _make_zip(pdir / "batch-02.zip", {"B.pdf": b"b"})
    ariba_batch.write_manifest(
        pdir, ariba_batch.make_fingerprint(["1.1", "1.2"], 54, 792.41),
        batches=[["1.1"], ["1.2"]], omitted=[])

    bundle = ariba_batch.finalise_partial("5713434353", tmp_path)

    assert bundle == tmp_path / "Doc5713434353.zip"
    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["A.pdf", "B.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["expected_files"] == 54 and body["actual_files"] == 2


def test_finalise_partial_is_none_when_there_is_nothing_to_finalise(tmp_path):
    assert ariba_batch.finalise_partial("5713434353", tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_ariba_batch.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'capture_in_batches'`

- [ ] **Step 3: Write minimal implementation**

Append to `scrapers/toronto_bids/sources/ariba_batch.py` (add `import shutil` if not already present):

```python
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

    if manifest and not manifest_is_current(manifest, fingerprint):
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
            log(f"  Doc{document_number}: batch {len(done_batches) + 1} failed ({exc}) — "
                f"partials kept, will resume")
            return None
        finally:
            for key in batch:
                picker.set_selected(key, False)
        done_batches.append(batch)
        write_manifest(pdir, fingerprint, done_batches, omitted)
        log(f"    batch {len(done_batches)}: {len(batch)} row(s) -> {path.name}")

    return _finalise(pdir, document_number, dest_dir, done_batches, omitted,
                     fingerprint.get("file_count"), log)


def _finalise(pdir, document_number, dest_dir, batches, omitted, expected_files, log):
    parts = [_batch_path(pdir, i + 1) for i in range(len(batches))]
    parts = [p for p in parts if p.exists()]
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
    if not manifest:
        return None
    return _finalise(pdir, document_number, dest_dir, manifest["batches"],
                     manifest.get("omitted", []),
                     manifest.get("fingerprint", {}).get("file_count"), log)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scrapers && uv run pytest tests/test_ariba_batch.py -q`
Expected: PASS (27 passed)

- [ ] **Step 5: Run the full suite**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS — 641 existing + 27 new = 668 passed

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_batch.py scrapers/tests/test_ariba_batch.py
git commit -m "feat(ariba): resumable batched capture driver (#174)"
```

---

### Task 5: The Playwright adapter

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_attachments.py`

**Interfaces:**
- Consumes: nothing from `ariba_batch` (it satisfies the protocol structurally).
- Produces: `class AribaPicker` with `row_keys()`, `set_selected(key, bool)`, `total_mb()`, `file_count()`, `download_to(path)`.

There is no unit test for this task — it is the browser adapter, and the codebase does not unit-test browser code (`discover_report_urls`, `capture_event`, `login` are all untested by design). Its verification is Task 7's live run. Keep it thin: every decision that can be made without a browser already lives in `ariba_batch.py`.

- [ ] **Step 1: Add the adapter**

Append to `scrapers/toronto_bids/sources/ariba_attachments.py`, after `_selected_total_mb`:

```python
_ROW_KEY = re.compile(r"^\s*(\d+(?:\.\d+)*)\s")


class AribaPicker:
    """Playwright adapter satisfying ariba_batch's Picker protocol (#174).

    Two hazards this class exists to contain, both measured live:

    * **The row list is virtualised.** A fixed 51 checkboxes render as a sliding window over
      ~85 logical rows -- at the top of the list index 9 is "4.1 Form A", after scrolling it is
      "5 Part 5 - Pricing Form". So rows are addressed by OUTLINE NUMBER, and enumeration has
      to scroll the whole list. Reading only what is rendered would silently plan over ~51 of
      ~85 rows, which looks like a clean capture that is quietly missing files.
    * **Handles detach.** The picker re-renders after every selection, so a locator is
      re-resolved at the moment of use and never held across a click.
    """

    def __init__(self, page, log=lambda _m: None):
        self.page = page
        self.log = log

    # --- reads ---------------------------------------------------------------------------
    def _rendered(self) -> dict:
        """{outline key: rendered index} for the rows currently in the DOM."""
        rows = self.page.evaluate(
            """() => Array.from(document.querySelectorAll('input.w-chk-native'))
                 .map((e, i) => { const tr = e.closest('tr');
                                  return [i, ((tr ? tr.innerText : '') || '').trim()]; })""")
        out = {}
        for index, text in rows:
            m = _ROW_KEY.match(text.replace("\xa0", " "))
            if m:
                out.setdefault(m.group(1), index)
        return out

    def row_keys(self) -> list:
        """Every row's outline number, in order, scrolling to defeat virtualisation."""
        self.page.keyboard.press("Home")
        self.page.wait_for_timeout(800)
        seen, order = set(), []
        for _ in range(40):
            for key in self._rendered():
                if key not in seen:
                    seen.add(key)
                    order.append(key)
            before = len(seen)
            self.page.mouse.wheel(0, 2000)
            self.page.wait_for_timeout(350)
            for key in self._rendered():
                if key not in seen:
                    seen.add(key)
                    order.append(key)
            if len(seen) == before:
                break
        order.sort(key=lambda k: [int(p) for p in k.split(".")])
        self.log(f"    picker rows: {len(order)}")
        return order

    def total_mb(self):
        return _selected_total_mb(self.page)

    def file_count(self) -> int:
        n = self.page.evaluate(
            """() => { const m = document.body.innerText.match(/Total\\s*Number:\\s*([\\d,]+)/);
                       return m ? m[1].replace(/,/g, '') : null; }""")
        return int(n) if n else 0

    # --- writes --------------------------------------------------------------------------
    def _locate(self, key: str):
        """Re-resolve the row's checkbox, scrolling it into the window first."""
        for _ in range(40):
            rendered = self._rendered()
            if key in rendered:
                loc = self.page.locator("div.w-chk-container").nth(rendered[key])
                loc.scroll_into_view_if_needed(timeout=10000)
                self.page.wait_for_timeout(200)
                return loc
            self.page.mouse.wheel(0, 2000)
            self.page.wait_for_timeout(300)
        raise RuntimeError(f"row {key} never appeared in the picker window")

    def set_selected(self, key: str, value: bool) -> None:
        before = self._checked()
        loc = self._locate(key)
        box = loc.bounding_box()
        if not box:
            raise RuntimeError(f"row {key} has no bounding box")
        self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        self._settle(before)

    def _checked(self) -> int:
        return self.page.evaluate(
            "() => Array.from(document.querySelectorAll('input.w-chk-native'))"
            ".filter(e => e.checked).length")

    def _settle(self, before: int, timeout_ms: int = 45000) -> None:
        """Poll until the recompute lands. NEVER sleep a guess -- that was #174's root cause."""
        waited, prev, stable = 0, None, 0
        while waited < timeout_ms:
            self.page.wait_for_timeout(500)
            waited += 500
            cur = (self._checked(), _selected_total_mb(self.page))
            stable = stable + 1 if cur == prev else 0
            prev = cur
            if stable >= 2 and cur[0] != before:
                return
        self.log("    warning: selection never settled within "
                 f"{timeout_ms / 1000:.0f}s — continuing on the last reading")

    def download_to(self, path):
        with self.page.expect_download(timeout=300000) as dl:
            self.page.get_by_role("button", name="Download Attachments").last.click()
        dl.value.save_as(str(path))
        return path
```

- [ ] **Step 2: Verify the module still imports and the suite is green**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS — 668 passed (no new tests; this proves nothing was broken)

- [ ] **Step 3: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_attachments.py
git commit -m "feat(ariba): AribaPicker adapter over the virtualised picker (#174)"
```

---

### Task 6: Wire it into capture_event

**Files:**
- Modify: `scrapers/toronto_bids/sources/ariba_attachments.py:309-376` (`capture_event`)

**Interfaces:**
- Consumes: `AribaPicker` (Task 5), `ariba_batch.capture_in_batches` / `finalise_partial` (Task 4).
- Produces: no new public names. `capture_event` keeps its signature and its `Path | None` return.

- [ ] **Step 1: Add the closed-with-partials rule**

In `capture_event`, replace the existing Respond-disabled block:

```python
    respond = page.get_by_role("button", name="Respond", exact=True)
    if respond.is_disabled():
        log(f"  Doc{document_number}: Respond disabled (closed) — skipped")
        return None
```

with:

```python
    respond = page.get_by_role("button", name="Respond", exact=True)
    if respond.is_disabled():
        # Respond dies the moment a posting closes, so any batches we already hold can never
        # be completed. Merging 3 of 5 is permanently better than nothing -- this is the whole
        # reason partial captures are retained (#174).
        # posting_open=False is the assertion, not a formality: finalise_partial refuses to
        # canonicalise a capture that could still complete, and this branch is the one place
        # that knows the posting is closed.
        salvaged = ariba_batch.finalise_partial(
            document_number, dest_dir, posting_open=False, log=log)
        if salvaged is not None:
            log(f"  Doc{document_number}: closed mid-capture — salvaged what we had")
            return salvaged
        log(f"  Doc{document_number}: Respond disabled (closed) — skipped")
        return None
```

- [ ] **Step 2: Replace the over-ceiling skip with a batched capture**

Replace:

```python
    total_mb = _selected_total_mb(page)
    if total_mb is not None and total_mb > MAX_BUNDLE_MB:
        # ponytail: single-zip only; per-Part download is the upgrade path when one is needed.
        log(f"  Doc{document_number}: bundle {total_mb:.0f} MB > {MAX_BUNDLE_MB} MB single-zip "
            f"limit — skipped, needs per-Part capture")
        return None
```

with:

```python
    total_mb = _selected_total_mb(page)
    if total_mb is not None and total_mb > MAX_BUNDLE_MB:
        # Ariba disables its own Download button over 500 MB and says "select specific items
        # and perform multiple downloads" -- so do exactly that (#174).
        log(f"  Doc{document_number}: bundle {total_mb:.0f} MB > {MAX_BUNDLE_MB} MB — "
            f"capturing in batches")
        picker = AribaPicker(page, log=log)
        fingerprint = ariba_batch.make_fingerprint(
            picker.row_keys(), picker.file_count(), total_mb)
        for key in fingerprint["row_keys"]:      # clear the select-all before batching
            picker.set_selected(key, False)
        # Reached only past the `respond.is_disabled()` check above, i.e. the posting is open --
        # which is what lets capture_in_batches discard partials it cannot identify.
        return ariba_batch.capture_in_batches(
            picker, document_number, dest_dir, fingerprint, posting_open=True, log=log)
```

Add the import at the top of the module, beside the other `toronto_bids` imports:

```python
from toronto_bids.sources import ariba_batch
```

And move `dest_dir` resolution above the Respond check so `finalise_partial` can use it — in `capture_event`, immediately after the `rfx_id, document_number = ...` line, insert:

```python
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
```

then delete the later duplicate pair of lines that did the same thing just before `target = ...`.

- [ ] **Step 3: Run the full suite**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS — 668 passed

- [ ] **Step 4: Commit**

```bash
git add scrapers/toronto_bids/sources/ariba_attachments.py
git commit -m "feat(ariba): capture_event batches oversized events and salvages on close (#174)"
```

---

### Task 7: Live acceptance run

**Files:** none — this is verification.

This is the acceptance test the spec names: `Doc5713434353`, 792.41 MB, ~54 files, expected to land in roughly two batches. **The posting closes 2026-08-07** — run this with days to spare, because a misbehaving batch download needs runs left to diagnose.

- [ ] **Step 1: Run the capture against the live event**

```bash
cd scrapers
set -a && . ~/.config/toronto-bids/tb.env && set +a
TB_DATA_DIR="$HOME/tb-data" uv run tb enrich-ariba-attachments --capture --virtual-display
```

Expected, roughly:

```
  open events: 43  already archived: 42  to capture: 1
    picker rows: 85
  Doc5713434353: bundle 792 MB > 500 MB — capturing in batches
    batch 1: N row(s) -> batch-01.zip
    batch 2: N row(s) -> batch-02.zip
  Doc5713434353: merged 2 batch(es) -> Doc5713434353.zip (54 files)
  bundles captured: 1
```

If Ariba answers `session/SSO did not settle`, that is login throttling, not a regression — wait several minutes and re-run. The capture is resumable, so a re-run continues rather than restarting.

- [ ] **Step 2: Verify the bundle indexes like any other**

```bash
cd scrapers && TB_DATA_DIR="$HOME/tb-data" uv run tb enrich-ariba-attachments --reindex
TB_DATA_DIR="$HOME/tb-data" uv run tb status
```

Expected: `ariba_attachment` row count rises by the number of leaves in the new bundle, and the bundle appears under 50 solicitations rather than 49.

- [ ] **Step 3: Confirm nothing was silently dropped**

```bash
ls -la ~/tb-data/ariba/attachments/Doc5713434353.zip
ls ~/tb-data/ariba/attachments/Doc5713434353.omitted.json 2>/dev/null && \
  cat ~/tb-data/ariba/attachments/Doc5713434353.omitted.json
ls -d ~/tb-data/ariba/attachments/.partial/Doc5713434353 2>/dev/null || echo "partials cleaned up"
```

Expected: the bundle exists; `.partial/` is gone; `Doc5713434353.omitted.json` is **absent** (a complete capture writes none). If it is present, read it — that is the design working, telling you exactly what is missing and why.

- [ ] **Step 4: Record the result on the issue and commit any doc updates**

Add a short CLAUDE.md note under the Ariba attachments section recording that events over 500 MB are captured in batches and merged, with the measured batch count for this event. Then:

```bash
git add CLAUDE.md
git commit -m "docs: record batched capture for oversized Ariba events (#174)"
```

---

## Self-Review

**Spec coverage:** threshold 450 (Global Constraints, Task 2) · keys-not-indices (Tasks 2, 5) · scrolled enumeration (Task 5) · poll-don't-sleep (Task 5 `_settle`) · plan/manifest + fingerprint (Task 1) · resume (Task 4) · stale-fingerprint re-plan (Task 4) · single-item-over-ceiling omitted (Tasks 2, 4) · unmeasurable total aborts (Task 2) · merge preserving CRCs (Task 3) · collision refuses (Task 3) · count check vs `Total Number` not leaf count (Task 3) · durable `.omitted.json` (Task 3) · closed-with-partials salvage (Tasks 4, 6) · `capture_attachments` unchanged (no task touches it — verified by Task 4's "canonical zip only on completion") · no schema change (no task touches `models.py` or `db.py`) · live acceptance (Task 7).

**Out-of-scope items confirmed absent:** no per-Part measurement, no new column, no per-file download.

**Type consistency:** `accumulate_batches` returns `(batches, omitted)` in Tasks 2 and 4 · `merge_bundles` returns `(target, count)` in Tasks 3 and 4 · `make_fingerprint(row_keys, file_count, total_mb)` is called with those names in Tasks 1, 4 and 6 · `partial_dir(dest_dir, document_number)` argument order is consistent throughout · `AribaPicker` implements exactly the five protocol methods `FakePicker` does.
