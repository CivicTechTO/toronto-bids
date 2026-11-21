"""Tests for per-file Ariba document capture (ariba_files)."""

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
    assert ariba_files.unique_names(["a.pdf", "a_2.pdf", "a.pdf"]) == [
        "a.pdf",
        "a_2.pdf",
        "a_3.pdf",
    ]


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

    target = ariba_files.build_bundle(
        [(a, "a.pdf"), (b, "b_2.pdf")], tmp_path / "Doc1.zip"
    )

    with zipfile.ZipFile(target) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b_2.pdf"]
        assert zf.read("a.pdf") == b"aaa"


def test_build_bundle_is_atomic_leaving_no_target_on_failure(tmp_path):
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
    """In-memory FileSource fake supporting failures, interrupts, custom keys, and append mode."""

    def __init__(
        self, names, fail_on=(), contents=None, keys=None, kill_on=(), append=False
    ):
        self.names = list(names)
        self.keys = list(keys) if keys is not None else self._default_keys(self.names)
        self.fail_on = set(fail_on)
        self.kill_on = set(kill_on)
        self.contents = contents or {}
        self.append = append
        self.downloaded = []

    @staticmethod
    def _default_keys(names):
        """Return `name#occurrence` keys independent of overall list position."""
        counts: dict = {}
        out = []
        for name in names:
            counts[name] = counts.get(name, 0) + 1
            out.append(f"{name}#{counts[name]}")
        return out

    def list_files(self):
        return [{"key": k, "name": n, "row": n} for k, n in zip(self.keys, self.names)]

    def _payload(self, file) -> bytes:
        if file["key"] in self.contents:
            return self.contents[file["key"]]
        return self.contents.get(file["name"], file["name"].encode())

    def _die_midway(self, dest, exc):
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


def test_a_failed_download_leaves_no_part_file_behind(tmp_path):
    # All files fail so partial_dir survives to inspect for leaked .part files.
    source = FakeFileSource(["a.pdf", "b.pdf"], fail_on=["a.pdf", "b.pdf"])

    assert ariba_files.capture_files(source, "5713434353", tmp_path) is None

    assert ariba_files.partial_dir(tmp_path, "5713434353").exists()
    assert list(tmp_path.rglob("*.part")) == []


def test_when_every_file_fails_nothing_is_archived_and_the_partials_are_kept(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"], fail_on=["a.pdf", "b.pdf"])

    assert ariba_files.capture_files(source, "5713434353", tmp_path) is None

    assert not (tmp_path / "Doc5713434353.zip").exists()
    assert (ariba_files.partial_dir(tmp_path, "5713434353") / "files").is_dir()


def test_a_run_that_captured_almost_nothing_stays_pending_instead_of_archiving(
    tmp_path,
):
    source = FakeFileSource(
        ["a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf"],
        fail_on=["b.pdf", "c.pdf", "d.pdf", "e.pdf"],
    )
    messages = []

    assert (
        ariba_files.capture_files(source, "5713434353", tmp_path, log=messages.append)
        is None
    )

    assert not (tmp_path / "Doc5713434353.zip").exists()
    assert not (tmp_path / "Doc5713434353.omitted.json").exists()
    assert (
        ariba_files.partial_dir(tmp_path, "5713434353") / "files" / "a.pdf"
    ).is_file()
    assert any("1 of 5" in m for m in messages), messages


def test_a_mostly_successful_capture_is_still_archived_with_its_gap_recorded(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf", "c.pdf", "d.pdf"], fail_on=["d.pdf"])

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b.pdf", "c.pdf"]
    assert json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())[
        "omitted"
    ] == ["d.pdf"]


def test_resume_skips_files_already_complete_on_disk(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"already here")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files()))

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    assert source.downloaded == ["b.pdf"]
    with zipfile.ZipFile(bundle) as zf:
        assert zf.read("a.pdf") == b"already here"


def test_a_resume_over_a_part_left_by_a_kill_refetches_it_cleanly(tmp_path):
    # append=True ensures a surviving .part would corrupt b.pdf if not unlinked first.
    source = FakeFileSource(
        ["a.pdf", "b.pdf"], contents={"b.pdf": b"WHOLE"}, append=True
    )
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"a.pdf")
    (pdir / "files" / "b.pdf.part").write_bytes(b"HALF")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files()))

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b.pdf"]
        assert zf.read("b.pdf") == b"WHOLE"
    assert not (tmp_path / "Doc5713434353.omitted.json").exists()


