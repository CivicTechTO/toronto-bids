"""The pure half of the Ariba attachment archive (#117): manifest + storage.

No browser and no network — the login/Respond/download half is exercised live, but indexing a
downloaded bundle is where the correctness lives (what document a bundle belongs to, what files
it holds, and that re-ingesting never duplicates).
"""
import io
import zipfile

from toronto_bids.sources import ariba_attachments as aa
from toronto_bids.store import db as _db
from toronto_bids.models import AribaAttachment


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


def test_index_zip_recurses_into_nested_zips_with_full_paths(tmp_path):
    inner = _zip_bytes({"drawings/site-plan.pdf": b"plan", "notes.txt": b"n"})
    z = _make_zip(tmp_path / "Doc1.zip", {
        "PART 1 - RFP.pdf": b"rfp",
        "Appendix C2 - Planning Documents.zip": inner,
    })
    by_path = {e["path"]: e for e in aa.index_zip(z)}
    # The container zip is gone; its contents surface with prefixed paths.
    assert set(by_path) == {
        "PART 1 - RFP.pdf",
        "Appendix C2 - Planning Documents.zip/drawings/site-plan.pdf",
        "Appendix C2 - Planning Documents.zip/notes.txt",
    }
    nested = by_path["Appendix C2 - Planning Documents.zip/drawings/site-plan.pdf"]
    assert nested["filename"] == "drawings/site-plan.pdf"   # leaf name within its zip
    assert nested["file_size"] == len(b"plan")
    assert int(nested["crc32"], 16) == zipfile.crc32(b"plan")


def test_index_zip_records_a_corrupt_nested_zip_as_a_leaf(tmp_path):
    z = _make_zip(tmp_path / "Doc2.zip", {
        "good.pdf": b"ok",
        "broken.zip": b"not a valid zip file",
    })
    by_path = {e["path"]: e for e in aa.index_zip(z)}
    # The unreadable zip is kept as its own leaf rather than lost or fatal.
    assert set(by_path) == {"good.pdf", "broken.zip"}


def test_index_zip_caps_total_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(aa, "_MAX_ZIP_ENTRIES", 3)
    z = _make_zip(tmp_path / "Doc3.zip", {f"f{i}.pdf": b"x" for i in range(10)})
    assert len(aa.index_zip(z)) == 3


def test_document_number_comes_from_the_Doc_token_not_stray_digits():
    assert aa.document_number_from_zip_name("Doc5660182540.zip") == "5660182540"
    # A filename with other digits must not fabricate a key from digit-stripping.
    assert aa.document_number_from_zip_name("2026-report.zip") is None
    assert aa.document_number_from_zip_name("random.zip") is None


def test_index_zip_lists_files_with_size_and_crc_and_drops_directories(tmp_path):
    z = _make_zip(tmp_path / "b.zip", {
        "PART 1 - RFP.pdf": b"hello world",
        "sub/Appendix A.pdf": b"appendix bytes",
    })
    entries = {e["filename"]: e for e in aa.index_zip(z)}
    # Only real files; the implicit "sub/" directory entry is not indexed.
    assert set(entries) == {"PART 1 - RFP.pdf", "sub/Appendix A.pdf"}
    assert entries["PART 1 - RFP.pdf"]["file_size"] == len(b"hello world")
    # CRC32 is the fixed 8-hex-digit central-directory value.
    crc = entries["PART 1 - RFP.pdf"]["crc32"]
    assert len(crc) == 8 and int(crc, 16) == zipfile.crc32(b"hello world")


def test_store_bundle_copies_to_canonical_path_and_indexes_every_file(conn, tmp_path):
    src = _make_zip(tmp_path / "Doc5660182540.zip", {"A.pdf": b"a", "B.pdf": b"bb"})
    dest = tmp_path / "store"
    n = aa.store_bundle(conn, src, "5660182540", dest)
    assert n == 2
    assert (dest / "Doc5660182540.zip").exists()          # keyed on document number

    rows = conn.execute(
        "SELECT filename, file_size, zip_name, zip_sha256 FROM ariba_attachment "
        "WHERE document_number='5660182540' ORDER BY filename").fetchall()
    assert [r["filename"] for r in rows] == ["A.pdf", "B.pdf"]
    assert rows[0]["zip_name"] == "Doc5660182540.zip"
    assert all(len(r["zip_sha256"]) == 64 for r in rows)  # one bundle hash on every file


def test_ingest_scans_a_folder_skips_unnamed_zips_and_is_idempotent(conn, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _make_zip(downloads / "Doc5660182540.zip", {"A.pdf": b"a"})
    _make_zip(downloads / "not-a-doc.zip", {"X.pdf": b"x"})   # no Doc########## -> skipped
    dest = tmp_path / "store"

    assert aa.ingest_downloads(conn, downloads, dest) == 1   # only the Doc-named one
    # Re-ingesting the same bundle updates in place — UNIQUE(document_number, filename) holds.
    assert aa.ingest_downloads(conn, downloads, dest) == 1
    total = conn.execute("SELECT COUNT(*) FROM ariba_attachment").fetchone()[0]
    assert total == 1


def test_init_db_migrates_old_unique_index_to_path(tmp_path):
    # Build a database with the OLD schema (UNIQUE on filename) and a stale row.
    conn = _db.connect(":memory:")
    conn.executescript(
        "CREATE TABLE ariba_attachment (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "document_number TEXT NOT NULL, filename TEXT NOT NULL, file_size INTEGER, "
        "crc32 TEXT, zip_name TEXT, zip_sha256 TEXT, "
        "first_seen TEXT NOT NULL DEFAULT (datetime('now')), "
        "last_seen TEXT NOT NULL DEFAULT (datetime('now')), "
        "UNIQUE (document_number, filename));")
    conn.execute("INSERT INTO ariba_attachment (document_number, filename) VALUES ('1','a.zip')")
    conn.commit()

    _db.init_db(conn)   # must add `path` and swap the unique index without error

    cols = [r[1] for r in conn.execute("PRAGMA table_info(ariba_attachment)")]
    assert "path" in cols
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='ariba_attachment'").fetchone()[0]
    assert "document_number, path" in sql
    assert "document_number, filename" not in sql
    # Two leaves sharing a filename but differing in path now coexist (the old key forbade it).
    for p in ("x.zip/a.pdf", "y.zip/a.pdf"):
        _db.upsert_row(conn, AribaAttachment(document_number="1", filename="a.pdf", path=p),
                       overwrite=True)
    assert conn.execute("SELECT COUNT(*) FROM ariba_attachment WHERE document_number='1' "
                        "AND filename='a.pdf'").fetchone()[0] == 2
