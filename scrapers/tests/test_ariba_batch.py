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
