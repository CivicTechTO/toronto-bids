import hashlib
import io
import json
import sqlite3
import zipfile

import pytest

from toronto_bids import cli, config
from toronto_bids.models import AribaAttachment
from toronto_bids.sources import ariba_attachments as aa
from toronto_bids.sources import ariba_files, ariba_versions
from toronto_bids.sources.zip_index import ZipIndexLimitError, index_zip
from toronto_bids.store import db as _db

DOC = "5713434353"
_TODAY = "2026-10-01"
_LINK = "https://service.ariba.com/Discovery.aw/ad/profile?#/RfxEvent/preview/{}"


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _make_zip(path, files: dict):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _provenance(path):
    with zipfile.ZipFile(path) as archive:
        return ariba_versions.read_provenance(archive)[0]


def _spine(conn, doc, deadline, rfx):
    conn.execute(
        "INSERT INTO solicitation (document_number, submission_deadline, ariba_posting_link) "
        "VALUES (?, ?, ?)",
        (doc, deadline, _LINK.format(rfx) if rfx else None),
    )


def _posting(conn, rfx, doc, close_date):
    conn.execute(
        "INSERT INTO ariba_posting (rfx_id, document_number, close_date) VALUES (?, ?, ?)",
        (rfx, doc, close_date),
    )


class FakeFileSource:
    def __init__(self, names, fail_on=(), contents=None, keys=None, kill_on=(), append=False):
        self.names = list(names)
        self.keys = list(keys) if keys is not None else self._default_keys(self.names)
        self.fail_on = set(fail_on)
        self.kill_on = set(kill_on)
        self.contents = contents or {}
        self.append = append
        self.downloaded = []

    @staticmethod
    def _default_keys(names):
        counts = {}
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


# --- Naming, document classification, anchor identity, and listing helpers ---


@pytest.mark.parametrize(
    "names,expected",
    [
        (["a.pdf", "b.pdf"], ["a.pdf", "b.pdf"]),
        (["a.pdf", "a.pdf"], ["a.pdf", "a_2.pdf"]),
        (["a.pdf"] * 3, ["a.pdf", "a_2.pdf", "a_3.pdf"]),
        (["README", "README"], ["README", "README_2"]),
        (["a.pdf", "a_2.pdf", "a.pdf"], ["a.pdf", "a_2.pdf", "a_3.pdf"]),
        (["x.pdf", "y.pdf", "x.pdf"], ["x.pdf", "y.pdf", "x_2.pdf"]),
    ],
)
def test_unique_names(names, expected):
    assert ariba_files.unique_names(names) == expected
    assert ariba_files.unique_names(names) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Addendum 1.pdf", True),
        ("Appendix C2 - Drawings.pdf (1.2 MB)", True),
        ("site.dwg   4,102 KB", True),
        ("PLAN.DWG", True),
        ("plan.dwf", True),
        ("prices.xlsm", True),
        ("scan.tif", True),
        ("scan.tiff", True),
        ("notice.msg", True),
        ("bundle.7z", True),
        ("dump.gz", True),
        ("site.kmz", True),
        ("a.zip", True),
        ("a.docx", True),
        ("a.xlsx", True),
        ("a.rtf", True),
        ("a.txt", True),
        ("a.jpeg", True),
        ("a.png", True),
        ("a.csv", True),
        ("a.pptx", True),
        ("References", False),
        ("Download this attachment", False),
        ("", False),
        ("   ", False),
        ("Section 3.1", False),
        ("https://example.org/a.pdf", False),
        ("http://example.org/a.pdf", False),
        ("www.example.org/a.pdf", False),
        ("notes.pdfx", False),
    ],
)
def test_is_document_name(name, expected):
    assert ariba_files.is_document_name(name) is expected


@pytest.mark.parametrize(
    "row,expected",
    [
        ("3.1 Drawings Package plan.dwg 787.7 MB", True),
        ("Addendum 1.pdf 2.1 MB", False),
        (None, False),
        ("", False),
    ],
)
def test_is_outline_row(row, expected):
    assert ariba_files.is_outline_row(row) is expected


@pytest.mark.parametrize(
    "first,second,same",
    [
        (
            {"row": "3.1  Part\xa0A", "name": "a.pdf", "ordinal": 0},
            {"row": "3.1 Part A", "name": "a.pdf", "ordinal": 0},
            True,
        ),
        (
            {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 0},
            {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 1},
            False,
        ),
        (
            {"row": "3.1 Part A", "name": "report.pdf", "ordinal": 0},
            {"row": "4.2 Part B", "name": "report.pdf", "ordinal": 0},
            False,
        ),
        (
            {
                "row": "Reference Documents Attachment 1 Manual.zip Attachment 2 Arborist Report.zip",
                "name": "Attachment 2 Arborist Report.zip",
                "ordinal": 0,
            },
            {"row": "3.1 Specifications", "name": "Attachment 2 Arborist Report.zip", "ordinal": 1},
            False,
        ),
    ],
)
def test_anchor_key_identity(first, second, same):
    assert (ariba_files.anchor_key(first) == ariba_files.anchor_key(second)) is same


def test_anchor_key_prefixes_outline_or_uses_row_text():
    outline = ariba_files.anchor_key(
        {"row": "3.1 Drawings Package plan.dwg 787.7 MB", "name": "plan.dwg", "ordinal": 0}
    )
    plain = ariba_files.anchor_key(
        {"row": "Addendum 1.pdf 2.1 MB", "name": "Addendum 1.pdf", "ordinal": 0}
    )
    assert outline.startswith("3.1#")
    assert "Addendum 1.pdf 2.1 MB" in plain and not plain.startswith("#")


@pytest.mark.parametrize(
    "anchor,expected",
    [
        ({"role": "menuitem"}, ariba_files.MENU_ITEM),
        ({"bh": "PMI"}, ariba_files.MENU_ITEM),
        ({"cls": "w-pmi-item rr"}, ariba_files.MENU_ITEM),
        ({"bh": "PML", "cls": "leg-p-r-9"}, ariba_files.TREE_LINK),
        ({}, ariba_files.TREE_LINK),
    ],
)
def test_anchor_kind(anchor, expected):
    assert ariba_files.anchor_kind(anchor) == expected


@pytest.mark.parametrize(
    "anchors,name,claimed,expected_id",
    [
        ([{"name": "a.pdf", "id": "_1"}, {"name": "b.pdf", "id": "_2"}], "b.pdf", set(), "_2"),
        ([{"name": "report.pdf", "id": "_1"}, {"name": "report.pdf", "id": "_2"}], "report.pdf", {"_1"}, "_2"),
        ([{"name": "report.pdf", "id": "_1"}], "report.pdf", {"_1"}, None),
        ([{"name": "a.pdf", "id": "_1"}], "z.pdf", set(), None),
        ([{"name": "Attachment\xa01  Report.zip", "id": "_1"}], "Attachment 1 Report.zip", set(), "_1"),
        ([{"name": "a.pdf", "id": ""}], "a.pdf", {""}, ""),
    ],
)
def test_pick_unclaimed(anchors, name, claimed, expected_id):
    hit = ariba_files.pick_unclaimed(anchors, name, claimed)
    if expected_id is None:
        assert hit is None
    else:
        assert hit is not None and hit["id"] == expected_id


