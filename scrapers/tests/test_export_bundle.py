"""Tests for the shared full-bundle export sequence and completeness validation (#319, #320)."""
import json
from pathlib import Path
import zipfile

import pytest

from toronto_bids import cli, config
from toronto_bids.export import bundle
from toronto_bids.models import Solicitation
from toronto_bids.store import db


def _init_db(tmp_path, monkeypatch):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "bids.sqlite"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    conn = db.connect(db_path)
    db.init_db(conn)
    db.upsert_row(conn, Solicitation(document_number="5672751291", status="Open", source="odata"), overwrite=True)
    conn.commit()
    return conn


def test_export_all_shares_timestamp_and_verifies_cleanly(tmp_path, monkeypatch):
    conn = _init_db(tmp_path, monkeypatch)
    try:
        ts = "2026-11-22T16:20:00+00:00"
        res = bundle.export_all(conn, generated_at=ts)
    finally:
        conn.close()

    export_dir = tmp_path / "export"
    assert res["generated_at"] == ts
    assert res["json_path"] == export_dir / "bids.json"
    assert res["json_bytes"] == (export_dir / "bids.json").stat().st_size
    assert json.loads((export_dir / "bids.json").read_text())["meta"]["generated_at"] == ts
    assert json.loads((export_dir / "schema.json").read_text())["generated_at"] == ts
    assert (export_dir / "bids-csv.zip").is_file()
    with zipfile.ZipFile(export_dir / "bids-csv.zip") as zf:
        assert set(zf.namelist()) == {f"{t}.csv" for t in db.EXPORT_TABLES}
    assert {p.name for p in export_dir.glob("*.parquet")} == {f"{t}.parquet" for t in db.EXPORT_TABLES}

    marker = bundle.verify_complete_export(export_dir)
    assert marker["generated_at"] == ts
    assert bundle.main([str(export_dir)]) == 0


def test_custom_export_preserves_unrelated_parquet_and_removes_owned_obsolete_file(tmp_path, conn):
    output = tmp_path / "custom.json"
    analysis = tmp_path / "analysis.parquet"
    analysis.write_bytes(b"independent analysis")
    result = bundle.export_all(conn, output)

    obsolete = tmp_path / "obsolete.parquet"
    obsolete.write_bytes(b"prior export")
    replaced = tmp_path / "replaced.parquet"
    replaced.write_bytes(b"prior export")
    artifacts = json.loads(result["marker_path"].read_text())["artifacts"]
    bundle.write_complete_marker(
        tmp_path, result["generated_at"], [*artifacts, obsolete.name, replaced.name], json_file=output.name
    )
    replaced.write_bytes(b"independent replacement")

    bundle.export_all(conn, output)
    assert analysis.read_bytes() == b"independent analysis"
    assert replaced.read_bytes() == b"independent replacement"
    assert not obsolete.exists()


@pytest.mark.parametrize("occupied", ["custom.json", "schema.json", "solicitation.parquet", bundle.COMPLETE_MARKER])
def test_custom_export_rejects_occupied_output_before_writing(tmp_path, conn, occupied):
    destination = tmp_path / "custom"
    destination.mkdir()
    (destination / occupied).write_bytes(b"unrelated")
    with pytest.raises(FileExistsError, match=occupied):
        bundle.export_all(conn, destination / "custom.json")
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == {occupied: b"unrelated"}


