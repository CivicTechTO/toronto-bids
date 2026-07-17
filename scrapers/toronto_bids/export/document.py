import json
from datetime import datetime, timezone

from toronto_bids.store import db


def _rows(conn, sql):
    return [dict(r) for r in conn.execute(sql).fetchall()]


def _drop(record: dict, *keys) -> dict:
    return {k: v for k, v in record.items() if k not in keys}


def _parse_json(record: dict, key: str) -> dict:
    raw = record.get(key)
    if raw:
        try:
            record[key] = json.loads(raw)
        except (TypeError, ValueError):
            pass  # leave the raw string if it isn't valid JSON
    return record


def build_export_document(conn, generated_at: str | None = None) -> dict:
    """Assemble the solicitation-centric nested export document from the store.

    Pure and deterministic: no file I/O, every query ordered. Awards and Ariba
    postings are nested under their solicitation only when their document_number
    matches an existing solicitation; everything else (NULL or non-matching
    document_number) goes to unlinked_ariba_postings / unlinked_awards
    (nothing dropped).
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()

    sol_docs = {r["document_number"] for r in _rows(conn, "SELECT document_number FROM solicitation")}

    awards_by_doc: dict[str, list] = {}
    unlinked_awards: list = []
    for award in _rows(conn, "SELECT * FROM award ORDER BY document_number, id"):
        doc = award["document_number"]
        cleaned = _drop(award, "id")
        if doc in sol_docs:
            awards_by_doc.setdefault(doc, []).append(_drop(cleaned, "document_number"))
        else:
            unlinked_awards.append(cleaned)

    postings_by_doc: dict[str, list] = {}
    unlinked: list = []
    for posting in _rows(conn, "SELECT * FROM ariba_posting ORDER BY rfx_id"):
        posting = _parse_json(_drop(posting, "raw_json"), "categories")
        doc = posting.get("document_number")
        if doc and doc in sol_docs:
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
        _drop(nc, "odata_id")
        for nc in _rows(conn, "SELECT * FROM noncompetitive ORDER BY workspace_number")
    ]

    # Forward-looking and joinable to nothing — a project has no document_number until it
    # is actually solicited. Top-level rather than nested for that reason (#69).
    capital_projects = _rows(conn, "SELECT * FROM capital_project ORDER BY name")

    # 2009-2012 awards, keyed on Call Number because they predate Ariba (#96). Top-level for
    # the same reason as capital_projects: they nest under no solicitation and never will.
    # For 2009-2011 these are the only awards the archive has at all.
    composite_awards = _rows(
        conn, "SELECT * FROM composite_award ORDER BY call_number, supplier_name_raw")

    suspended_firms = [
        _drop(firm, "id")
        for firm in _rows(conn, "SELECT * FROM suspended_firm ORDER BY supplier_name_raw, council_authority")
    ]

    suppliers = [
        _parse_json(_drop(s, "supplier_key"), "variants")
        for s in _rows(conn, "SELECT * FROM supplier ORDER BY display_name")
    ]

    sources = _rows(
        conn,
        "SELECT source, status, finished_at, rows_fetched, rows_upserted "
        "FROM sync_run ORDER BY id",
    )

    pdfs_by_ref: dict[str, list] = {}
    bids_by_ref = {}
    for bid in _rows(conn, "SELECT * FROM bid ORDER BY reference, bidder_name_raw, id"):
        bids_by_ref.setdefault(bid["reference"], []).append(_drop(bid, "id"))

    for pdf in _rows(conn, "SELECT * FROM background_pdf ORDER BY reference, url"):
        pdfs_by_ref.setdefault(pdf["reference"], []).append(_drop(pdf, "id", "text", "local_path"))

    council_items = []
    for ci in _rows(conn, "SELECT * FROM council_item ORDER BY reference"):
        ci["background_pdfs"] = pdfs_by_ref.get(ci["reference"], [])
        ci["bids"] = bids_by_ref.get(ci["reference"], [])
        council_items.append(ci)

    return {
        "meta": {
            "generated_at": generated_at,
            "counts": db.counts(conn),
            "sources": sources,
        },
        "solicitations": solicitations,
        "noncompetitive": noncompetitive,
        "suspended_firms": suspended_firms,
        "suppliers": suppliers,
        "capital_projects": capital_projects,
        "composite_awards": composite_awards,
        "council_items": council_items,
        "unlinked_ariba_postings": unlinked,
        "unlinked_awards": unlinked_awards,
    }