@pytest.mark.parametrize(
    "anchors,expected_names",
    [
        (
            [
                {"row": "4.10 Late", "name": "j.pdf", "ordinal": 0},
                {"row": "4.9 Early", "name": "i.pdf", "ordinal": 0},
            ],
            ["i.pdf", "j.pdf"],
        ),
        (
            [
                {"row": "3.1 Parts", "name": "b.pdf", "ordinal": 1},
                {"row": "3.1 Parts", "name": "a.pdf", "ordinal": 0},
            ],
            ["a.pdf", "b.pdf"],
        ),
        (
            [
                {"row": "Addendum 2.pdf 1 MB", "name": "Addendum 2.pdf", "ordinal": 0},
                {"row": "3.1 Parts", "name": "a.pdf", "ordinal": 0},
                {"row": "Addendum 1.pdf 1 MB", "name": "Addendum 1.pdf", "ordinal": 0},
            ],
            ["a.pdf", "Addendum 1.pdf", "Addendum 2.pdf"],
        ),
    ],
)
def test_order_listing(anchors, expected_names):
    forward = ariba_files.order_listing(ariba_files.listing_from_anchors(anchors)["files"])
    reverse = ariba_files.order_listing(ariba_files.listing_from_anchors(list(reversed(anchors)))["files"])
    assert [f["name"] for f in forward] == expected_names
    assert forward == reverse


def test_listing_from_anchors_captures_handles_kinds_ordinals_rejections_and_collisions():
    assert ariba_files.listing_from_anchors([]) == {"files": [], "rejected": [], "collided": []}

    result = ariba_files.listing_from_anchors(
        [
            {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 0, "id": "_1", "bh": "PML"},
            {"row": "3.1 Parts", "name": "report.pdf", "ordinal": 2, "role": "menuitem"},
            {"row": "3.1 Parts", "name": "References", "ordinal": 1},
            {"row": "Attachments a.pdf", "name": "a.pdf", "ordinal": 0},
            {"row": "Attachments a.pdf", "name": "a.pdf", "ordinal": 0},
        ]
    )
    files = result["files"]
    assert [f["name"] for f in files] == ["report.pdf", "report.pdf", "a.pdf"]
    assert files[0]["key"] != files[1]["key"]
    assert [f["handle"] for f in files] == ["_1", "", ""]
    assert [f["kind"] for f in files[:2]] == [ariba_files.TREE_LINK, ariba_files.MENU_ITEM]
    assert files[1]["ordinal"] == 2
    assert result["rejected"] == ["References"]
    assert result["collided"] == [{"key": files[2]["key"], "name": "a.pdf"}]


@pytest.mark.parametrize(
    "first,second,equal",
    [
        (
            [{"key": "X", "name": "report.pdf"}, {"key": "Y", "name": "report.pdf"}],
            [{"key": "Y", "name": "report.pdf"}, {"key": "X", "name": "report.pdf"}],
            False,
        ),
        (
            [{"key": "X", "name": "report.pdf"}],
            [{"key": "Z", "name": "report.pdf"}],
            False,
        ),
        (
            ariba_files.listing_from_anchors([{"name": "a.pdf", "row": "1.1 x", "ordinal": 0, "id": "_1"}])["files"],
            ariba_files.listing_from_anchors([{"name": "a.pdf", "row": "1.1 x", "ordinal": 0, "id": "_2"}])["files"],
            True,
        ),
    ],
)
def test_make_fingerprint_sensitivity(first, second, equal):
    assert (ariba_files.make_fingerprint(first) == ariba_files.make_fingerprint(second)) is equal


def test_fingerprint_json_round_trip_and_non_positional_keys(tmp_path):
    anchors = [
        {"row": "3.1 Part A", "name": "report.pdf", "ordinal": 0},
        {"row": "4.2 Part B", "name": "report.pdf", "ordinal": 0},
    ]
    forward = ariba_files.listing_from_anchors(anchors)["files"]
    reverse = ariba_files.listing_from_anchors(list(reversed(anchors)))["files"]
    assert {f["key"] for f in forward} == {f["key"] for f in reverse}
    assert ariba_files.make_fingerprint(forward) != ariba_files.make_fingerprint(reverse)

    fp = ariba_files.make_fingerprint(forward)
    ariba_files.write_manifest(tmp_path, fp)
    assert ariba_files.read_manifest(tmp_path)["fingerprint"] == fp


@pytest.mark.parametrize(
    "omitted,collided,should_write",
    [
        ([], 0, False),
        ([], 1, True),
        (["bad.pdf"], 0, True),
    ],
)
def test_write_and_clear_omitted(tmp_path, omitted, collided, should_write):
    bundle = tmp_path / "Doc1.zip"
    gap = bundle.with_suffix(".omitted.json")
    path = ariba_files.write_omitted(bundle, omitted, collided=collided)
    if should_write:
        assert path == gap and gap.exists()
        body = json.loads(gap.read_text())
        assert body["omitted"] == omitted
        assert body.get("collided", 0) == collided
        ariba_files.clear_omitted_when_complete(bundle, omitted, collided=collided)
        assert gap.exists()
        ariba_files.clear_omitted_when_complete(bundle, [], collided=0)
        assert not gap.exists()
    else:
        assert path is None and not gap.exists()


# --- Bundle creation, capture_files, resume, and finalise_partial ---


def test_build_bundle_writes_flat_overwrites_and_is_atomic(tmp_path):
    a = _write(tmp_path / "src" / "a.pdf", b"aaa")
    b = _write(tmp_path / "src" / "b.pdf", b"bb")
    target = tmp_path / "Doc1.zip"
    target.write_bytes(b"stale")

    assert ariba_files.build_bundle([(a, "a.pdf"), (b, "b_2.pdf")], target) == target
    with zipfile.ZipFile(target) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "b_2.pdf"]
        assert zf.read("a.pdf") == b"aaa"

    missing_target = tmp_path / "Doc2.zip"
    with pytest.raises(OSError):
        ariba_files.build_bundle([(a, "a.pdf"), (tmp_path / "gone.pdf", "gone.pdf")], missing_target)
    assert not missing_target.exists()
    assert not missing_target.with_suffix(".zip.tmp").exists()


