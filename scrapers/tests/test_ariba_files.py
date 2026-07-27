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
