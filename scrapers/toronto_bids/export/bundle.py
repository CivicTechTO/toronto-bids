"""Shared full-bundle export sequence and release completeness verification (#319, #320)."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from toronto_bids import config
from toronto_bids.export.json_export import export_json, export_schema
from toronto_bids.export.tabular_export import write_csv_zip, write_parquet_files
from toronto_bids.store import db

COMPLETE_MARKER = ".export-complete.json"


def _file_fingerprint(path: Path) -> dict:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": digest}


def write_complete_marker(export_dir: Path, generated_at: str, artifact_names: list[str], *, json_file: str = "bids.json") -> Path:
    """Atomically write the export completion marker describing every generated artifact."""
    export_dir = Path(export_dir)
    payload = {
        "version": 1,
        "generated_at": generated_at,
        "json_file": json_file,
        "artifacts": {name: _file_fingerprint(export_dir / name) for name in sorted(artifact_names)},
    }
    marker_path = export_dir / COMPLETE_MARKER
    with tempfile.NamedTemporaryFile(
        dir=export_dir, prefix=".export-complete.", suffix=".tmp", mode="w", encoding="utf-8", delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
        try:
            json.dump(payload, tmp, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp.close()
            os.replace(tmp_path, marker_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    return marker_path


def _custom_export_artifacts(json_path: Path, artifact_names: list[str]) -> set[str]:
    """Check destination collisions and return prior artifacts whose bytes still match."""
    out_dir = json_path.parent
    marker_path = out_dir / COMPLETE_MARKER
    owned = set()
    if os.path.lexists(marker_path):
        if marker_path.is_symlink() or not marker_path.is_file():
            raise FileExistsError(f"Export marker is occupied: {marker_path}")
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise FileExistsError(f"Export marker is occupied: {marker_path}") from exc
        if (not isinstance(marker, dict) or marker.get("version") != 1
                or marker.get("json_file") != json_path.name
                or not isinstance(marker.get("artifacts"), dict)
                or not {json_path.name, "schema.json", "bids-csv.zip"} <= marker["artifacts"].keys()):
            raise FileExistsError(f"Export marker is occupied: {marker_path}")

        for name, fingerprint in marker["artifacts"].items():
            if name in ("", ".", "..", COMPLETE_MARKER) or "/" in name or chr(0) in name:
                continue
            artifact = out_dir / name
            if not artifact.is_symlink() and artifact.is_file() and _file_fingerprint(artifact) == fingerprint:
                owned.add(name)

    for name in artifact_names:
        target = out_dir / name
        if name not in owned and os.path.lexists(target):
            raise FileExistsError(f"Export output is occupied: {target}")
    return owned


def export_all(conn, out_path=None, *, generated_at: str | None = None) -> dict:
    """Stage a full bundle with one timestamp and publish its completion marker last."""
    default_path = Path(config.DATA_DIR) / "export" / "bids.json"
    json_path = Path(out_path) if out_path else default_path
    out_dir = json_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    custom = json_path.name != default_path.name or out_dir.resolve() != default_path.parent.resolve()

    # Serialize exporters without adding a lock file to the destination.
    directory_fd = os.open(out_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        return _export_bundle(conn, json_path, generated_at, custom=custom)
    finally:
        os.close(directory_fd)


def _export_bundle(conn, json_path: Path, generated_at: str | None, *, custom: bool) -> dict:
    out_dir = json_path.parent
    marker_path = out_dir / COMPLETE_MARKER
    artifact_names = [json_path.name, "schema.json", "bids-csv.zip", *(f"{t}.parquet" for t in db.EXPORT_TABLES)]
    if len(set(artifact_names)) != len(artifact_names) or json_path.name == COMPLETE_MARKER:
        raise FileExistsError(f"JSON output name is reserved by the export bundle: {json_path}")

    if custom:
        obsolete = _custom_export_artifacts(json_path, artifact_names).difference(artifact_names)
    else:
        marker_path.unlink(missing_ok=True)
        obsolete = {p.name for p in out_dir.glob("*.parquet")}.difference(artifact_names)

    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".export-stage.") as stage_raw:
        stage = Path(stage_raw)
        stage_json = export_json(conn, stage / json_path.name, timestamp)
        stage_schema = export_schema(conn, stage / "schema.json", timestamp)
        stage_parquets = write_parquet_files(conn, stage)
        stage_csv = write_csv_zip(conn, stage / "bids-csv.zip")

        staged = [stage_json, stage_schema, stage_csv, *stage_parquets]
        stage_marker = write_complete_marker(stage, timestamp, artifact_names, json_file=json_path.name)
        for src in staged:
            os.replace(src, out_dir / src.name)
        for name in sorted(obsolete):
            (out_dir / name).unlink(missing_ok=True)
        os.replace(stage_marker, marker_path)

    return {
        "generated_at": timestamp,
        "export_dir": out_dir,
        "json_path": json_path,
        "json_bytes": json_path.stat().st_size,
        "schema_path": out_dir / "schema.json",
        "csv_zip_path": out_dir / "bids-csv.zip",
        "parquet_paths": [out_dir / p.name for p in sorted(stage_parquets)],
        "marker_path": marker_path,
    }


def verify_complete_export(export_dir) -> dict:
    """Verify that export_dir holds a complete, internally consistent export ready for publication."""
    export_dir = Path(export_dir)
    marker_path = export_dir / COMPLETE_MARKER
    if not marker_path.is_file():
        raise ValueError(f"missing export completion marker at {marker_path}")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid export completion marker: {exc}") from exc
    if not isinstance(marker, dict) or marker.get("version") != 1:
        raise ValueError("unsupported export completion marker version")
    generated_at = marker.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        raise ValueError("export completion marker missing generated_at")
    json_file = marker.get("json_file")
    if json_file != "bids.json":
        raise ValueError(f"unexpected export json_file {json_file!r}; expected 'bids.json'")
    artifacts = marker.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("export completion marker has no artifacts")

    for required in ("bids.json", "schema.json", "bids-csv.zip"):
        if required not in artifacts:
            raise ValueError(f"export completion marker missing required artifact {required}")
    recorded_parquets = {name for name in artifacts if name.endswith(".parquet")}
    if not recorded_parquets:
        raise ValueError("export completion marker lists no Parquet artifacts")
    actual_parquets = {p.name for p in export_dir.glob("*.parquet")}
    if actual_parquets != recorded_parquets:
        raise ValueError(
            f"Parquet files in {export_dir} do not match completion marker "
            f"(extra={sorted(actual_parquets - recorded_parquets)}, "
            f"missing={sorted(recorded_parquets - actual_parquets)})"
        )

    for name, expected in artifacts.items():
        if not isinstance(expected, dict) or "/" in name or "\\" in name:
            raise ValueError(f"invalid artifact entry {name!r} in completion marker")
        path = export_dir / name
        if not path.is_file():
            raise ValueError(f"missing export artifact {path}")
        actual = _file_fingerprint(path)
        if actual["bytes"] != expected.get("bytes") or actual["sha256"] != expected.get("sha256"):
            raise ValueError(f"export artifact {name} does not match completion marker")

    bids_doc = json.loads((export_dir / "bids.json").read_text(encoding="utf-8"))
    bids_ts = (bids_doc.get("meta") or {}).get("generated_at")
    if bids_ts != generated_at:
        raise ValueError(f"bids.json generated_at ({bids_ts!r}) does not match marker ({generated_at!r})")
    schema_doc = json.loads((export_dir / "schema.json").read_text(encoding="utf-8"))
    schema_ts = schema_doc.get("generated_at")
    if schema_ts != generated_at:
        raise ValueError(f"schema.json generated_at ({schema_ts!r}) does not match marker ({generated_at!r})")
    return marker


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m toronto_bids.export.bundle EXPORT_DIR", file=sys.stderr)
        return 2
    try:
        marker = verify_complete_export(args[0])
    except Exception as exc:  # noqa: BLE001
        print(f"export verification failed: {exc}", file=sys.stderr)
        return 1
    print(marker["generated_at"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