def test_capture_files_clean_run_and_duplicate_names(tmp_path):
    source = FakeFileSource(["dup.pdf", "dup.pdf", "b.pdf"], contents={"dup.pdf": b"first"})
    bundle = ariba_files.capture_files(source, DOC, tmp_path)
    assert bundle == tmp_path / f"Doc{DOC}.zip"
    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["b.pdf", "dup.pdf", "dup_2.pdf"]
    assert not ariba_files.partial_dir(tmp_path, DOC).exists()
    assert not (tmp_path / f"Doc{DOC}.omitted.json").exists()


def test_capture_files_zero_files_raises(tmp_path):
    with pytest.raises(RuntimeError, match="no files"):
        ariba_files.capture_files(FakeFileSource([]), DOC, tmp_path)
    assert not (tmp_path / f"Doc{DOC}.zip").exists()


def test_capture_files_partial_failure_records_gap_and_continues(tmp_path):
    source = FakeFileSource(["a.pdf", "bad.pdf", "c.pdf"], fail_on=["bad.pdf"])
    bundle = ariba_files.capture_files(source, DOC, tmp_path)
    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["a.pdf", "c.pdf"]
    body = json.loads((tmp_path / f"Doc{DOC}.omitted.json").read_text())
    assert body["omitted"] == ["bad.pdf"]
    assert body.get("collided", 0) == 0


def test_capture_files_all_fail_keeps_partials_without_part_files(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"], fail_on=["a.pdf", "b.pdf"])
    assert ariba_files.capture_files(source, DOC, tmp_path) is None
    assert not (tmp_path / f"Doc{DOC}.zip").exists()
    assert (ariba_files.partial_dir(tmp_path, DOC) / "files").is_dir()
    assert list(tmp_path.rglob("*.part")) == []


def test_capture_files_below_threshold_stays_pending(tmp_path):
    source = FakeFileSource(
        ["a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf"],
        fail_on=["b.pdf", "c.pdf", "d.pdf", "e.pdf"],
    )
    messages = []
    assert ariba_files.capture_files(source, DOC, tmp_path, log=messages.append) is None
    assert not (tmp_path / f"Doc{DOC}.zip").exists()
    assert not (tmp_path / f"Doc{DOC}.omitted.json").exists()
    assert (ariba_files.partial_dir(tmp_path, DOC) / "files" / "a.pdf").is_file()
    assert any("1 of 5" in m for m in messages)


def test_capture_files_resumes_completed_files_and_refetches_leftover_part(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"], contents={"b.pdf": b"WHOLE"}, append=True)
    pdir = ariba_files.partial_dir(tmp_path, DOC)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"already here")
    (pdir / "files" / "b.pdf.part").write_bytes(b"HALF")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files()))

    bundle = ariba_files.capture_files(source, DOC, tmp_path)
    assert source.downloaded == ["b.pdf"]
    with zipfile.ZipFile(bundle) as zf:
        assert zf.read("a.pdf") == b"already here"
        assert zf.read("b.pdf") == b"WHOLE"
    assert not (tmp_path / f"Doc{DOC}.omitted.json").exists()


def test_capture_files_changed_event_preserves_stale_partials_without_reusing(tmp_path):
    source = FakeFileSource(["a.pdf", "b.pdf"], contents={"a.pdf": b"FRESH"})
    stale = FakeFileSource(["a.pdf"])
    pdir = ariba_files.partial_dir(tmp_path, DOC)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"STALE")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(stale.list_files()))

    bundle = ariba_files.capture_files(source, DOC, tmp_path)
    with zipfile.ZipFile(bundle) as zf:
        assert zf.read("a.pdf") == b"FRESH"
        assert any(zf.read(name) == b"STALE" for name in zf.namelist())


def test_capture_files_reordered_duplicate_keys_do_not_reuse_partials(tmp_path):
    contents = {"X": b"DOC-X", "Y": b"DOC-Y"}
    run1 = FakeFileSource(["report.pdf", "report.pdf"], keys=["X", "Y"], contents=contents, kill_on=["Y"])
    with pytest.raises(KeyboardInterrupt):
        ariba_files.capture_files(run1, DOC, tmp_path)
    assert list(tmp_path.rglob("*.part")) == []
    assert not (tmp_path / f"Doc{DOC}.zip").exists()
    assert (ariba_files.partial_dir(tmp_path, DOC) / "files" / "report.pdf").exists()

    run2 = FakeFileSource(["report.pdf", "report.pdf"], keys=["Y", "X"], contents=contents)
    bundle = ariba_files.capture_files(run2, DOC, tmp_path)
    with zipfile.ZipFile(bundle) as zf:
        assert zf.read("report.pdf") == b"DOC-Y"
        assert zf.read("report_2.pdf") == b"DOC-X"
        assert {zf.read(n) for n in zf.namelist()} == {b"DOC-X", b"DOC-Y"}
    assert not (tmp_path / f"Doc{DOC}.omitted.json").exists()


@pytest.mark.parametrize("bad_manifest", ["{not valid json", json.dumps({"fingerprint": {"files": [17]}})])
def test_corrupt_or_wrong_shape_manifest_retires_partials_without_adopting(tmp_path, bad_manifest):
    pdir = ariba_files.partial_dir(tmp_path, DOC)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"unverified prior bytes")
    (pdir / ariba_files.MANIFEST_NAME).write_text(bad_manifest)

    source = FakeFileSource(["a.pdf"], contents={"a.pdf": b"fresh"})
    target = ariba_files.capture_files(source, DOC, tmp_path)
    assert source.downloaded == ["a.pdf"]
    with zipfile.ZipFile(target) as zf:
        assert zf.read("a.pdf") == b"fresh"
        assert any(zf.read(n) == b"unverified prior bytes" for n in zf.namelist())
    retired = next(r for r in _provenance(target)["files"] if r.get("origin") == "retired_partial")
    assert retired["source_keys"] == []


def test_gap_record_written_before_bundle_and_cleared_only_after_success(tmp_path, monkeypatch):
    source = FakeFileSource(["a.pdf", "bad.pdf"], fail_on=["bad.pdf"])
    replace = ariba_files.os.replace

    def fail_zip_replace(src, dst):
        if str(dst).endswith(f"Doc{DOC}.zip"):
            assert (tmp_path / f"Doc{DOC}.omitted.json").exists()
            raise OSError("ENOSPC")
        return replace(src, dst)

    monkeypatch.setattr(ariba_files.os, "replace", fail_zip_replace)
    with pytest.raises(OSError, match="ENOSPC"):
        ariba_files.capture_files(source, DOC, tmp_path)
    assert json.loads((tmp_path / f"Doc{DOC}.omitted.json").read_text())["omitted"] == ["bad.pdf"]
    assert not (tmp_path / f"Doc{DOC}.zip").exists()

    monkeypatch.setattr(ariba_files.os, "replace", replace)
    ariba_files.capture_files(FakeFileSource(["a.pdf", "bad.pdf"]), DOC, tmp_path)
    assert not (tmp_path / f"Doc{DOC}.omitted.json").exists()