def test_a_changed_event_preserves_old_partials_without_reusing_them(tmp_path):
    # Stale partial shares a name with the new listing to verify it is not reused.
    source = FakeFileSource(["a.pdf", "b.pdf"], contents={"a.pdf": b"FRESH"})
    stale = FakeFileSource(["a.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"STALE")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(stale.list_files()))

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.read("a.pdf") == b"FRESH"
        assert any(zf.read(name)==b"STALE" for name in zf.namelist())


def test_duplicate_names_across_the_tree_are_both_kept(tmp_path):
    source = FakeFileSource(["dup.pdf", "dup.pdf"], contents={"dup.pdf": b"first"})

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["dup.pdf", "dup_2.pdf"]


def test_the_fingerprint_sees_a_reordered_listing():
    first = [{"key": "X", "name": "report.pdf"}, {"key": "Y", "name": "report.pdf"}]
    second = [{"key": "Y", "name": "report.pdf"}, {"key": "X", "name": "report.pdf"}]

    assert ariba_files.make_fingerprint(first) != ariba_files.make_fingerprint(second)


def test_the_fingerprint_sees_a_substituted_identity_behind_an_unchanged_name():
    kept = [{"key": "X", "name": "report.pdf"}]
    swapped = [{"key": "Z", "name": "report.pdf"}]

    assert ariba_files.make_fingerprint(kept) != ariba_files.make_fingerprint(swapped)


def test_the_fingerprint_survives_a_json_round_trip(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"])
    fp = ariba_files.make_fingerprint(source.list_files())
    ariba_files.write_manifest(tmp_path, fp)

    assert ariba_files.read_manifest(tmp_path)["fingerprint"] == fp


def test_a_reordered_listing_with_duplicate_names_does_not_reuse_the_partials(tmp_path):
    doc = "5713434353"
    contents = {"X": b"DOC-X", "Y": b"DOC-Y"}
    run1 = FakeFileSource(
        ["report.pdf", "report.pdf"], keys=["X", "Y"], contents=contents, kill_on=["Y"]
    )
    with pytest.raises(KeyboardInterrupt):
        ariba_files.capture_files(run1, doc, tmp_path)
    assert (ariba_files.partial_dir(tmp_path, doc) / "files" / "report.pdf").exists()

    run2 = FakeFileSource(
        ["report.pdf", "report.pdf"], keys=["Y", "X"], contents=contents
    )
    bundle = ariba_files.capture_files(run2, doc, tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert {"report.pdf", "report_2.pdf"}.issubset(zf.namelist())
        assert zf.read("report.pdf") == b"DOC-Y"
        assert zf.read("report_2.pdf") == b"DOC-X"
        assert {zf.read(n) for n in zf.namelist()} == {
            b"DOC-X",
            b"DOC-Y",
        }


def test_the_gap_record_is_written_before_the_bundle(tmp_path, monkeypatch):
    source = FakeFileSource(["a.pdf", "bad.pdf"], fail_on=["bad.pdf"])

    replace = ariba_files.os.replace

    def boom(src, dst):
        if str(dst).endswith('Doc5713434353.zip'):
            assert (tmp_path / 'Doc5713434353.omitted.json').exists()
            raise OSError("ENOSPC: no space left on device")
        return replace(src, dst)

    monkeypatch.setattr(ariba_files.os, "replace", boom)

    with pytest.raises(OSError):
        ariba_files.capture_files(source, "5713434353", tmp_path)

    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["bad.pdf"]
    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_the_partial_directory_is_this_modules_own_namespace(tmp_path):
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")

    assert pdir == tmp_path / ".partial-files" / "Doc5713434353"
    assert pdir != tmp_path / ".partial" / "Doc5713434353"


def test_a_complete_capture_clears_a_stale_gap_record(tmp_path):
    stale = tmp_path / "Doc5713434353.omitted.json"
    stale.write_text(json.dumps({"omitted": ["gone.pdf"]}))
    source = FakeFileSource(["a.pdf", "gone.pdf"])

    ariba_files.capture_files(source, "5713434353", tmp_path)

    assert not stale.exists()


def test_a_zero_byte_download_is_a_failure_not_a_silent_hole(tmp_path):
    source = FakeFileSource(["a.pdf", "empty.pdf"], contents={"empty.pdf": b""})

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["a.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["empty.pdf"]
    assert list(tmp_path.rglob("*.part")) == []


def test_a_kill_mid_transfer_is_not_swallowed_as_one_dead_file(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"], kill_on=["b.pdf"])

    with pytest.raises(KeyboardInterrupt):
        ariba_files.capture_files(source, "5713434353", tmp_path)

    assert list(tmp_path.rglob("*.part")) == []
    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_finalise_partial_refuses_an_open_posting(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"], fail_on=["a.pdf", "b.pdf"])
    ariba_files.capture_files(source, "5713434353", tmp_path)

    with pytest.raises(ValueError, match="CLOSED"):
        ariba_files.finalise_partial("5713434353", tmp_path, posting_open=True)

    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_finalise_partial_is_none_when_there_is_nothing_to_finalise(tmp_path):
    assert (
        ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False) is None
    )
    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_finalise_partial_bundles_what_is_on_disk_and_records_the_rest(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf", "c.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files()))

    bundle = ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    assert bundle == tmp_path / "Doc5713434353.zip"
    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["a.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["b.pdf", "c.pdf"]
    assert not pdir.exists()


def test_finalise_partial_keeps_a_part_file_rather_than_deleting_it(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    (pdir / "files" / "b.pdf.part").write_bytes(b"HALF")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files()))

    bundle = ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["a.pdf"]
    assert json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())[
        "omitted"
    ] == ["b.pdf"]
    assert (pdir / "files" / "b.pdf.part").exists()


def test_a_corrupt_manifest_discards_partials_instead_of_trusting_them_positionally(
    tmp_path,
):
    doc = "5713434353"
    pdir = ariba_files.partial_dir(tmp_path, doc)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "report.pdf").write_bytes(b"DOC-X")
    (pdir / ariba_files.MANIFEST_NAME).write_text(
        "{not valid json"
    )

    source = FakeFileSource(
        ["report.pdf", "report.pdf"],
        keys=["Y", "X"],
        contents={"X": b"DOC-X", "Y": b"DOC-Y"},
    )

    bundle = ariba_files.capture_files(source, doc, tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert {zf.read(n) for n in zf.namelist()} == {
            b"DOC-X",
            b"DOC-Y",
        }
    assert not (tmp_path / f"Doc{doc}.omitted.json").exists()
    assert source.downloaded.count("report.pdf") == 2


def test_a_stable_nonsequential_key_reorder_survives_after_an_interrupted_run(tmp_path):
    doc = "5713434353"
    contents = {"aria-QQ7": b"DOC-Q", "aria-ZZ2": b"DOC-Z"}
    run1 = FakeFileSource(
        ["report.pdf", "report.pdf"],
        keys=["aria-QQ7", "aria-ZZ2"],
        contents=contents,
        kill_on=["aria-ZZ2"],
    )
    with pytest.raises(KeyboardInterrupt):
        ariba_files.capture_files(run1, doc, tmp_path)
    assert (ariba_files.partial_dir(tmp_path, doc) / "files" / "report.pdf").exists()

    run2 = FakeFileSource(
        ["report.pdf", "report.pdf"], keys=["aria-ZZ2", "aria-QQ7"], contents=contents
    )
    bundle = ariba_files.capture_files(run2, doc, tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert {zf.read(n) for n in zf.namelist()} == {
            b"DOC-Q",
            b"DOC-Z",
        }
    assert not (tmp_path / f"Doc{doc}.omitted.json").exists()


def test_fake_file_source_default_keys_are_not_positional(tmp_path):
    forward = {
        f["name"]: f["key"] for f in FakeFileSource(["a.pdf", "b.pdf"]).list_files()
    }
    backward = {
        f["name"]: f["key"] for f in FakeFileSource(["b.pdf", "a.pdf"]).list_files()
    }
    assert forward == backward

    dup_keys = [f["key"] for f in FakeFileSource(["dup.pdf", "dup.pdf"]).list_files()]
    assert len(set(dup_keys)) == 2
    assert dup_keys != ["0", "1"]


def test_a_stale_gap_record_survives_if_the_bundle_write_then_fails(
    tmp_path, monkeypatch
):
    stale = tmp_path / "Doc5713434353.omitted.json"
    stale.write_text(json.dumps({"omitted": ["gone.pdf"]}))
    source = FakeFileSource(["a.pdf"])

    def boom(files, target, **kwargs):
        raise OSError("ENOSPC: no space left on device")

    monkeypatch.setattr(ariba_files, "build_bundle", boom)

    with pytest.raises(OSError):
        ariba_files.capture_files(source, "5713434353", tmp_path)

    assert stale.exists()
    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_finalise_partial_keeps_a_stale_gap_record_if_the_bundle_write_then_fails(
    tmp_path, monkeypatch
):
    stale = tmp_path / "Doc5713434353.omitted.json"
    stale.write_text(json.dumps({"omitted": ["gone.pdf"]}))
    source = FakeFileSource(["a.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files()))

    def boom(files, target, **kwargs):
        raise OSError("ENOSPC: no space left on device")

    monkeypatch.setattr(ariba_files, "build_bundle", boom)

    with pytest.raises(OSError):
        ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    assert stale.exists()
    assert not (tmp_path / "Doc5713434353.zip").exists()


def test_omitted_names_a_duplicate_by_its_disambiguated_zip_name(tmp_path):
    source = FakeFileSource(
        ["report.pdf", "report.pdf"], keys=["A", "B"], fail_on=["B"]
    )

    bundle = ariba_files.capture_files(source, "5713434353", tmp_path)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["report.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["report_2.pdf"]


def test_finalise_partial_names_a_duplicate_omission_by_its_disambiguated_zip_name(
    tmp_path,
):
    source = FakeFileSource(["report.pdf", "report.pdf"], keys=["A", "B"])
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "report.pdf").write_bytes(b"AAA")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files()))

    bundle = ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["report.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["omitted"] == ["report_2.pdf"]


def test_finalise_partial_salvages_files_a_lost_manifest_cannot_name(tmp_path):
    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    (pdir / ariba_files.MANIFEST_NAME).write_text("not json at all")

    bundle = ariba_files.finalise_partial("5713434353", tmp_path, posting_open=False)

    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["a.pdf"]
    assert not (tmp_path / "Doc5713434353.omitted.json").exists()


class TestIsDocumentName:
    def test_a_plain_filename_is_a_document(self):
        assert ariba_files.is_document_name("Addendum 1.pdf")

    def test_trailing_metadata_does_not_hide_the_extension(self):
        assert ariba_files.is_document_name("Appendix C2 - Drawings.pdf (1.2 MB)")
        assert ariba_files.is_document_name("site.dwg   4,102 KB")

    def test_the_formats_the_anchored_list_omitted(self):
        for name in (
            "plan.dwf",
            "prices.xlsm",
            "scan.tif",
            "scan.tiff",
            "notice.msg",
            "bundle.7z",
            "dump.gz",
            "site.kmz",
        ):
            assert ariba_files.is_document_name(name), name

    def test_the_formats_that_were_always_covered(self):
        for name in (
            "a.pdf",
            "a.zip",
            "a.doc",
            "a.docx",
            "a.xls",
            "a.xlsx",
            "a.dwg",
            "a.rtf",
            "a.txt",
            "a.jpg",
            "a.jpeg",
            "a.png",
            "a.csv",
            "a.ppt",
            "a.pptx",
        ):
            assert ariba_files.is_document_name(name), name

    def test_the_extension_is_matched_case_insensitively(self):
        assert ariba_files.is_document_name("PLAN.DWG")

    def test_a_bare_label_is_not_a_document(self):
        for name in (
            "References",
            "Download this attachment",
            "",
            "   ",
            "Section 3.1",
        ):
            assert not ariba_files.is_document_name(name), name

    def test_a_url_is_not_a_document_even_when_it_ends_in_pdf(self):
        assert not ariba_files.is_document_name("https://example.org/a.pdf")
        assert not ariba_files.is_document_name("http://example.org/a.pdf")
        assert not ariba_files.is_document_name("www.example.org/a.pdf")

    def test_an_extension_glued_to_more_letters_is_not_an_extension(self):
        assert not ariba_files.is_document_name("notes.pdfx")


class TestAnchorKey:
    def test_a_row_that_leads_with_an_outline_number_keys_on_it(self):
        key = ariba_files.anchor_key(
            {
                "row": "3.1 Drawings Package plan.dwg 787.7 MB",
                "name": "plan.dwg",
                "ordinal": 0,
            }
        )
        assert key.startswith("3.1#")

    def test_a_row_with_no_outline_number_keys_on_its_own_text(self):
        key = ariba_files.anchor_key(
            {"row": "Addendum 1.pdf 2.1 MB", "name": "Addendum 1.pdf", "ordinal": 0}
        )
        assert "Addendum 1.pdf 2.1 MB" in key
        assert not key.startswith("#")

    def test_two_files_in_one_row_sharing_a_name_are_different_documents(self):
        a = ariba_files.anchor_key(
            {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 0}
        )
        b = ariba_files.anchor_key(
            {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 1}
        )
        assert a != b

    def test_the_same_filename_in_two_different_rows_is_two_documents(self):
        a = ariba_files.anchor_key(
            {"row": "3.1 Part A", "name": "report.pdf", "ordinal": 0}
        )
        b = ariba_files.anchor_key(
            {"row": "4.2 Part B", "name": "report.pdf", "ordinal": 0}
        )
        assert a != b

    def test_the_key_survives_a_reordered_traversal(self):
        entry = {"row": "4.2 Part B", "name": "report.pdf", "ordinal": 1}
        assert ariba_files.anchor_key(entry) == ariba_files.anchor_key(dict(entry))

    def test_whitespace_and_nbsp_in_the_row_do_not_change_the_key(self):
        a = ariba_files.anchor_key(
            {"row": "3.1  Part\xa0A", "name": "a.pdf", "ordinal": 0}
        )
        b = ariba_files.anchor_key({"row": "3.1 Part A", "name": "a.pdf", "ordinal": 0})
        assert a == b

    def test_the_key_does_NOT_survive_the_menu_reparenting_which_is_why_it_is_not_clicked_on(
        self,
    ):
        # AribaWeb reparents menu containers after the first click, shifting closest('tr') and ordinal.
        blob = (
            "Reference Documents Attachment 1 Field Services Manual Appendix E.zip "
            "Attachment 2 Arborist Report.zip Attachment 3 Tree"
        )
        before = ariba_files.anchor_key(
            {"row": blob, "name": "Attachment 2 Arborist Report.zip", "ordinal": 0}
        )
        after = ariba_files.anchor_key(
            {
                "row": "3.1 Specifications",
                "name": "Attachment 2 Arborist Report.zip",
                "ordinal": 1,
            }
        )
        assert before != after


class TestHandleAndUnclaimed:
    def test_the_dom_id_rides_along_as_a_handle(self):
        files = ariba_files.listing_from_anchors(
            [{"name": "a.pdf", "row": "1.1 x", "ordinal": 0, "id": "_obgqjc"}]
        )["files"]
        assert files[0]["handle"] == "_obgqjc"

    def test_a_respawned_id_does_not_change_the_fingerprint(self):
        first = ariba_files.listing_from_anchors(
            [{"name": "a.pdf", "row": "1.1 x", "ordinal": 0, "id": "_obgqjc"}]
        )["files"]
        second = ariba_files.listing_from_anchors(
            [
                {
                    "name": "a.pdf",
                    "row": "1.1 x",
                    "ordinal": 0,
                    "id": "_TOTALLY_DIFFERENT",
                }
            ]
        )["files"]
        assert ariba_files.make_fingerprint(second) == ariba_files.make_fingerprint(
            first
        )

    def test_an_anchor_with_no_id_is_still_listed(self):
        files = ariba_files.listing_from_anchors(
            [{"name": "a.pdf", "row": "1.1 x", "ordinal": 0}]
        )["files"]
        assert files[0]["handle"] == ""

    def test_it_finds_the_only_link_of_that_name(self):
        anchors = [{"name": "a.pdf", "id": "_1"}, {"name": "b.pdf", "id": "_2"}]
        assert ariba_files.pick_unclaimed(anchors, "b.pdf", set())["id"] == "_2"

    def test_a_claimed_anchor_is_skipped_so_a_duplicate_name_cannot_refetch_the_first(
        self,
    ):
        anchors = [
            {"name": "report.pdf", "id": "_1"},
            {"name": "report.pdf", "id": "_2"},
        ]
        assert ariba_files.pick_unclaimed(anchors, "report.pdf", {"_1"})["id"] == "_2"

    def test_it_returns_none_when_every_link_of_that_name_is_claimed(self):
        anchors = [{"name": "report.pdf", "id": "_1"}]
        assert ariba_files.pick_unclaimed(anchors, "report.pdf", {"_1"}) is None

    def test_it_returns_none_when_the_name_is_absent(self):
        assert (
            ariba_files.pick_unclaimed([{"name": "a.pdf", "id": "_1"}], "z.pdf", set())
            is None
        )

    def test_nbsp_and_run_together_whitespace_still_match(self):
        anchors = [{"name": "Attachment\xa01  Report.zip", "id": "_1"}]
        assert (
            ariba_files.pick_unclaimed(anchors, "Attachment 1 Report.zip", set())["id"]
            == "_1"
        )

    def test_a_popup_menu_item_is_recognised_by_any_of_the_three_markers(self):
        for anchor in ({"role": "menuitem"}, {"bh": "PMI"}, {"cls": "w-pmi-item rr"}):
            assert ariba_files.anchor_kind(anchor) == ariba_files.MENU_ITEM, anchor

    def test_a_tree_link_is_not_mistaken_for_a_menu_item(self):
        assert (
            ariba_files.anchor_kind({"bh": "PML", "cls": "leg-p-r-9"})
            == ariba_files.TREE_LINK
        )

    def test_an_unrecognised_anchor_defaults_to_the_self_checking_path(self):
        assert ariba_files.anchor_kind({}) == ariba_files.TREE_LINK

    def test_the_listing_carries_the_kind(self):
        files = ariba_files.listing_from_anchors(
            [
                {
                    "name": "a.pdf",
                    "row": "1.1 x",
                    "ordinal": 0,
                    "id": "_1",
                    "bh": "PML",
                },
                {
                    "name": "b.zip",
                    "row": "3.1 y",
                    "ordinal": 0,
                    "id": "_2",
                    "role": "menuitem",
                },
            ]
        )["files"]
        assert [f["kind"] for f in files] == [
            ariba_files.TREE_LINK,
            ariba_files.MENU_ITEM,
        ]

    def test_an_id_less_anchor_is_matchable_but_never_counts_as_claimed(self):
        anchors = [{"name": "a.pdf", "id": ""}]
        assert ariba_files.pick_unclaimed(anchors, "a.pdf", {""}) is not None


class TestListingFromAnchors:
    def test_it_keeps_both_of_two_same_named_files_in_one_row(self):
        result = ariba_files.listing_from_anchors(
            [
                {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 0},
                {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 1},
            ]
        )
        assert [f["name"] for f in result["files"]] == ["report.pdf", "report.pdf"]
        assert result["files"][0]["key"] != result["files"][1]["key"]
        assert result["collided"] == []

    def test_it_reports_what_it_rejected(self):
        result = ariba_files.listing_from_anchors(
            [
                {"row": "3.1", "name": "a.pdf", "ordinal": 0},
                {"row": "3.1", "name": "References", "ordinal": 1},
            ]
        )
        assert [f["name"] for f in result["files"]] == ["a.pdf"]
        assert result["rejected"] == ["References"]

    def test_a_second_reading_of_the_same_anchor_is_one_file(self):
        anchor = {"row": "3.1 Parts", "name": "a.pdf", "ordinal": 0}
        result = ariba_files.listing_from_anchors([dict(anchor), dict(anchor)])
        assert len(result["files"]) == 1

    def test_an_indistinguishable_duplicate_is_reported_not_silently_dropped(self):
        anchor = {"row": "Attachments a.pdf", "name": "a.pdf", "ordinal": 0}
        result = ariba_files.listing_from_anchors([dict(anchor), dict(anchor)])
        assert len(result["files"]) == 1
        assert result["collided"] == [
            {"key": result["files"][0]["key"], "name": "a.pdf"}
        ]

    def test_the_entry_carries_the_ordinal_the_adapter_needs_to_click_it(self):
        result = ariba_files.listing_from_anchors(
            [{"row": "3.1 Parts", "name": "a.pdf", "ordinal": 2}]
        )
        assert result["files"][0]["ordinal"] == 2

    def test_no_key_encodes_the_listing_position(self):
        anchors = [
            {"row": "3.1 Part A", "name": "report.pdf", "ordinal": 0},
            {"row": "4.2 Part B", "name": "report.pdf", "ordinal": 0},
        ]
        forward = ariba_files.listing_from_anchors(anchors)["files"]
        reverse = ariba_files.listing_from_anchors(list(reversed(anchors)))["files"]

        assert {f["key"] for f in forward} == {f["key"] for f in reverse}
        assert ariba_files.make_fingerprint(forward) != ariba_files.make_fingerprint(
            reverse
        )

    def test_an_empty_read_is_an_empty_listing(self):
        assert ariba_files.listing_from_anchors([]) == {
            "files": [],
            "rejected": [],
            "collided": [],
        }


class TestIsOutlineRow:
    def test_a_row_leading_with_an_outline_number_is_true(self):
        assert ariba_files.is_outline_row("3.1 Drawings Package plan.dwg 787.7 MB")

    def test_a_row_with_no_outline_number_is_false(self):
        assert not ariba_files.is_outline_row("Addendum 1.pdf 2.1 MB")

    def test_none_and_empty_are_false(self):
        assert not ariba_files.is_outline_row(None)
        assert not ariba_files.is_outline_row("")


class TestOrderListing:
    def test_outline_rows_sort_numerically_not_as_strings(self):
        files = ariba_files.listing_from_anchors(
            [
                {"row": "4.10 Late", "name": "j.pdf", "ordinal": 0},
                {"row": "4.9 Early", "name": "i.pdf", "ordinal": 0},
            ]
        )["files"]
        assert [f["name"] for f in ariba_files.order_listing(files)] == [
            "i.pdf",
            "j.pdf",
        ]

    def test_two_files_in_one_row_stay_in_their_dom_order(self):
        files = ariba_files.listing_from_anchors(
            [
                {"row": "3.1 Parts", "name": "b.pdf", "ordinal": 1},
                {"row": "3.1 Parts", "name": "a.pdf", "ordinal": 0},
            ]
        )["files"]
        assert [f["name"] for f in ariba_files.order_listing(files)] == [
            "a.pdf",
            "b.pdf",
        ]

    def test_unnumbered_rows_sort_after_numbered_ones_deterministically(self):
        files = ariba_files.listing_from_anchors(
            [
                {"row": "Addendum 2.pdf 1 MB", "name": "Addendum 2.pdf", "ordinal": 0},
                {"row": "3.1 Parts", "name": "a.pdf", "ordinal": 0},
                {"row": "Addendum 1.pdf 1 MB", "name": "Addendum 1.pdf", "ordinal": 0},
            ]
        )["files"]
        assert [f["name"] for f in ariba_files.order_listing(files)] == [
            "a.pdf",
            "Addendum 1.pdf",
            "Addendum 2.pdf",
        ]

    def test_the_order_does_not_depend_on_the_order_it_was_given(self):
        anchors = [
            {"row": "3.1 A", "name": "a.pdf", "ordinal": 0},
            {"row": "3.2 B", "name": "b.pdf", "ordinal": 0},
            {"row": "Addendum.pdf", "name": "Addendum.pdf", "ordinal": 0},
        ]
        forward = ariba_files.order_listing(
            ariba_files.listing_from_anchors(anchors)["files"]
        )
        reverse = ariba_files.order_listing(
            ariba_files.listing_from_anchors(list(reversed(anchors)))["files"]
        )
        assert forward == reverse


def test_write_omitted_records_a_collided_count(tmp_path):
    path = ariba_files.write_omitted(tmp_path / "Doc1.zip", [], collided=1)

    assert path is not None
    assert json.loads(path.read_text())["collided"] == 1


def test_write_omitted_is_still_a_noop_when_nothing_is_missing_or_collided(tmp_path):
    assert ariba_files.write_omitted(tmp_path / "Doc1.zip", []) is None
    assert ariba_files.write_omitted(tmp_path / "Doc1.zip", [], collided=0) is None


def test_clear_omitted_when_complete_keeps_a_record_a_collision_still_explains(
    tmp_path,
):
    stale = (tmp_path / "Doc1.zip").with_suffix(".omitted.json")
    stale.write_text(json.dumps({"omitted": [], "collided": 1}))

    ariba_files.clear_omitted_when_complete(tmp_path / "Doc1.zip", [], collided=1)

    assert stale.exists()


def test_clear_omitted_when_complete_still_clears_a_clean_stale_record(tmp_path):
    stale = (tmp_path / "Doc1.zip").with_suffix(".omitted.json")
    stale.write_text(json.dumps({"omitted": []}))

    ariba_files.clear_omitted_when_complete(tmp_path / "Doc1.zip", [])

    assert not stale.exists()


def test_capture_files_folds_a_collided_count_from_the_source_into_the_omitted_record(
    tmp_path,
):
    class SourceWithCollisions(FakeFileSource):
        def collided_count(self):
            return 2

    source = SourceWithCollisions(["a.pdf"])

    ariba_files.capture_files(source, "5713434353", tmp_path)

    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body["collided"] == 2


def test_capture_files_defaults_collided_to_zero_for_a_source_without_the_method(
    tmp_path,
):
    source = FakeFileSource(["a.pdf", "bad.pdf"], fail_on=["bad.pdf"])

    ariba_files.capture_files(source, "5713434353", tmp_path)

    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert body.get("collided", 0) == 0


def test_recapture_with_fewer_files_preserves_withdrawn_files(tmp_path):
    ariba_files.capture_files(
        FakeFileSource(["a.pdf", "b.pdf", "c.pdf"]), "5713434353", tmp_path
    )
    target = tmp_path / "Doc5713434353.zip"
    assert target.exists()

    messages = []
    result = ariba_files.capture_files(
        FakeFileSource(["a.pdf", "b.pdf"]),
        "5713434353",
        tmp_path,
        log=messages.append,
    )

    assert result == target
    with zipfile.ZipFile(target) as zf:
        assert zf.read("c.pdf") == b"c.pdf"
        assert len(zf.namelist()) == 3


def test_recapture_with_equal_or_more_files_proceeds(tmp_path):
    source_small = FakeFileSource(["a.pdf", "b.pdf"])
    ariba_files.capture_files(source_small, "5713434353", tmp_path)
    target = tmp_path / "Doc5713434353.zip"
    assert target.exists()

    source_bigger = FakeFileSource(["a.pdf", "b.pdf", "c.pdf"])
    result = ariba_files.capture_files(source_bigger, "5713434353", tmp_path)

    assert result == target
    with zipfile.ZipFile(target) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b.pdf", "c.pdf"]


def test_first_capture_has_no_shrink_check(tmp_path):
    source = FakeFileSource(["a.pdf"])
    result = ariba_files.capture_files(source, "5713434353", tmp_path)
    assert result == tmp_path / "Doc5713434353.zip"


def test_version_merge_keeps_nested_historical_leaves(tmp_path):
    inner_zip = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner_zip, "w") as zf:
        zf.writestr("nested_a.pdf", "aaa")
        zf.writestr("nested_b.pdf", "bbb")

    source_with_nested = FakeFileSource(
        ["a.pdf", "b.pdf", "drawings.zip"],
        contents={"drawings.zip": inner_zip.read_bytes()},
    )
    ariba_files.capture_files(source_with_nested, "5713434353", tmp_path)
    target = tmp_path / "Doc5713434353.zip"
    assert target.exists()

    before = target.read_bytes()
    gap = target.with_suffix('.omitted.json')
    gap.write_text('{"omitted": ["missing.pdf"]}')
    messages = []
    source_same_count = FakeFileSource(["x.pdf", "y.pdf", "z.pdf"])
    result = ariba_files.capture_files(source_same_count, "5713434353", tmp_path,
                                       log=messages.append)

    assert result == target
    with zipfile.ZipFile(target) as archive:
        assert archive.read("drawings.zip") == inner_zip.read_bytes()
    from toronto_bids.sources.zip_index import index_zip
    assert len(index_zip(target, strict=True)) == 7
    assert not gap.exists()


@pytest.mark.parametrize('new_leaves', [4, 5])
def test_recapture_with_fewer_outer_entries_but_equal_or_more_leaves_proceeds(tmp_path, new_leaves):
    ariba_files.capture_files(FakeFileSource(['a.pdf','b.pdf','c.pdf','d.pdf']),
                              '5713434353', tmp_path)
    inner = tmp_path / 'drawings.zip'
    with zipfile.ZipFile(inner, 'w') as z:
        for i in range(new_leaves):
            z.writestr(f'drawing-{i}.pdf', str(i))
    result = ariba_files.capture_files(
        FakeFileSource(['drawings.zip'], contents={'drawings.zip': inner.read_bytes()}),
        '5713434353', tmp_path)
    assert result == tmp_path / 'Doc5713434353.zip'
    with zipfile.ZipFile(result) as z:
        assert set(z.namelist()) == {'a.pdf','b.pdf','c.pdf','d.pdf','drawings.zip'}


def test_preservation_guard_keeps_partials_on_failure(tmp_path, monkeypatch):
    ariba_files.capture_files(
        FakeFileSource(["a.pdf", "b.pdf", "c.pdf"]), "5713434353", tmp_path
    )

    from toronto_bids.sources import ariba_versions
    def fail(*args):
        raise ValueError("candidate failed byte preservation")
    monkeypatch.setattr(ariba_versions, 'verify_preserved', fail)
    with pytest.raises(ValueError, match="byte preservation"):
        ariba_files.capture_files(FakeFileSource(["a.pdf"]), "5713434353", tmp_path)

    pdir = ariba_files.partial_dir(tmp_path, "5713434353")
    assert pdir.exists(), "partials should be kept for resume on the next run"


@pytest.mark.parametrize('limit', ['depth', 'entries'])
def test_strict_leaf_count_refuses_an_incomplete_traversal(tmp_path, limit):
    from toronto_bids.sources.zip_index import index_zip, ZipIndexLimitError
    inner = tmp_path / 'inner.zip'
    with zipfile.ZipFile(inner, 'w') as z:
        z.writestr('one.pdf', b'one')
        z.writestr('two.pdf', b'two')
    outer = tmp_path / 'outer.zip'
    with zipfile.ZipFile(outer, 'w') as z:
        z.writestr('inner.zip', inner.read_bytes())
    kwargs = {'max_depth': 0} if limit == 'depth' else {'max_entries': 1}
    assert len(index_zip(outer, **kwargs)) == 1
    with pytest.raises(ZipIndexLimitError):
        index_zip(outer, strict=True, **kwargs)


def test_closed_salvage_cannot_shrink_an_existing_archive(tmp_path):
    doc = '5713434353'
    target = ariba_files.capture_files(FakeFileSource(['a.pdf','b.pdf','c.pdf']),doc,tmp_path)
    before = target.read_bytes()
    source = FakeFileSource(['x.pdf'])
    pdir = ariba_files.partial_dir(tmp_path,doc)
    (pdir / 'files').mkdir(parents=True)
    (pdir / 'files' / 'x.pdf').write_bytes(b'x')
    ariba_files.write_manifest(pdir,ariba_files.make_fingerprint(source.list_files()))
    assert ariba_files.finalise_partial(doc,tmp_path,posting_open=False) is None
    assert target.read_bytes() == before
    assert pdir.exists()
