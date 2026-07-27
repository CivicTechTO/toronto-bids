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
