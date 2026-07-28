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


_UNSET = object()


class FakeFileSource:
    """FileSource stand-in.

    `fail_on` / `kill_on` name files (by name OR key) whose download raises -- an ordinary
    Exception and a BaseException respectively, the latter standing in for a Ctrl-C mid
    transfer. `keys` gives the listing's identities explicitly, which is what distinguishes two
    DIFFERENT documents that share a name. `expected=None` is passed THROUGH, never coerced:
    the picker's count really can be unreadable and that path has to be reachable from a test.
    `append=True` models an adapter that appends to (rather than truncates) the path it is
    handed, which is what makes a leftover `.part` dangerous.

    Without `keys`, identity defaults to `name#occurrence` (`_default_keys` below) -- stable
    and INDEPENDENT of a file's position among files with different names, satisfying the `key`
    contract in the module docstring for the common case. It still cannot tell apart two files
    that share a name (there is no name-only way to do that): that case needs `keys=` passed
    explicitly. A bare positional default (the index into the list) would look the same shape
    but carry zero real identity, silently defeating exactly the fingerprint this module tests.
    """

    def __init__(self, names, expected=_UNSET, fail_on=(), contents=None, keys=None,
                 kill_on=(), append=False):
        self.names = list(names)
        self.keys = list(keys) if keys is not None else self._default_keys(self.names)
        self.expected = len(self.names) if expected is _UNSET else expected
        self.fail_on = set(fail_on)
        self.kill_on = set(kill_on)
        self.contents = contents or {}
        self.append = append
        self.downloaded = []

    @staticmethod
    def _default_keys(names):
        """`name#occurrence` -- stable across calls and NOT derived from list position.

        Two different names get different keys no matter where they sit in the list; two
        occurrences of the SAME name get distinct-but-deterministic keys rather than colliding
        on a bare index. Still cannot represent two DIFFERENT documents that share a name --
        nothing about a name alone can -- so that case must pass `keys=` explicitly.
        """
        counts: dict = {}
        out = []
        for name in names:
            counts[name] = counts.get(name, 0) + 1
            out.append(f"{name}#{counts[name]}")
        return out

    def list_files(self):
        return [{"key": k, "name": n, "row": n} for k, n in zip(self.keys, self.names)]

    def expected_count(self):
        return self.expected

    def _payload(self, file) -> bytes:
        if file["key"] in self.contents:
            return self.contents[file["key"]]
        return self.contents.get(file["name"], file["name"].encode())

    def _die_midway(self, dest, exc):
        """Fail the way a real transfer fails: some bytes already on disk, then the error."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "ab") as fh:
            fh.write(b"PARTIALLY-TRANSFERRED")
        raise exc

    def download(self, file, dest):
        if self.kill_on & {file["name"], file["key"]}:
            self._die_midway(dest, KeyboardInterrupt(f"killed on {file['name']}"))
        if self.fail_on & {file["name"], file["key"]}:
            self._die_midway(dest, RuntimeError(f"boom: {file['name']}"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "ab" if self.append else "wb") as fh:
            fh.write(self._payload(file))
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
    """EVERY file fails, so the partial directory survives to be inspected.

    With a file captured the run rmtree's the whole partial directory, which would hide a
    leaked `.part` behind a directory that no longer exists.
    """
    source = FakeFileSource(["a.pdf", "b.pdf"], fail_on=["a.pdf", "b.pdf"])

    assert ariba_files.capture_files(source, "5713434353", tmp_path) is None

    assert ariba_files.partial_dir(tmp_path, "5713434353").exists()
    assert list(tmp_path.rglob("*.part")) == []


def test_when_every_file_fails_nothing_is_archived_and_the_partials_are_kept(tmp_path):
    """No bundle means capture_attachments comes back next run; an empty one would not."""
    source = FakeFileSource(["a.pdf", "b.pdf"], fail_on=["a.pdf", "b.pdf"])

    assert ariba_files.capture_files(source, "5713434353", tmp_path) is None

    assert not (tmp_path / "Doc5713434353.zip").exists()
    assert (ariba_files.partial_dir(tmp_path, "5713434353") / "files").is_dir()


def test_resume_skips_files_already_complete_on_disk(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"already here")
    ariba_files.write_manifest(
        pdir, ariba_files.make_fingerprint(source.list_files(), 2))

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    assert source.downloaded == ["b.pdf"]           # a.pdf was NOT re-fetched
    with zipfile.ZipFile(bundle) as zf:
        assert zf.read("a.pdf") == b"already here"


def test_a_resume_over_a_part_left_by_a_kill_refetches_it_cleanly(tmp_path):
    """A `.part` is an interrupted transfer: never complete, and never appended to.

    The adapter here APPENDS to the path it is handed, so a surviving `.part` would make the
    re-download `HALF` + `WHOLE` and that corruption would become canonical.
    """
    source = FakeFileSource(["a.pdf", "b.pdf"], contents={"b.pdf": b"WHOLE"}, append=True)
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"a.pdf")            # complete
    (pdir / "files" / "b.pdf.part").write_bytes(b"HALF")        # killed mid-transfer
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files(), 2))

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b.pdf"]      # the .part is not a member
        assert zf.read("b.pdf") == b"WHOLE"                     # not b"HALFWHOLE"
    assert not (tmp_path / "Doc5713434353.omitted.json").exists()


def test_a_changed_event_discards_the_partials(tmp_path):
    """An addendum landed between runs -- partials describe a different version.

    The stale partial deliberately carries a name the NEW listing also has: a stale file whose
    name the new listing lacks could never have entered the bundle anyway, so it proves nothing.
    """
    source = FakeFileSource(["a.pdf", "b.pdf"], contents={"a.pdf": b"FRESH"})
    stale = FakeFileSource(["a.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"STALE")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(stale.list_files(), 1))

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b.pdf"]
        assert zf.read("a.pdf") == b"FRESH"          # the stale bytes were discarded


def test_a_count_mismatch_records_rather_than_refusing(tmp_path):
    """Respond dies at close, so bytes beat strictness."""
    source = FakeFileSource(["a.pdf"], expected=54)

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    assert bundle.exists()
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["expected_files"] == 54 and body["actual_files"] == 1


def test_a_short_traversal_is_logged_loudly_and_never_raises(tmp_path):
    """The #174 correction: "a short traversal must be loud" does not mean "must raise".

    A review round asked for the shortfall to be loud, and a since-reverted change answered
    that with a `raise` in the traversal itself -- which would abort every capture the moment
    the picker's Total Number and the tree's file count disagreed, even though it is NOT
    established that the two count the same thing (a nested archive's members vs. one tree
    file, say). Respond dies the instant a posting closes, so an unverified check must never be
    able to block the only path that gets these bytes. This pins both halves together: the
    capture still completes (`test_a_count_mismatch_records_rather_than_refusing` covers the
    durable `.omitted.json` side of that already) AND the gap is surfaced through `log`, not
    left silent until someone thinks to open the JSON file mid-run.
    """
    messages = []
    source = FakeFileSource(["a.pdf"], expected=54)

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path, log=messages.append)

    assert bundle.exists()                       # recorded, never refused
    assert any("54" in m and "SHORT" in m for m in messages), (
        f"no loud shortfall log naming both counts among: {messages}")


def test_an_unknown_expected_count_is_recorded_as_unknown_not_zero(tmp_path):
    """`expected=None` reaches the record as JSON null -- unconditionally asserted."""
    source = FakeFileSource(["a.pdf"], expected=None)

    ariba_files.capture_files(source, "5713434353", tmp_path)

    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["expected_files"] is None
    assert body["omitted"] == [] and body["actual_files"] == 1


def test_duplicate_names_across_the_tree_are_both_kept(tmp_path):
    source = FakeFileSource(["dup.pdf", "dup.pdf"],
                            contents={"dup.pdf": b"first"})

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["dup.pdf", "dup_2.pdf"]


# --- C1: a resumed file's identity is the listing's ORDER, not a name multiset -------------

def test_the_fingerprint_sees_a_reordered_listing():
    """Same names, same count, different documents in each position -- a different event."""
    first = [{"key": "X", "name": "report.pdf"}, {"key": "Y", "name": "report.pdf"}]
    second = [{"key": "Y", "name": "report.pdf"}, {"key": "X", "name": "report.pdf"}]

    assert ariba_files.make_fingerprint(first, 2) != ariba_files.make_fingerprint(second, 2)


def test_the_fingerprint_sees_a_substituted_identity_behind_an_unchanged_name():
    kept = [{"key": "X", "name": "report.pdf"}]
    swapped = [{"key": "Z", "name": "report.pdf"}]

    assert ariba_files.make_fingerprint(kept, 1) != ariba_files.make_fingerprint(swapped, 1)


def test_the_fingerprint_survives_a_json_round_trip(tmp_path):
    """It is compared against a manifest READ BACK, so tuples-vs-lists must not diverge."""
    source = FakeFileSource(["a.pdf", "b.pdf"])
    fp = ariba_files.make_fingerprint(source.list_files(), 2)
    ariba_files.write_manifest(tmp_path, fp)

    assert ariba_files.read_manifest(tmp_path)["fingerprint"] == fp


def test_a_reordered_listing_with_duplicate_names_does_not_reuse_the_partials(tmp_path):
    """The archive-corrupting case: two documents named `report.pdf`, captured across a kill.

    Run 1 lists X then Y, saves X as `report.pdf`, and dies. Run 2 lists them the other way
    round. A fingerprint over a name MULTISET cannot see that, so the partials are kept and
    `report.pdf` (which now means Y) is skipped as already-complete -- the bundle ends up
    holding X twice, Y never, and the counts match so no gap is recorded. Silently wrong,
    permanently.
    """
    doc = "5713434353"
    contents = {"X": b"DOC-X", "Y": b"DOC-Y"}
    run1 = FakeFileSource(["report.pdf", "report.pdf"], keys=["X", "Y"],
                          contents=contents, kill_on=["Y"])
    with pytest.raises(KeyboardInterrupt):
        ariba_files.capture_files(run1, doc, tmp_path)
    assert (ariba_files.partial_dir(tmp_path, doc) / "files" / "report.pdf").exists()

    run2 = FakeFileSource(["report.pdf", "report.pdf"], keys=["Y", "X"], contents=contents)
    bundle = ariba_files.capture_files(run2, doc, tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["report.pdf", "report_2.pdf"]
        assert zf.read("report.pdf") == b"DOC-Y"        # position 1 is Y this run
        assert zf.read("report_2.pdf") == b"DOC-X"
        assert {zf.read(n) for n in zf.namelist()} == {b"DOC-X", b"DOC-Y"}   # neither lost


# --- I2 / I7: the gap record precedes the bundle, and this module owns its own partials ----

def test_the_gap_record_is_written_before_the_bundle(tmp_path, monkeypatch):
    """A bundle whose gap record failed to land is an event archived with an undescribed gap.

    Writing 787 MB is exactly where ENOSPC happens, and the record whose ABSENCE means
    "nothing is missing" would be the thing that went missing.
    """
    source = FakeFileSource(["a.pdf", "bad.pdf"], fail_on=["bad.pdf"])

    def boom(files, target):
        raise OSError("ENOSPC: no space left on device")

    monkeypatch.setattr(ariba_files, "build_bundle", boom)

    with pytest.raises(OSError):
        ariba_files.capture_files(source, "5713434353", tmp_path)

    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["bad.pdf"]
    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_the_partial_directory_is_this_modules_own_namespace(tmp_path):
    """Not the batched capture's `.partial/`: same path, same manifest name, other schema."""
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")

    assert pdir == tmp_path / ".partial-files" / "Doc5713434353"
    assert pdir != tmp_path / ".partial" / "Doc5713434353"


