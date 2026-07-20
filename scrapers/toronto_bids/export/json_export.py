import json
from pathlib import Path

from toronto_bids.export.document import build_export_document
from toronto_bids.export.schema_export import build_schema_document


def export_json(conn, out_path, generated_at: str | None = None) -> Path:
    document = build_export_document(conn, generated_at)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def export_schema(conn, out_path, generated_at: str | None = None) -> Path:
    document = build_schema_document(conn, generated_at)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path