def test_stale_gap_survives_failed_bundle_write(tmp_path, monkeypatch):
    stale = tmp_path / f"Doc{DOC}.omitted.json"
    stale.write_text(json.dumps({"omitted": ["gone.pdf"]}))
    monkeypatch.setattr(ariba_files, "build_bundle", lambda *a, **k: (_ for _ in ()).throw(OSError("ENOSPC")))

    with pytest.raises(OSError, match="ENOSPC"):
        ariba_files.capture_files(FakeFileSource(["a.pdf"]), DOC, tmp_path)
    assert stale.exists()
    assert not (tmp_path / f"Doc{DOC}.zip").exists()


def test_zero_byte_download_is_omitted_and_duplicate_uses_zip_name(tmp_path):
    source = FakeFileSource(
        ["report.pdf", "report.pdf", "empty.pdf", "keep.pdf"],
        keys=["A", "B", "C", "D"],
        fail_on=["B"],
        contents={"empty.pdf": b""},
    )
    bundle = ariba_files.capture_files(source, DOC, tmp_path)
    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["keep.pdf", "report.pdf"]
    body = json.loads((tmp_path / f"Doc{DOC}.omitted.json").read_text())
    assert body["omitted"] == ["report_2.pdf", "empty.pdf"]
    assert list(tmp_path.rglob("*.part")) == []


def test_capture_files_folds_source_collided_count(tmp_path):
    class SourceWithCollisions(FakeFileSource):
        def collided_count(self):
            return 2

    ariba_files.capture_files(SourceWithCollisions(["a.pdf"]), DOC, tmp_path)
    assert json.loads((tmp_path / f"Doc{DOC}.omitted.json").read_text())["collided"] == 2


def test_finalise_partial_guards_open_and_empty(tmp_path):
    ariba_files.capture_files(FakeFileSource(["a.pdf", "b.pdf"], fail_on=["a.pdf", "b.pdf"]), DOC, tmp_path)
    with pytest.raises(ValueError, match="CLOSED"):
        ariba_files.finalise_partial(DOC, tmp_path, posting_open=True)
    assert ariba_files.finalise_partial("9999999999", tmp_path, posting_open=False) is None
    assert not (tmp_path / f"Doc{DOC}.zip").exists()


def test_finalise_partial_bundles_disk_records_omissions_and_preserves_parts(tmp_path):
    source = FakeFileSource(["report.pdf", "report.pdf", "c.pdf"], keys=["A", "B", "C"])
    pdir = ariba_files.partial_dir(tmp_path, DOC)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "report.pdf").write_bytes(b"AAA")
    (pdir / "files" / "c.pdf.part").write_bytes(b"HALF")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(source.list_files()))

    bundle = ariba_files.finalise_partial(DOC, tmp_path, posting_open=False)
    assert bundle == tmp_path / f"Doc{DOC}.zip"
    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["report.pdf"]
    assert json.loads((tmp_path / f"Doc{DOC}.omitted.json").read_text())["omitted"] == ["report_2.pdf", "c.pdf"]
    assert (pdir / "files" / "c.pdf.part").exists()


def test_finalise_partial_cleans_up_without_parts_and_salvages_lost_manifest(tmp_path):
    pdir = ariba_files.partial_dir(tmp_path, DOC)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    (pdir / ariba_files.MANIFEST_NAME).write_text("not json")

    bundle = ariba_files.finalise_partial(DOC, tmp_path, posting_open=False)
    with zipfile.ZipFile(bundle) as zf:
        assert zf.namelist() == ["a.pdf"]
    assert not (tmp_path / f"Doc{DOC}.omitted.json").exists()
    assert not pdir.exists()


def test_finalise_partial_keeps_stale_gap_on_build_failure_and_refuses_to_shrink(tmp_path, monkeypatch):
    stale = tmp_path / f"Doc{DOC}.omitted.json"
    stale.write_text(json.dumps({"omitted": ["gone.pdf"]}))
    pdir = ariba_files.partial_dir(tmp_path, DOC)
    (pdir / "files").mkdir(parents=True)
    (pdir / "files" / "a.pdf").write_bytes(b"aaa")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(FakeFileSource(["a.pdf"]).list_files()))

    real_build = ariba_files.build_bundle
    monkeypatch.setattr(ariba_files, "build_bundle", lambda *a, **k: (_ for _ in ()).throw(OSError("ENOSPC")))
    with pytest.raises(OSError, match="ENOSPC"):
        ariba_files.finalise_partial(DOC, tmp_path, posting_open=False)
    assert stale.exists()

    monkeypatch.setattr(ariba_files, "build_bundle", real_build)
    stale.unlink()
    target = ariba_files.capture_files(FakeFileSource(["a.pdf", "b.pdf", "c.pdf"]), DOC, tmp_path)
    before = target.read_bytes()
    (pdir / "files").mkdir(parents=True, exist_ok=True)
    (pdir / "files" / "x.pdf").write_bytes(b"x")
    ariba_files.write_manifest(pdir, ariba_files.make_fingerprint(FakeFileSource(["x.pdf"]).list_files()))
    assert ariba_files.finalise_partial(DOC, tmp_path, posting_open=False) is None
    assert target.read_bytes() == before
    assert pdir.exists()


# --- Versioned amendments and provenance ---


def test_amended_retry_preserves_old_files_and_resolves_withdrawn_gap(tmp_path):
    doc = "5861259648"
    target = ariba_files.capture_files(
        FakeFileSource(
            ["drawings.pdf", "old agreement.pdf"],
            keys=["old", "agreement"],
            contents={"old": b"old drawings", "agreement": b"agreement"},
            fail_on=["agreement"],
        ),
        doc,
        tmp_path,
    )
    assert target
    result = ariba_files.capture_files(
        FakeFileSource(
            ["revised drawings.pdf", "new agreement.pdf"],
            keys=["new", "agreement2"],
            contents={"new": b"new drawings", "agreement2": b"agreement"},
        ),
        doc,
        tmp_path,
    )
    assert result == target
    with zipfile.ZipFile(target) as zf:
        assert zf.read("drawings.pdf") == b"old drawings"
        assert zf.read("revised drawings.pdf") == b"new drawings"
        assert zf.read("new agreement.pdf") == b"agreement"
    assert not target.with_suffix(".omitted.json").exists()


