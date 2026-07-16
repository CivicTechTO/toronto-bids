import json

from toronto_bids.export.json_export import export_json
from toronto_bids.models import Solicitation
from toronto_bids.store import db


def test_export_writes_valid_json_file(conn, tmp_path):
    db.upsert_row(conn, Solicitation(document_number="5672751291", title="RFT", source="odata"),
                  overwrite=True)
    conn.commit()
    out = tmp_path / "nested" / "bids.json"
    result = export_json(conn, out, generated_at="2026-07-15T00:00:00Z")
    assert result == out
    assert out.exists()  # parent dir created
    doc = json.loads(out.read_text())
    assert doc["meta"]["generated_at"] == "2026-07-15T00:00:00Z"
    assert doc["solicitations"][0]["document_number"] == "5672751291"


def test_export_writes_utf8_content(conn, tmp_path):
    db.upsert_row(conn, Solicitation(document_number="5672751291", title="Café Réno",
                                     source="odata"), overwrite=True)
    conn.commit()
    out = tmp_path / "bids.json"
    export_json(conn, out, generated_at="t")
    # ensure_ascii=False keeps accented characters literal, not \u-escaped
    assert "Café Réno" in out.read_text(encoding="utf-8")
