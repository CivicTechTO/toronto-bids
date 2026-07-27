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
        # A real download is a zip -- merge_bundles opens each batch as one, so the fake must
        # produce one too, with an entry per selected row to keep merged names distinct.
        with zipfile.ZipFile(path, "w") as zf:
            for key in sorted(self.selected):
                zf.writestr(f"{key}.pdf", key.encode())
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


def test_a_failed_merge_leaves_no_target_behind(tmp_path):
    """capture_attachments treats Doc<n>.zip existing as "already archived" -- a partial
    merge that left a readable-but-incomplete file there would be silent, permanent data
    loss, since nothing would ever retry it. The target must not exist after a raised merge,
    and the input batches must be untouched."""
    a = _make_zip(tmp_path / "batch-01.zip", {"Same.pdf": b"one"})
    b = _make_zip(tmp_path / "batch-02.zip", {"Same.pdf": b"two"})
    a_before = a.read_bytes()
    b_before = b.read_bytes()
    target = tmp_path / "Doc3.zip"

    with pytest.raises(RuntimeError, match="collision"):
        ariba_batch.merge_bundles([a, b], target)

    assert not target.exists()
    assert not list(tmp_path.glob("Doc3.zip.*"))  # no stray temp file either
    assert a.read_bytes() == a_before
    assert b.read_bytes() == b_before


def test_merge_preserves_member_date_time(tmp_path):
    """The copy loop must pass the source ZipInfo, not the bare filename, or every merged
    member's date_time resets to 1980-01-01 and its compression reverts to the archive
    default -- an inconsistency with normal single-download bundles, which keep both."""
    path = tmp_path / "batch-01.zip"
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("Report.pdf", date_time=(2019, 6, 15, 10, 30, 0))
        zf.writestr(info, b"report bytes")

    target, _ = ariba_batch.merge_bundles([path], tmp_path / "Doc6.zip")

    with zipfile.ZipFile(target) as zf:
        merged = zf.getinfo("Report.pdf")
        assert merged.date_time == (2019, 6, 15, 10, 30, 0)


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


def test_finalise_partial_salvages_a_batch_absent_from_the_manifest(tmp_path):
    """A crash between `download_to()` writing a batch and the following `write_manifest()`
    leaves a valid batch-NN.zip on disk that the manifest never recorded. Since a batch can
    never be re-fetched once the posting closes, finalise_partial must still pick it up from
    disk rather than silently dropping it (and then deleting it via the pdir rmtree)."""
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    _make_zip(pdir / "batch-01.zip", {"A.pdf": b"a"})
    _make_zip(pdir / "batch-02.zip", {"B.pdf": b"b"})  # written, but never made it into the manifest
    ariba_batch.write_manifest(
        pdir, ariba_batch.make_fingerprint(["1.1"], 54, 792.41),
        batches=[["1.1"]], omitted=[])

    bundle = ariba_batch.finalise_partial("5713434353", tmp_path)

    assert bundle == tmp_path / "Doc5713434353.zip"
    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["A.pdf", "B.pdf"]


def test_a_corrupt_manifest_discards_the_partials_and_replans(tmp_path):
    """A corrupt manifest must not resume numbering at batch-01 while leaving other orphaned
    batch files untouched (finding 1). Without a clean discard, a re-plan that today needs
    fewer batches than a previous, unrecorded attempt would overwrite batch-01.zip with fresh
    content but leave a stale batch-02.zip sitting on disk -- which _finalise's glob then
    merges into the final bundle as leftover debris. Discarding the whole partial dir first
    (like the stale-fingerprint branch does) prevents that leak."""
    picker = FakePicker({"1.1": 100})   # today's plan fits in a single batch
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    _make_zip(pdir / "batch-01.zip", {"OLD1.pdf": b"stale"})
    _make_zip(pdir / "batch-02.zip", {"OLD2.pdf": b"stale-leftover"})  # debris from an
    # earlier, abandoned attempt that the corrupt manifest never recorded
    (pdir / ariba_batch.MANIFEST_NAME).write_text("not json at all")

    fp = ariba_batch.make_fingerprint(picker.row_keys(), 1, 100.0)
    bundle = ariba_batch.capture_in_batches(
        picker, "5713434353", tmp_path, fp, threshold_mb=450)

    assert bundle.exists()
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
        assert "OLD2.pdf" not in names   # the orphaned leftover batch must not leak in
        assert "OLD1.pdf" not in names
        assert "1.1.pdf" in names        # today's fresh download for the only current row


def test_a_failed_download_leaves_no_partial_batch_file_behind(tmp_path):
    """A download that fails partway can leave a truncated batch-NN.zip on disk -- to
    _finalise's glob that debris is indistinguishable from a good batch and would abort the
    merge of every sibling batch (finding 2). The failure handler must remove it."""
    class WritesThenDies(FakePicker):
        def download_to(self, path):
            path.write_bytes(b"PK\x03\x04truncated garbage")  # partial write, then boom
            raise RuntimeError("connection reset")

    picker = WritesThenDies({"1.1": 300, "1.2": 300})
    fp = ariba_batch.make_fingerprint(picker.row_keys(), 2, 600.0)

    result = ariba_batch.capture_in_batches(
        picker, "5713434353", tmp_path, fp, threshold_mb=450)

    assert result is None
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    assert list(pdir.glob("batch-*.zip")) == []


def test_finalise_partial_skips_a_corrupt_batch_and_merges_the_rest(tmp_path):
    """One corrupt batch must not abort recovery of its siblings (finding 2) -- and the gap
    must be durable, not just a log line."""
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    _make_zip(pdir / "batch-01.zip", {"A.pdf": b"a"})
    (pdir / "batch-02.zip").write_bytes(b"not a zip at all")
    _make_zip(pdir / "batch-03.zip", {"C.pdf": b"c"})
    ariba_batch.write_manifest(
        pdir, ariba_batch.make_fingerprint(["1.1", "1.2", "1.3"], 3, 3.0),
        batches=[["1.1"], ["1.2"], ["1.3"]], omitted=[])

    bundle = ariba_batch.finalise_partial("5713434353", tmp_path)

    assert bundle == tmp_path / "Doc5713434353.zip"
    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["A.pdf", "C.pdf"]
    body = json.loads((tmp_path / "Doc5713434353.omitted.json").read_text())
    assert "batch-02.zip" in body["omitted"]


def test_read_manifest_returns_none_on_corrupt_json(tmp_path):
    """A torn write (crash mid-write, or corruption at rest) must degrade to "no manifest"
    rather than raising -- an uncaught JSONDecodeError here would abort the whole capture."""
    pdir = tmp_path / "pdir"
    pdir.mkdir()
    (pdir / ariba_batch.MANIFEST_NAME).write_text('{"fingerprint": {"row_keys": [')

    assert ariba_batch.read_manifest(pdir) is None


def test_finalise_partial_salvages_from_disk_when_manifest_is_corrupt(tmp_path):
    """Even with no readable manifest at all, the batch zips on disk must not be lost -- this
    is what makes degrading a corrupt manifest to None safe rather than lossy."""
    pdir = ariba_batch.partial_dir(tmp_path, "5713434353")
    _make_zip(pdir / "batch-01.zip", {"A.pdf": b"a"})
    _make_zip(pdir / "batch-02.zip", {"B.pdf": b"b"})
    (pdir / ariba_batch.MANIFEST_NAME).write_text("not json at all")

    bundle = ariba_batch.finalise_partial("5713434353", tmp_path)

    assert bundle == tmp_path / "Doc5713434353.zip"
    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["A.pdf", "B.pdf"]