def test_recapture_with_fewer_files_and_nested_leaves_preserves_history(tmp_path):
    inner = _zip_bytes({"nested_a.pdf": b"aaa", "nested_b.pdf": b"bbb"})
    target = ariba_files.capture_files(
        FakeFileSource(["a.pdf", "b.pdf", "drawings.zip"], contents={"drawings.zip": inner}),
        DOC,
        tmp_path,
    )
    gap = target.with_suffix(".omitted.json")
    gap.write_text('{"omitted": ["missing.pdf"]}')

    result = ariba_files.capture_files(FakeFileSource(["x.pdf", "y.pdf"]), DOC, tmp_path)
    assert result == target
    with zipfile.ZipFile(target) as zf:
        assert zf.read("drawings.zip") == inner
        assert zf.read("a.pdf") == b"a.pdf"
    assert len(index_zip(target, strict=True)) == 6
    assert not gap.exists()


@pytest.mark.parametrize("new_leaves", [4, 5])
def test_recapture_with_fewer_outer_entries_but_more_leaves_proceeds(tmp_path, new_leaves):
    ariba_files.capture_files(FakeFileSource(["a.pdf", "b.pdf", "c.pdf", "d.pdf"]), DOC, tmp_path)
    inner = _zip_bytes({f"drawing-{i}.pdf": str(i).encode() for i in range(new_leaves)})
    result = ariba_files.capture_files(
        FakeFileSource(["drawings.zip"], contents={"drawings.zip": inner}), DOC, tmp_path
    )
    assert result == tmp_path / f"Doc{DOC}.zip"
    with zipfile.ZipFile(result) as zf:
        assert set(zf.namelist()) == {"a.pdf", "b.pdf", "c.pdf", "d.pdf", "drawings.zip"}


def test_revision_provenance_idempotency_revert_and_indexing(tmp_path, conn):
    doc = "5861259648"
    target = ariba_files.capture_files(
        FakeFileSource(["agreement.pdf"], keys=["a"], contents={"a": b"old"}), doc, tmp_path
    )
    revised = FakeFileSource(
        ["agreement.pdf", "agreement.pdf"], keys=["a", "b"], contents={"a": b"new", "b": b"other"}
    )
    assert ariba_files.capture_files(revised, doc, tmp_path) == target
    first = _provenance(target)
    assert len(first["files"]) == 3
    current = {r["key"]: r["path"] for r in first["listing"]}
    assert current["a"].startswith("_versions/") and current["b"] == "agreement_2.pdf"
    assert "agreement.pdf" not in current.values()
    assert first["document_number"] == doc

    assert ariba_files.capture_files(revised, doc, tmp_path) == target
    second = _provenance(target)
    assert {r["path"]: r["sha256"] for r in first["files"]} == {
        r["path"]: r["sha256"] for r in second["files"]
    }
    assert aa.store_bundle(conn, target, doc, tmp_path) == 3
    assert conn.execute("SELECT count(*) FROM ariba_attachment").fetchone()[0] == 3

    ariba_files.capture_files(
        FakeFileSource(["agreement.pdf"], keys=["a"], contents={"a": b"old"}), doc, tmp_path
    )
    reverted = _provenance(target)
    assert len(reverted["files"]) == 3
    assert reverted["listing"][0]["path"] == "agreement.pdf"


def test_large_embedded_manifest_is_not_indexed_as_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr(ariba_versions, "COMMENT_LIMIT", 1)
    doc = "5861259648"
    target = ariba_files.capture_files(
        FakeFileSource(["a.pdf"], keys=["a"], contents={"a": b"actual attachment"}), doc, tmp_path
    )
    with zipfile.ZipFile(target) as zf:
        manifest, member = ariba_versions.read_provenance(zf)
        assert member and manifest["listing"][0]["key"] == "a"
    assert [r["path"] for r in index_zip(target, strict=True)] == ["a.pdf"]
    assert ariba_files.capture_files(
        FakeFileSource(["a.pdf"], keys=["a"], contents={"a": b"revised"}), doc, tmp_path
    )
    assert len(index_zip(target, strict=True)) == 2


def test_legacy_archive_retains_unknown_provenance(tmp_path):
    doc = "5861259648"
    target = _make_zip(tmp_path / f"Doc{doc}.zip", {"old.pdf": b"old"})
    ariba_files.capture_files(FakeFileSource(["new.pdf"], keys=["new"], contents={"new": b"new"}), doc, tmp_path)
    data = _provenance(target)
    old = next(r for r in data["files"] if r["path"] == "old.pdf")
    assert old["first_observed"] is None and old["source_keys"] == []
    assert data["listing"][0]["path"] == "new.pdf"


def test_failed_atomic_swap_retains_zip_gap_and_partials(tmp_path, monkeypatch):
    doc = "5861259648"
    target = ariba_files.capture_files(
        FakeFileSource(["a.pdf", "b.pdf"], keys=["a", "b"], contents={"a": b"old", "b": b"b"}, fail_on=["b"]),
        doc,
        tmp_path,
    )
    before = target.read_bytes()
    gap = target.with_suffix(".omitted.json")
    old_gap = gap.read_bytes()
    replace = ariba_files.os.replace

    def fail(src, dst):
        if dst == target:
            raise OSError("disk replacement failed")
        return replace(src, dst)

    monkeypatch.setattr(ariba_files.os, "replace", fail)
    with pytest.raises(OSError, match="replacement failed"):
        ariba_files.capture_files(
            FakeFileSource(["a.pdf", "b.pdf"], keys=["a", "b"], contents={"a": b"new", "b": b"b"}),
            doc,
            tmp_path,
        )
    assert target.read_bytes() == before and gap.read_bytes() == old_gap
    partial = ariba_files.partial_dir(tmp_path, doc) / "files"
    assert (partial / "a.pdf").read_bytes() == b"new" and (partial / "b.pdf").read_bytes() == b"b"


def test_tampered_existing_provenance_and_preservation_guard_reject_replacement(tmp_path, monkeypatch):
    doc = "5861259648"
    target = ariba_files.capture_files(
        FakeFileSource(["a.pdf"], keys=["a"], contents={"a": b"old"}), doc, tmp_path
    )
    data = _provenance(target)
    data["files"][0]["sha256"] = "0" * 64
    ariba_versions.write_provenance(target, data)
    before = target.read_bytes()
    with pytest.raises(ValueError, match="hash disagrees"):
        ariba_files.capture_files(
            FakeFileSource(["a.pdf"], keys=["a"], contents={"a": b"new"}), doc, tmp_path
        )
    assert target.read_bytes() == before

    doc2 = "5861259649"
    ariba_files.capture_files(FakeFileSource(["a.pdf", "b.pdf", "c.pdf"]), doc2, tmp_path)
    monkeypatch.setattr(
        ariba_versions,
        "verify_preserved",
        lambda *a: (_ for _ in ()).throw(ValueError("candidate failed byte preservation")),
    )
    with pytest.raises(ValueError, match="byte preservation"):
        ariba_files.capture_files(FakeFileSource(["a.pdf"]), doc2, tmp_path)
    assert ariba_files.partial_dir(tmp_path, doc2).exists()


