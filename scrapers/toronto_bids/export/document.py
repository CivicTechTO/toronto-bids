import json
from datetime import datetime, timezone

from toronto_bids.store import db


def _rows(conn, sql):
    return [dict(r) for r in conn.execute(sql).fetchall()]


def _drop(record: dict, *keys) -> dict:
    return {k: v for k, v in record.items() if k not in keys}


def _parse_categories(posting: dict) -> dict:
    raw = posting.get("categories")
    if raw:
        try:
            posting["categories"] = json.loads(raw)
        except (TypeError, ValueError):
            pass  # leave the raw string if it isn't valid JSON
    return posting


def build_export_document(conn, generated_at: str | None = None) -> dict:
    """Assemble the solicitation-centric nested export document from the store.

    Pure and deterministic: no file I/O, every query ordered. Awards and Ariba
    postings are nested under their solicitation by document_number; postings
    with a NULL document_number go to unlinked_ariba_postings (nothing dropped).
    """
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()

    awards_by_doc: dict[str, list] = {}
    for award in _rows(conn, "SELECT * FROM award ORDER BY document_number, id"):
        awards_by_doc.setdefault(award["document_number"], []).append(
            _drop(award, "id", "supplier_id", "document_number")
        )

    postings_by_doc: dict[str, list] = {}
    unlinked: list = []
    for posting in _rows(conn, "SELECT * FROM ariba_posting ORDER BY rfx_id"):
        posting = _parse_categories(_drop(posting, "raw_json"))
        doc = posting.get("document_number")
        if doc:
            postings_by_doc.setdefault(doc, []).append(_drop(posting, "document_number"))
        else:
            unlinked.append(posting)

    solicitations = []
    for sol in _rows(conn, "SELECT * FROM solicitation ORDER BY document_number"):
        sol = _drop(sol, "odata_id")
        doc = sol["document_number"]
        sol["awards"] = awards_by_doc.get(doc, [])
        sol["ariba_postings"] = postings_by_doc.get(doc, [])
        solicitations.append(sol)

    noncompetitive = [
        _drop(nc, "supplier_id", "odata_id")
        for nc in _rows(conn, "SELECT * FROM noncompetitive ORDER BY workspace_number")
    ]

    sources = _rows(
        conn,
        "SELECT source, status, finished_at, rows_fetched, rows_upserted "
        "FROM sync_run ORDER BY id",
    )

    return {
        "meta": {
            "generated_at": generated_at,
            "counts": db.counts(conn),
            "sources": sources,
        },
        "solicitations": solicitations,
        "noncompetitive": noncompetitive,
        "unlinked_ariba_postings": unlinked,
    }