def test_custom_export_rejects_replaced_artifact_without_changing_bundle(tmp_path, conn):
    output = tmp_path / "custom.json"
    bundle.export_all(conn, output)
    (tmp_path / "schema.json").write_bytes(b"unrelated replacement")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(FileExistsError, match="schema.json"):
        bundle.export_all(conn, output)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_failed_export_invalidates_marker_and_preserves_prior_files_unmixed(tmp_path, monkeypatch):
    conn = _init_db(tmp_path, monkeypatch)
    try:
        first = bundle.export_all(conn, generated_at="2026-11-21T00:00:00+00:00")
        export_dir = first["export_dir"]
        old_json = (export_dir / "bids.json").read_bytes()
        old_csv = (export_dir / "bids-csv.zip").read_bytes()

        # Mutate DB, then force tabular failure midway through a second export.
        db.upsert_row(conn, Solicitation(document_number="5672751292", status="Open", source="odata"), overwrite=True)
        conn.commit()
        monkeypatch.setattr(bundle, "write_csv_zip", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            bundle.export_all(conn, generated_at="2026-11-22T00:00:00+00:00")
    finally:
        conn.close()

    # Prior marker is gone, so publisher refuses even though old artifacts remain on disk.
    assert not (export_dir / bundle.COMPLETE_MARKER).exists()
    with pytest.raises(ValueError, match="missing export completion marker"):
        bundle.verify_complete_export(export_dir)
    # Staging prevented mixing new JSON with old CSV/Parquet.
    assert (export_dir / "bids.json").read_bytes() == old_json
    assert (export_dir / "bids-csv.zip").read_bytes() == old_csv


def test_verify_complete_export_rejects_tampered_or_extra_artifacts(tmp_path, monkeypatch):
    conn = _init_db(tmp_path, monkeypatch)
    try:
        res = bundle.export_all(conn, generated_at="2026-11-22T16:20:00+00:00")
    finally:
        conn.close()
    export_dir = res["export_dir"]

    # Extra stale parquet file rejected.
    stale = export_dir / "stale_table.parquet"
    stale.write_bytes(b"stale")
    with pytest.raises(ValueError, match="Parquet files"):
        bundle.verify_complete_export(export_dir)
    stale.unlink()

    # Mixing fresh bids.json with old tabular files rejected by hash check.
    (export_dir / "bids.json").write_text(json.dumps({"meta": {"generated_at": "2026-11-23T00:00:00+00:00"}}))
    with pytest.raises(ValueError, match="does not match completion marker"):
        bundle.verify_complete_export(export_dir)


def test_cli_export_nightly_and_batch_refresh_share_identical_artifact_layout(tmp_path, monkeypatch):
    from toronto_bids import batch_extraction as batch
    from toronto_bids.claude import ClaudeExtractionClient
    from toronto_bids.extract import EXTRACTOR_VERSION

    # 1) CLI `tb export`
    cli_dir = tmp_path / "cli"
    conn1 = _init_db(cli_dir, monkeypatch)
    conn1.close()
    assert cli.main(["export"]) == 0
    cli_files = {p.name for p in (cli_dir / "export").iterdir()}
    cli_marker = bundle.verify_complete_export(cli_dir / "export")
    assert json.loads((cli_dir / "export/bids.json").read_text())["meta"]["generated_at"] == cli_marker["generated_at"]
    assert json.loads((cli_dir / "export/schema.json").read_text())["generated_at"] == cli_marker["generated_at"]

    # 2) Batch refresh via `tb extraction-batches refresh --manifest ...`
    batch_dir = tmp_path / "batch"
    conn2 = _init_db(batch_dir, monkeypatch)
    conn2.execute(
        "INSERT INTO background_pdf (url,kind,sha256,text,document_number) "
        "VALUES ('https://example.com/f.pdf','award_summary','sha-1','form','5672751291')"
    )
    conn2.commit()
    conn2.close()

    queue = batch.BatchQueue(batch_dir / "llm-usage.sqlite", api_key="test")
    body = ClaudeExtractionClient(api_key="test").request_body("form")
    with pytest.raises(batch.ExtractionPending):
        queue.document("sha-1", EXTRACTOR_VERSION, [body], source="pdf")
    # Mark job ready directly so collect imports it into extraction_cache offline.
    qconn = queue._connect()
    try:
        with qconn:
            qconn.execute(
                "UPDATE batch_job SET status='ready', result=?",
                (json.dumps({"contracts": [{"reference": "5672751291", "bids": [{"supplier_name": "Acme", "amount_raw": "$10"}]}]}),),
            )
    finally:
        qconn.close()

    handoff = batch_dir / "handoff.json"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert cli.main(["extraction-batches", "refresh", "--manifest", str(handoff)]) == 0
    assert handoff.is_file()
    batch_files = {p.name for p in (batch_dir / "export").iterdir()}
    batch_marker = bundle.verify_complete_export(batch_dir / "export")
    assert batch_files == cli_files
    assert json.loads((batch_dir / "export/bids.json").read_text())["meta"]["generated_at"] == batch_marker["generated_at"]
    assert json.loads((batch_dir / "export/schema.json").read_text())["generated_at"] == batch_marker["generated_at"]