def test_current_gap_and_recorded_collision_keep_bundle_incomplete(tmp_path):
    doc = "5861259648"
    target = ariba_files.capture_files(
        FakeFileSource(["a.pdf"], keys=["a"], contents={"a": b"old"}), doc, tmp_path
    )
    ariba_files.capture_files(
        FakeFileSource(["a.pdf", "b.pdf"], keys=["a", "b"], contents={"a": b"new", "b": b"b"}, fail_on=["a"]),
        doc,
        tmp_path,
    )
    assert _provenance(target)["listing"][0]["path"] is None
    assert not aa.bundle_complete(tmp_path, doc)
    target.with_suffix(".omitted.json").unlink()
    assert not aa.bundle_complete(tmp_path, doc)

    doc2 = "5861259649"
    target2 = ariba_files.capture_files(FakeFileSource(["a.pdf"], keys=["a"]), doc2, tmp_path)
    ariba_files.write_omitted(target2, [], collided=1)
    ariba_files.capture_files(FakeFileSource(["b.pdf"], keys=["b"]), doc2, tmp_path)
    assert not aa.bundle_complete(tmp_path, doc2)
    assert ariba_files.read_omitted(target2)["collided"] == 1


def test_interrupted_old_listing_bytes_survive_amendment(tmp_path):
    doc = "5861259648"
    with pytest.raises(KeyboardInterrupt):
        ariba_files.capture_files(
            FakeFileSource(
                ["a.pdf", "b.pdf"],
                keys=["a", "b"],
                contents={"a": b"old partial", "b": b"b"},
                kill_on=["b"],
            ),
            doc,
            tmp_path,
        )
    target = ariba_files.capture_files(
        FakeFileSource(["new.pdf"], keys=["new"], contents={"new": b"new"}), doc, tmp_path
    )
    data = _provenance(target)
    assert len(data["listing"]) == 1 and data["listing"][0]["name"] == "new.pdf"
    with zipfile.ZipFile(target) as zf:
        assert any(zf.read(i) == b"old partial" for i in zf.namelist())
    historical = next(r for r in data["files"] if r.get("origin") == "retired_partial")
    assert historical["source_keys"] == ["a"]


# --- ariba_versions unit coverage gaps ---


@pytest.mark.parametrize("comment", [b"", b"plain text", json.dumps({"other": 1}).encode(), b"[1, 2]"])
def test_read_provenance_ignores_non_ariba_comments(tmp_path, comment):
    path = _make_zip(tmp_path / "foreign.zip", {"a.pdf": b"a"})
    with zipfile.ZipFile(path, "a") as zf:
        zf.comment = comment
    with zipfile.ZipFile(path) as zf:
        assert ariba_versions.read_provenance(zf) == (None, None)


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"format": ariba_versions.FORMAT, "version": 2, "files": [], "listing": []}, "Invalid Ariba version provenance"),
        ({"format": ariba_versions.FORMAT, "version": 1, "files": "bad", "listing": []}, "Invalid Ariba version provenance"),
        (
            {
                "format": ariba_versions.FORMAT,
                "version": 1,
                "files": [{"path": "a.pdf", "sha256": "not-a-sha"}],
                "listing": [],
            },
            "Invalid/duplicate Ariba version entry",
        ),
        (
            {
                "format": ariba_versions.FORMAT,
                "version": 1,
                "files": [{"path": "a.pdf", "sha256": hashlib.sha256(b"a").hexdigest()}],
                "listing": [{"key": "k", "name": "a.pdf", "archive_name": "a.pdf", "path": "missing.pdf"}],
            },
            "Invalid current Ariba listing provenance",
        ),
        (
            {
                "format": ariba_versions.FORMAT,
                "version": 1,
                "files": [{"path": "other.pdf", "sha256": hashlib.sha256(b"a").hexdigest()}],
                "listing": [],
            },
            "does not match archived files",
        ),
    ],
)
def test_read_provenance_rejects_malformed_manifests(tmp_path, payload, match):
    path = _make_zip(tmp_path / "bad.zip", {"a.pdf": b"a"})
    ariba_versions.write_provenance(path, payload)
    with zipfile.ZipFile(path) as zf:
        with pytest.raises(ValueError, match=match):
            ariba_versions.read_provenance(zf)


def test_verify_preserved_failure_cases(tmp_path):
    plain = _make_zip(tmp_path / "plain.zip", {"a.pdf": b"a"})
    with pytest.raises(ValueError, match="no version provenance"):
        ariba_versions.verify_preserved(plain, {})

    sha_a = hashlib.sha256(b"a").hexdigest()
    valid_manifest = {
        "format": ariba_versions.FORMAT,
        "version": 1,
        "document_number": DOC,
        "files": [{"path": "a.pdf", "sha256": sha_a}],
        "listing": [{"key": "a", "name": "a.pdf", "archive_name": "a.pdf", "path": "a.pdf"}],
    }
    lost = _make_zip(tmp_path / "lost.zip", {"a.pdf": b"a"})
    ariba_versions.write_provenance(lost, valid_manifest)
    with pytest.raises(ValueError, match="lose historical version"):
        ariba_versions.verify_preserved(lost, {"old.pdf": hashlib.sha256(b"old").hexdigest()})

    tampered = _make_zip(tmp_path / "tampered.zip", {"a.pdf": b"tampered"})
    ariba_versions.write_provenance(tampered, valid_manifest)
    with pytest.raises(ValueError, match="content hash mismatch"):
        ariba_versions.verify_preserved(tampered, {"a.pdf": sha_a})


def test_merge_capture_rejects_cross_event_provenance_and_duplicate_archive_names(tmp_path):
    source_zip = ariba_files.capture_files(FakeFileSource(["a.pdf"], keys=["a"]), "1111111111", tmp_path)
    renamed = tmp_path / "Doc2222222222.zip"
    renamed.write_bytes(source_zip.read_bytes())
    disk = _write(tmp_path / "stage" / "a.pdf", b"new")
    with pytest.raises(ValueError, match="different Ariba event"):
        with ariba_versions.merge_capture(
            [(disk, "a.pdf")], renamed, [{"key": "a", "name": "a.pdf", "archive_name": "a.pdf"}]
        ):
            pass

    dup_listing = [
        {"key": "k1", "name": "a.pdf", "archive_name": "a.pdf"},
        {"key": "k2", "name": "a.pdf", "archive_name": "a.pdf"},
    ]
    with pytest.raises(ValueError, match="archive names must be unique"):
        with ariba_versions.merge_capture([], tmp_path / "Doc3333333333.zip", dup_listing):
            pass


# --- ZIP indexing, storage, ingest, DB migration, and open/near-close selection ---


