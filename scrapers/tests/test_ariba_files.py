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
