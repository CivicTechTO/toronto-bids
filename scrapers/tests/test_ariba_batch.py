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