def test_index_zip_recurses_nested_zips_and_handles_corrupt_inner(tmp_path):
    inner = _zip_bytes({"drawings/site-plan.pdf": b"plan", "notes.txt": b"n"})
    z = _make_zip(
        tmp_path / "Doc1.zip",
        {
            "PART 1 - RFP.pdf": b"rfp",
            "Appendix C2 - Planning Documents.zip": inner,
            "broken.zip": b"not a valid zip file",
        },
    )
    by_path = {e["path"]: e for e in aa.index_zip(z)}
    assert set(by_path) == {
        "PART 1 - RFP.pdf",
        "Appendix C2 - Planning Documents.zip/drawings/site-plan.pdf",
        "Appendix C2 - Planning Documents.zip/notes.txt",
        "broken.zip",
    }
    nested = by_path["Appendix C2 - Planning Documents.zip/drawings/site-plan.pdf"]
    assert nested["filename"] == "drawings/site-plan.pdf"
    assert nested["file_size"] == len(b"plan")
    assert len(nested["crc32"]) == 8 and int(nested["crc32"], 16) == zipfile.crc32(b"plan")


def test_index_zip_caps_total_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(aa, "_MAX_ZIP_ENTRIES", 3)
    z = _make_zip(tmp_path / "Doc3.zip", {f"f{i}.pdf": b"x" for i in range(10)})
    assert len(aa.index_zip(z)) == 3


@pytest.mark.parametrize("limit", ["depth", "entries"])
def test_strict_leaf_count_refuses_incomplete_traversal(tmp_path, limit):
    inner = _zip_bytes({"one.pdf": b"one", "two.pdf": b"two"})
    outer = _make_zip(tmp_path / "outer.zip", {"inner.zip": inner})
    kwargs = {"max_depth": 0} if limit == "depth" else {"max_entries": 1}
    assert len(index_zip(outer, **kwargs)) == 1
    with pytest.raises(ZipIndexLimitError):
        index_zip(outer, strict=True, **kwargs)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Doc5660182540.zip", "5660182540"),
        ("DocBad.zip", None),
        ("2026-report.zip", None),
        ("random.zip", None),
    ],
)
def test_document_number_from_zip_name(name, expected):
    assert aa.document_number_from_zip_name(name) == expected


def test_bundle_complete_rejects_corrupt_zip_and_mismatched_provenance(tmp_path):
    (tmp_path / "Doc5660182540.zip").write_bytes(b"not a zip")
    assert not aa.bundle_complete(tmp_path, "5660182540")

    valid = ariba_files.capture_files(FakeFileSource(["a.pdf"]), "1111111111", tmp_path)
    (tmp_path / "Doc2222222222.zip").write_bytes(valid.read_bytes())
    assert aa.bundle_complete(tmp_path, "1111111111")
    assert not aa.bundle_complete(tmp_path, "2222222222")


@pytest.mark.parametrize("payload_size", [1, 2 << 20], ids=["small", "multi-buffer"])
def test_store_bundle_copies_indexes_and_rebuilds_stale_rows(conn, tmp_path, payload_size):
    inner = _zip_bytes({"a.pdf": b"a"})
    src = _make_zip(tmp_path / "Doc5660182540.zip", {"P1.pdf": b"p" * payload_size, "Appx.zip": inner})
    dest = tmp_path / "store"

    assert aa.store_bundle(conn, src, "5660182540", dest) == 2
    assert (dest / "Doc5660182540.zip").exists()
    rows = conn.execute(
        "SELECT filename, path, file_size, zip_name, zip_sha256 FROM ariba_attachment "
        "WHERE document_number='5660182540' ORDER BY path"
    ).fetchall()
    assert [r["path"] for r in rows] == ["Appx.zip/a.pdf", "P1.pdf"]
    assert rows[0]["zip_name"] == "Doc5660182540.zip"
    expected_digest = hashlib.sha256(src.read_bytes()).hexdigest()
    assert {r["zip_sha256"] for r in rows} == {expected_digest}

    conn.execute(
        "INSERT INTO ariba_attachment (document_number, filename, path) VALUES ('5660182540', 'Appx.zip', 'Appx.zip')"
    )
    conn.commit()
    aa.store_bundle(conn, src, "5660182540", dest)
    paths = {r["path"] for r in conn.execute("SELECT path FROM ariba_attachment WHERE document_number='5660182540'")}
    assert paths == {"P1.pdf", "Appx.zip/a.pdf"}


def _bundle_index(conn):
    return {
        (row["path"], row["zip_sha256"])
        for row in conn.execute("SELECT path, zip_sha256 FROM ariba_attachment")
    }


@pytest.mark.parametrize("damage", ["truncated", "bad-crc"])
def test_ingest_invalid_replacement_preserves_archive_and_index(conn, tmp_path, damage):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    dest = tmp_path / "archive"
    source = _make_zip(downloads / f"Doc{DOC}.zip", {"old.pdf": b"original"})
    installed = dest / source.name
    original = source.read_bytes()
    assert aa.ingest_downloads(conn, downloads, dest) == 1

    _make_zip(source, {"new.pdf": b"replacement"})
    replacement = source.read_bytes()
    damaged = replacement[:-22] if damage == "truncated" else replacement.replace(b"replacement", b"replacemenX")
    source.write_bytes(damaged)
    with pytest.raises(zipfile.BadZipFile):
        aa.ingest_downloads(conn, downloads, dest)
    assert installed.read_bytes() == original
    assert _bundle_index(conn) == {("old.pdf", hashlib.sha256(original).hexdigest())}

    source.write_bytes(replacement)
    assert aa.ingest_downloads(conn, downloads, dest) == 1
    assert installed.read_bytes() == replacement
    assert _bundle_index(conn) == {("new.pdf", hashlib.sha256(replacement).hexdigest())}


def test_reindex_recovers_after_interruption_between_install_and_commit(conn, tmp_path):
    source = _make_zip(tmp_path / f"Doc{DOC}.zip", {"old.pdf": b"original"})
    dest = tmp_path / "archive"
    aa.store_bundle(conn, source, DOC, dest)
    previous_index = _bundle_index(conn)
    _make_zip(source, {"new.pdf": b"replacement"})
    replacement = source.read_bytes()

    def interrupt_commit(action, operation, *_):
        if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(interrupt_commit)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            aa.store_bundle(conn, source, DOC, dest)
    finally:
        conn.set_authorizer(None)

    assert not conn.in_transaction
    assert _bundle_index(conn) == previous_index
    assert (dest / source.name).read_bytes() == replacement
    assert aa.reindex_bundles(conn, dest) == 1
    assert _bundle_index(conn) == {("new.pdf", hashlib.sha256(replacement).hexdigest())}