# --- M8 / M11: stale records cleared, a 0-byte "success" recorded as the gap it is ---------

def test_a_complete_capture_clears_a_stale_gap_record(tmp_path):
    stale = tmp_path / "Doc5713434353.omitted.json"
    stale.write_text(json.dumps({"omitted": ["gone.pdf"], "expected_files": 2,
                                 "actual_files": 1}))
    source = FakeFileSource(["a.pdf"])

    ariba_files.capture_files(source, "5713434353", tmp_path)

    assert not stale.exists()       # nothing is missing now, and the record must not say so


def test_a_zero_byte_download_is_a_failure_not_a_silent_hole(tmp_path):
    source = FakeFileSource(["a.pdf", "empty.pdf"], contents={"empty.pdf": b""})

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["a.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["empty.pdf"]
    assert list(tmp_path.rglob("*.part")) == []


def test_a_kill_mid_transfer_is_not_swallowed_as_one_dead_file(tmp_path):
    """Ctrl-C must abort the run rather than be recorded as an omission -- and leave no .part."""
    source = FakeFileSource(["a.pdf", "b.pdf"], kill_on=["b.pdf"])

    with pytest.raises(KeyboardInterrupt):
        ariba_files.capture_files(source, "5713434353", tmp_path)

    assert list(tmp_path.rglob("*.part")) == []
    assert not (tmp_path / "Doc5713434353.zip").exists()


# --- I7b: salvage, for a posting that closed mid-capture -----------------------------------

def test_finalise_partial_refuses_an_open_posting(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"], fail_on=["a.pdf", "b.pdf"])
    ariba_files.capture_files(source, "5713434353", tmp_path)

    with pytest.raises(ValueError, match="CLOSED"):
        ariba_files.finalise_partial("5713434353", tmp_path, posting_open=True)

    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_finalise_partial_is_none_when_there_is_nothing_to_finalise(tmp_path):
    assert ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False) is None
    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_finalise_partial_bundles_what_is_on_disk_and_records_the_rest(tmp_path):
    """Respond dies at close, so the files already downloaded can never be completed."""
    source = FakeFileSource(["a.pdf", "b.pdf", "c.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files(), 3))

    bundle = ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    assert bundle == tmp_path / "Doc5713434353.zip"
    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["a.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["b.pdf", "c.pdf"]
    assert body["expected_files"] == 3 and body["actual_files"] == 1
    assert not pdir.exists()


def test_finalise_partial_keeps_a_part_file_rather_than_deleting_it(tmp_path):
    """A truncated transfer is bytes that can never be re-fetched: recorded missing, not erased."""
    source = FakeFileSource(["a.pdf", "b.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    (pdir / "files" / "b.pdf.part").write_bytes(b"HALF")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files(), 2))

    bundle = ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["a.pdf"]           # the .part is never a member
    assert json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())["omitted"] == [
        "b.pdf"]
    assert (pdir / "files" / "b.pdf.part").exists()


# --- F1: an unreadable (missing OR corrupt) manifest must not adopt partials positionally --

def test_a_corrupt_manifest_discards_partials_instead_of_trusting_them_positionally(tmp_path):
    """`files/report.pdf` already holds DOC-X on disk; the manifest is truncated/corrupt.

    Pre-fix, `read_manifest` returning None fell straight through `if manifest and ...`
    (None is falsy) without discarding anything, so the existing file was trusted
    POSITIONALLY. This run's listing comes back reordered ([Y, X] instead of [X, Y]), so
    `report.pdf` is adopted as already-complete for what is now Y's slot, Y's real bytes are
    never fetched, and `report_2.pdf` downloads X again -- the bundle holds DOC-X twice, DOC-Y
    never, and the counts match so no gap is recorded. That is the Critical verbatim, reached
    through the "corrupt" half of `manifest is None` rather than the "missing" half.
    """
    doc = "5713434353"
    pdir = ariba_files.partial_dir(tmp_path, doc)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "report.pdf").write_bytes(b"DOC-X")
    (pdir / ariba_files.MANIFEST_NAME).write_text("{not valid json")   # truncated / corrupt

    source = FakeFileSource(["report.pdf", "report.pdf"], keys=["Y", "X"],
                            contents={"X": b"DOC-X", "Y": b"DOC-Y"})

    bundle = ariba_files.capture_files(source, doc, tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert {zf.read(n) for n in zf.namelist()} == {b"DOC-X", b"DOC-Y"}   # neither lost
    assert not (tmp_path / f"Doc{doc}.omitted.json").exists()       # nothing missing, no gap
    assert source.downloaded.count("report.pdf") == 2                # both re-fetched, not adopted


# --- F2: the fingerprint's identity guarantee needs keys that are stable and non-positional -

def test_a_stable_nonsequential_key_reorder_survives_after_an_interrupted_run(tmp_path):
    """Same shape as the archive-corrupting case, with keys that are not sequential integers.

    `key` only defends the fingerprint if it is stable across traversals and independent of
    position -- never derived from a file's index in the listing. This pins that contract with
    arbitrary, non-sequential identifiers rather than "0"/"1", so a regression that silently
    made key derivation positional again (in `FakeFileSource`'s default, or in a real adapter)
    could not hide behind keys that merely happen to look like stable strings.
    """
    doc = "5713434353"
    contents = {"aria-QQ7": b"DOC-Q", "aria-ZZ2": b"DOC-Z"}
    run1 = FakeFileSource(["report.pdf", "report.pdf"], keys=["aria-QQ7", "aria-ZZ2"],
                          contents=contents, kill_on=["aria-ZZ2"])
    with pytest.raises(KeyboardInterrupt):
        ariba_files.capture_files(run1, doc, tmp_path)
    assert (ariba_files.partial_dir(tmp_path, doc) / "files" / "report.pdf").exists()

    run2 = FakeFileSource(["report.pdf", "report.pdf"], keys=["aria-ZZ2", "aria-QQ7"],
                          contents=contents)
    bundle = ariba_files.capture_files(run2, doc, tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert {zf.read(n) for n in zf.namelist()} == {b"DOC-Q", b"DOC-Z"}   # neither lost
    assert not (tmp_path / f"Doc{doc}.omitted.json").exists()


def test_fake_file_source_default_keys_are_not_positional(tmp_path):
    """The fake's default (no `keys=` given) must satisfy the same contract real adapters do.

    A file's default key must depend on its NAME, not the index it happens to sit at: two
    unique names get the same keys regardless of order, and duplicates of the same name get
    distinct-but-deterministic keys (`name#occurrence`) rather than colliding on a bare index.
    """
    forward = {f["name"]: f["key"] for f in FakeFileSource(["a.pdf", "b.pdf"]).list_files()}
    backward = {f["name"]: f["key"] for f in FakeFileSource(["b.pdf", "a.pdf"]).list_files()}
    assert forward == backward           # same identity regardless of position in the listing

    dup_keys = [f["key"] for f in FakeFileSource(["dup.pdf", "dup.pdf"]).list_files()]
    assert len(set(dup_keys)) == 2       # distinct, not both "dup.pdf" nor a bare index
    assert dup_keys != ["0", "1"]        # not the old positional default


# --- Minor 3: the stale-record unlink must run AFTER the bundle, never before ---------------

def test_a_stale_gap_record_survives_if_the_bundle_write_then_fails(tmp_path, monkeypatch):
    """A previous run left a real gap recorded. This run has nothing missing, but the bundle
    write itself then fails, so `Doc<n>.zip` never lands. If the stale record were cleared up
    front (before `build_bundle` even runs) "absence means nothing is missing" becomes false:
    no bundle stands, and now nothing describes why.
    """
    stale = tmp_path / "Doc5713434353.omitted.json"
    stale.write_text(json.dumps({"omitted": ["gone.pdf"], "expected_files": 2,
                                 "actual_files": 1}))
    source = FakeFileSource(["a.pdf"])

    def boom(files, target):
        raise OSError("ENOSPC: no space left on device")

    monkeypatch.setattr(ariba_files, "build_bundle", boom)

    with pytest.raises(OSError):
        ariba_files.capture_files(source, "5713434353", tmp_path)

    assert stale.exists()                                    # not cleared -- no bundle landed
    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_finalise_partial_keeps_a_stale_gap_record_if_the_bundle_write_then_fails(
        tmp_path, monkeypatch):
    """`finalise_partial` has the same shape as `capture_files` here -- see the test above."""
    stale = tmp_path / "Doc5713434353.omitted.json"
    stale.write_text(json.dumps({"omitted": ["gone.pdf"], "expected_files": 2,
                                 "actual_files": 1}))
    source = FakeFileSource(["a.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files(), 1))

    def boom(files, target):
        raise OSError("ENOSPC: no space left on device")

    monkeypatch.setattr(ariba_files, "build_bundle", boom)

    with pytest.raises(OSError):
        ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    assert stale.exists()
    assert not (tmp_path / "Doc5713434353.zip").exists()


# --- Minor 5: a gap must name a duplicate by its disambiguated zip name, not a bare label ---

def test_omitted_names_a_duplicate_by_its_disambiguated_zip_name(tmp_path):
    """Two `report.pdf`s, one fails -- the gap record must say WHICH one, not just the label."""
    source = FakeFileSource(["report.pdf", "report.pdf"], keys=["A", "B"], fail_on=["B"])

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["report.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["report_2.pdf"]           # not the ambiguous "report.pdf"


def test_finalise_partial_names_a_duplicate_omission_by_its_disambiguated_zip_name(tmp_path):
    source = FakeFileSource(["report.pdf", "report.pdf"], keys=["A", "B"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "report.pdf").write_bytes(b"AAA")      # only the first is on disk
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files(), 2))

    bundle = ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["report.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["report_2.pdf"]


def test_finalise_partial_salvages_files_a_lost_manifest_cannot_name(tmp_path):
    """No manifest means no names and no count -- but the bytes are still unrepeatable."""
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    (pdir / ariba_files.MANIFEST_NAME).write_text("not json at all")

    bundle = ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["a.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["expected_files"] is None and body["actual_files"] == 1


# --- the pure decisions the Playwright adapter used to make itself (#174) ------------------
#
# Three decisions lived inside `AribaFileSource`, which is untestable by convention: which
# anchor labels name a document, how a listed file is IDENTIFIED, and which repeats are
# duplicates. Two of them are what the archive's integrity rests on -- a positional identity
# reproduces the corruption `make_fingerprint` exists to catch, and a name-keyed one cannot
# address the right document at all. They live here now, and these are the tests that pin them.


class TestIsDocumentName:
    """The filename predicate. A silent reject is a document missing from the archive."""

    def test_a_plain_filename_is_a_document(self):
        assert ariba_files.is_document_name("Addendum 1.pdf")

    def test_trailing_metadata_does_not_hide_the_extension(self):
        """The `$`-anchored pattern this replaces skipped every row rendering its own size."""
        assert ariba_files.is_document_name("Appendix C2 - Drawings.pdf (1.2 MB)")
        assert ariba_files.is_document_name("site.dwg   4,102 KB")

    def test_the_formats_the_anchored_list_omitted(self):
        for name in ("plan.dwf", "prices.xlsm", "scan.tif", "scan.tiff", "notice.msg",
                     "bundle.7z", "dump.gz", "site.kmz"):
            assert ariba_files.is_document_name(name), name

    def test_the_formats_that_were_always_covered(self):
        for name in ("a.pdf", "a.zip", "a.doc", "a.docx", "a.xls", "a.xlsx", "a.dwg", "a.rtf",
                     "a.txt", "a.jpg", "a.jpeg", "a.png", "a.csv", "a.ppt", "a.pptx"):
            assert ariba_files.is_document_name(name), name

    def test_the_extension_is_matched_case_insensitively(self):
        assert ariba_files.is_document_name("PLAN.DWG")

    def test_a_bare_label_is_not_a_document(self):
        for name in ("References", "Download this attachment", "", "   ", "Section 3.1"):
            assert not ariba_files.is_document_name(name), name

    def test_a_url_is_not_a_document_even_when_it_ends_in_pdf(self):
        assert not ariba_files.is_document_name("https://example.org/a.pdf")
        assert not ariba_files.is_document_name("http://example.org/a.pdf")
        assert not ariba_files.is_document_name("www.example.org/a.pdf")

    def test_an_extension_glued_to_more_letters_is_not_an_extension(self):
        assert not ariba_files.is_document_name("notes.pdfx")


class TestAnchorKey:
    """Identity, and the one property that matters: it is a fact about the document's place in
    the tree, never about when the traversal reached it."""

    def test_a_row_that_leads_with_an_outline_number_keys_on_it(self):
        key = ariba_files.anchor_key(
            {"row": "3.1 Drawings Package plan.dwg 787.7 MB", "name": "plan.dwg", "ordinal": 0})
        assert key.startswith("3.1#")

    def test_a_row_with_no_outline_number_keys_on_its_own_text(self):
        """The References sub-rows -- where the brief says the bulk of the files live -- are
        their own `<tr>` and carry no outline number. The old fallback was an empty prefix plus
        a traversal-order counter, i.e. positional."""
        key = ariba_files.anchor_key(
            {"row": "Addendum 1.pdf 2.1 MB", "name": "Addendum 1.pdf", "ordinal": 0})
        assert "Addendum 1.pdf 2.1 MB" in key
        assert not key.startswith("#")

    def test_two_files_in_one_row_sharing_a_name_are_different_documents(self):
        a = ariba_files.anchor_key({"row": "3.1 Parts", "name": "report.pdf", "ordinal": 0})
        b = ariba_files.anchor_key({"row": "3.1 Parts", "name": "report.pdf", "ordinal": 1})
        assert a != b

    def test_the_same_filename_in_two_different_rows_is_two_documents(self):
        a = ariba_files.anchor_key({"row": "3.1 Part A", "name": "report.pdf", "ordinal": 0})
        b = ariba_files.anchor_key({"row": "4.2 Part B", "name": "report.pdf", "ordinal": 0})
        assert a != b

    def test_the_key_survives_a_reordered_traversal(self):
        entry = {"row": "4.2 Part B", "name": "report.pdf", "ordinal": 1}
        assert ariba_files.anchor_key(entry) == ariba_files.anchor_key(dict(entry))

    def test_whitespace_and_nbsp_in_the_row_do_not_change_the_key(self):
        a = ariba_files.anchor_key({"row": "3.1  Part\xa0A", "name": "a.pdf", "ordinal": 0})
        b = ariba_files.anchor_key({"row": "3.1 Part A", "name": "a.pdf", "ordinal": 0})
        assert a == b


class TestListingFromAnchors:
    """One DOM read -> the listing. Rejects and collapses are RETURNED, never swallowed."""

    def test_it_keeps_both_of_two_same_named_files_in_one_row(self):
        """The `(name, row)` dedupe dropped the second one with no log and no count."""
        result = ariba_files.listing_from_anchors([
            {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 0},
            {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 1},
        ])
        assert [f["name"] for f in result["files"]] == ["report.pdf", "report.pdf"]
        assert result["files"][0]["key"] != result["files"][1]["key"]
        assert result["collided"] == []

    def test_it_reports_what_it_rejected(self):
        result = ariba_files.listing_from_anchors([
            {"row": "3.1", "name": "a.pdf", "ordinal": 0},
            {"row": "3.1", "name": "References", "ordinal": 1},
        ])
        assert [f["name"] for f in result["files"]] == ["a.pdf"]
        assert result["rejected"] == ["References"]

    def test_a_second_reading_of_the_same_anchor_is_one_file(self):
        """Traversal re-reads the whole DOM on every scroll pass."""
        anchor = {"row": "3.1 Parts", "name": "a.pdf", "ordinal": 0}
        result = ariba_files.listing_from_anchors([dict(anchor), dict(anchor)])
        assert len(result["files"]) == 1

    def test_an_indistinguishable_duplicate_is_reported_not_silently_dropped(self):
        """Two rows whose flattened text is identical: we cannot tell them apart, so the
        collapse is recorded rather than left as a hole nothing mentions."""
        anchor = {"row": "Attachments a.pdf", "name": "a.pdf", "ordinal": 0}
        result = ariba_files.listing_from_anchors([dict(anchor), dict(anchor)])
        assert len(result["files"]) == 1
        assert result["collided"] == [{"key": result["files"][0]["key"], "name": "a.pdf"}]

    def test_the_entry_carries_the_ordinal_the_adapter_needs_to_click_it(self):
        result = ariba_files.listing_from_anchors(
            [{"row": "3.1 Parts", "name": "a.pdf", "ordinal": 2}])
        assert result["files"][0]["ordinal"] == 2

    def test_no_key_encodes_the_listing_position(self):
        """The exact Critical: with a positional key the ordered (key, name) pairs come out
        byte-identical after a reorder, so a resumed run adopts the partials POSITIONALLY --
        one document stored twice, another lost, counts matching, no gap recorded."""
        anchors = [{"row": "3.1 Part A", "name": "report.pdf", "ordinal": 0},
                   {"row": "4.2 Part B", "name": "report.pdf", "ordinal": 0}]
        forward = ariba_files.listing_from_anchors(anchors)["files"]
        reverse = ariba_files.listing_from_anchors(list(reversed(anchors)))["files"]

        assert {f["key"] for f in forward} == {f["key"] for f in reverse}
        assert (ariba_files.make_fingerprint(forward, 2)
                != ariba_files.make_fingerprint(reverse, 2))

    def test_an_empty_read_is_an_empty_listing(self):
        assert ariba_files.listing_from_anchors([]) == {
            "files": [], "rejected": [], "collided": []}


class TestIsOutlineRow:
    """A positive marker that the content tree, not some other page, is in front of us
    (#174 M4) -- an outline-numbered row is the tree's own row addressing and nothing else
    on the event's pages looks like it."""

    def test_a_row_leading_with_an_outline_number_is_true(self):
        assert ariba_files.is_outline_row("3.1 Drawings Package plan.dwg 787.7 MB")

    def test_a_row_with_no_outline_number_is_false(self):
        assert not ariba_files.is_outline_row("Addendum 1.pdf 2.1 MB")

    def test_none_and_empty_are_false(self):
        assert not ariba_files.is_outline_row(None)
        assert not ariba_files.is_outline_row("")


class TestOrderListing:
    """The bundle's order must be a property of the tree, not of where a sweep started."""

    def test_outline_rows_sort_numerically_not_as_strings(self):
        files = ariba_files.listing_from_anchors([
            {"row": "4.10 Late", "name": "j.pdf", "ordinal": 0},
            {"row": "4.9 Early", "name": "i.pdf", "ordinal": 0},
        ])["files"]
        assert [f["name"] for f in ariba_files.order_listing(files)] == ["i.pdf", "j.pdf"]

    def test_two_files_in_one_row_stay_in_their_dom_order(self):
        files = ariba_files.listing_from_anchors([
            {"row": "3.1 Parts", "name": "b.pdf", "ordinal": 1},
            {"row": "3.1 Parts", "name": "a.pdf", "ordinal": 0},
        ])["files"]
        assert [f["name"] for f in ariba_files.order_listing(files)] == ["a.pdf", "b.pdf"]

    def test_unnumbered_rows_sort_after_numbered_ones_deterministically(self):
        files = ariba_files.listing_from_anchors([
            {"row": "Addendum 2.pdf 1 MB", "name": "Addendum 2.pdf", "ordinal": 0},
            {"row": "3.1 Parts", "name": "a.pdf", "ordinal": 0},
            {"row": "Addendum 1.pdf 1 MB", "name": "Addendum 1.pdf", "ordinal": 0},
        ])["files"]
        assert [f["name"] for f in ariba_files.order_listing(files)] == [
            "a.pdf", "Addendum 1.pdf", "Addendum 2.pdf"]

    def test_the_order_does_not_depend_on_the_order_it_was_given(self):
        anchors = [{"row": "3.1 A", "name": "a.pdf", "ordinal": 0},
                   {"row": "3.2 B", "name": "b.pdf", "ordinal": 0},
                   {"row": "Addendum.pdf", "name": "Addendum.pdf", "ordinal": 0}]
        forward = ariba_files.order_listing(ariba_files.listing_from_anchors(anchors)["files"])
        reverse = ariba_files.order_listing(
            ariba_files.listing_from_anchors(list(reversed(anchors)))["files"])
        assert forward == reverse


# --- Low: a dropped collision is greppable in the durable record, not just the log (#174) --

def test_write_omitted_records_a_collided_count(tmp_path):
    path = ariba_files.write_omitted(tmp_path / "Doc1.zip", [], 3, 3, collided=1)

    assert path is not None
    assert json.loads(path.read_text())["collided"] == 1


def test_write_omitted_is_still_a_noop_when_nothing_is_missing_or_collided(tmp_path):
    assert ariba_files.write_omitted(tmp_path / "Doc1.zip", [], 3, 3) is None
    assert ariba_files.write_omitted(tmp_path / "Doc1.zip", [], 3, 3, collided=0) is None


def test_clear_omitted_when_complete_keeps_a_record_a_collision_still_explains(tmp_path):
    """Counts matching is not evidence nothing is wrong when a collision was dropped."""
    stale = (tmp_path / "Doc1.zip").with_suffix(".omitted.json")
    stale.write_text(json.dumps(
        {"omitted": [], "expected_files": 1, "actual_files": 1, "collided": 1}))

    ariba_files.clear_omitted_when_complete(tmp_path / "Doc1.zip", [], 1, 1, collided=1)

    assert stale.exists()


def test_clear_omitted_when_complete_still_clears_a_clean_stale_record(tmp_path):
    stale = (tmp_path / "Doc1.zip").with_suffix(".omitted.json")
    stale.write_text(json.dumps({"omitted": [], "expected_files": 1, "actual_files": 1}))

    ariba_files.clear_omitted_when_complete(tmp_path / "Doc1.zip", [], 1, 1)

    assert not stale.exists()


def test_capture_files_folds_a_collided_count_from_the_source_into_the_omitted_record(
        tmp_path):
    """`list_files` can drop a collision (two rows read as identical, #174) that never shows
    up as a shortfall against `expected_count` -- so it must reach the durable record on its
    own terms, not only through the PROVISIONAL count-mismatch log line."""
    class SourceWithCollisions(FakeFileSource):
        def collided_count(self):
            return 2

    source = SourceWithCollisions(["a.pdf"])

    ariba_files.capture_files(source, "5713434353", tmp_path)

    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["collided"] == 2


def test_capture_files_defaults_collided_to_zero_for_a_source_without_the_method(tmp_path):
    """The FileSource protocol's other three methods must not gain a hard new requirement --
    `collided_count` is read defensively, so a source that lacks it is just uncounted, not
    broken."""
    source = FakeFileSource(["a.pdf", "bad.pdf"], fail_on=["bad.pdf"])

    ariba_files.capture_files(source, "5713434353", tmp_path)

    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body.get("collided", 0) == 0