def test_ingest_downloads_skips_invalid_names_and_is_idempotent(conn, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _make_zip(downloads / "Doc5660182540.zip", {"A.pdf": b"a"})
    _make_zip(downloads / "DocBad.zip", {"B.pdf": b"b"})
    _make_zip(downloads / "not-a-doc.zip", {"X.pdf": b"x"})
    dest = tmp_path / "store"
    logs = []

    assert aa.ingest_downloads(conn, downloads, dest, log=logs.append) == 1
    assert any("skipped DocBad.zip" in m for m in logs)
    assert aa.ingest_downloads(conn, downloads, dest) == 1
    assert conn.execute("SELECT COUNT(*) FROM ariba_attachment").fetchone()[0] == 1


def test_reindex_bundles_and_cli_reindex(conn, tmp_path, monkeypatch, capsys):
    dest = tmp_path / "store"
    dest.mkdir()
    _make_zip(dest / "Doc1234567891.zip", {"only.pdf": b"x"})
    _make_zip(dest / "Doc1234567892.zip", {"a.pdf": b"a", "b.pdf": b"b"})

    assert aa.reindex_bundles(conn, dest) == 2
    assert conn.execute("SELECT COUNT(*) FROM ariba_attachment").fetchone()[0] == 3

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "bids.sqlite")
    monkeypatch.setattr(config, "ARIBA_ATTACHMENTS_DIR", dest)
    assert cli.main(["enrich-ariba-attachments", "--reindex"]) == 0
    assert "Doc1234567891.zip" in capsys.readouterr().out


def test_init_db_migrates_old_unique_index_to_path():
    conn = _db.connect(":memory:")
    conn.executescript(
        "CREATE TABLE ariba_attachment (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "document_number TEXT NOT NULL, filename TEXT NOT NULL, file_size INTEGER, "
        "crc32 TEXT, zip_name TEXT, zip_sha256 TEXT, "
        "first_seen TEXT NOT NULL DEFAULT (datetime('now')), "
        "last_seen TEXT NOT NULL DEFAULT (datetime('now')), "
        "UNIQUE (document_number, filename));"
    )
    conn.execute("INSERT INTO ariba_attachment (document_number, filename) VALUES ('1','a.zip')")
    conn.commit()

    _db.init_db(conn)

    cols = [r[1] for r in conn.execute("PRAGMA table_info(ariba_attachment)")]
    assert "path" in cols
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='ariba_attachment'").fetchone()[0]
    assert "document_number, path" in sql
    assert "document_number, filename" not in sql
    for p in ("x.zip/a.pdf", "y.zip/a.pdf"):
        _db.upsert_row(conn, AribaAttachment(document_number="1", filename="a.pdf", path=p), overwrite=True)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM ariba_attachment WHERE document_number='1' AND filename='a.pdf'"
        ).fetchone()[0]
        == 2
    )


def test_training_postings_stay_eligible_for_source_verification(conn):
    rows = [
        ("4044346425", "Mock RFT Training and Practice", "2099-12-31", "1110088888"),
        ("3949149442", "Mock RFQ Training and Practice", "2099-12-31", "1110077777"),
        ("5660182540", "Mockingbird Park Playground Renewal", "2099-01-01", "1110066666"),
        ("5713434353", "Road Resurfacing", "2099-06-30", "1110055555"),
    ]
    for doc, title, deadline, rfx in rows:
        conn.execute(
            "INSERT INTO solicitation (document_number, title, submission_deadline, ariba_posting_link) "
            "VALUES (?, ?, ?, ?)",
            (doc, title, deadline, _LINK.format(rfx)),
        )
    events = aa.open_solicitation_events(conn)
    assert [e["document_number"] for e in events] == [
        "5660182540",
        "5713434353",
        "3949149442",
        "4044346425",
    ]
    assert events[0]["rfx_id"] == "1110066666"


def test_open_events_backstop_from_ariba_posting_when_spine_missing_or_stale(conn):
    _spine(conn, "1111111111", "2026-10-05", "1110000001")
    _spine(conn, "6666666666", None, "1110000006")
    _spine(conn, "7777777777", "2026-09-20", "1110000007")
    _spine(conn, "8888888888", "2026-09-20", "1110000008")
    _posting(conn, "1110000002", "2222222222", "2026-10-03T09:00:00-07:00")
    _posting(conn, "1110000001", "1111111111", "2026-10-05T09:00:00-07:00")
    _posting(conn, "1110000003", None, "2026-10-04T09:00:00-07:00")
    _posting(conn, "1110000004", "4044346425", "2099-12-31T09:00:00-07:00")
    _posting(conn, "1110000005", "5555555555", "2026-09-30T09:00:00-07:00")
    _posting(conn, "1110000006", "6666666666", "2026-10-02T09:00:00-07:00")
    _posting(conn, "1110000007", "7777777777", "2026-10-09T09:00:00-07:00")
    _posting(conn, "1110000008", "8888888888", "2026-09-20T09:00:00-07:00")

    events = aa.open_solicitation_events(conn, today=_TODAY)
    assert [(e["document_number"], e["rfx_id"], e["closes"]) for e in events] == [
        ("6666666666", "1110000006", "2026-10-02"),
        ("2222222222", "1110000002", "2026-10-03"),
        ("1111111111", "1110000001", "2026-10-05"),
        ("7777777777", "1110000007", "2026-10-09"),
        ("4044346425", "1110000004", "2099-12-31"),
    ]


def test_near_close_uncaptured_window_partial_bundles_and_malformed_dates(conn, tmp_path):
    _spine(conn, "1111111111", "2026-10-03", "1110000001")
    _spine(conn, "2222222222", "2026-10-02", "1110000002")
    _spine(conn, "3333333333", "2026-10-11", "1110000003")
    _spine(conn, "4444444444", "2026-09-30", "1110000004")
    _spine(conn, "6666666666", "2026-10-01", "1110000006")
    _spine(conn, "7777777777", "2026-10-02", "1110000007")
    _spine(conn, "8888888888", "2026-13-45", "1110000008")
    _posting(conn, "1110000005", "5555555555", "2026-10-04T09:00:00-07:00")
    _make_zip(tmp_path / "Doc2222222222.zip", {"a.pdf": b"a"})
    ariba_files.capture_files(
        FakeFileSource(["a.pdf", "b.pdf", "c.pdf"], fail_on=["b.pdf"]), "7777777777", tmp_path
    )

    got = aa.near_close_uncaptured(conn, dest_dir=tmp_path, today=_TODAY)
    assert got == [
        {"document_number": "6666666666", "closes": "2026-10-01", "days_left": 0},
        {"document_number": "7777777777", "closes": "2026-10-02", "days_left": 1},
        {"document_number": "1111111111", "closes": "2026-10-03", "days_left": 2},
        {"document_number": "5555555555", "closes": "2026-10-04", "days_left": 3},
    ]
    assert aa.NEAR_CLOSE_DAYS == 3
    assert aa.near_close_uncaptured(conn, dest_dir=tmp_path, days=1, today=_TODAY) == got[:2]
