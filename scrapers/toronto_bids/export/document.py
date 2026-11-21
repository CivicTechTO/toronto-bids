import json
from datetime import datetime, timezone

from toronto_bids import config
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
            pass
    return record


def _ext(name: str | None) -> str | None:
    if not name:
        return None
    leaf = name.rsplit("/", 1)[-1]
    dot = leaf.rfind(".")
    return leaf[dot + 1:].lower() if dot != -1 else None


def _attribution() -> list[dict]:
    """Collect attribution statements for enabled portals, sorted by slug."""
    return [
        {
            "slug": p["slug"],
            "body": p["body"],
            "statement": p["attribution"],
            "source_url": p["portal_url"],
            "permission": p["permission"],
        }
        for p in sorted(config.BIDS_TENDERS_PORTALS + config.BONFIRE_PORTALS + config.MERX_PORTALS, key=lambda p: p["slug"])
        if p["enabled"] and p.get("attribution")
    ]


def build_export_document(conn, generated_at: str | None = None) -> dict:
    """Assemble the nested solicitation-centric export document.

    Awards, postings, and reference-null bids nest under their solicitation when
    document_number matches, or fall into unlinked_* lists.
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()

    sol_docs = {r["document_number"] for r in _rows(conn, "SELECT document_number FROM solicitation")}
    from toronto_bids.linking.city_document_aliases import applied_targets
    city_alias_targets = applied_targets(conn)

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

    documents_by_doc: dict[str, list] = {}
    for att in _rows(conn, "SELECT document_number, filename, COALESCE(path, filename) AS path, "
                           "file_size FROM ariba_attachment ORDER BY document_number, path"):
        leaf = att["path"].rsplit("/", 1)[-1]
        documents_by_doc.setdefault(att["document_number"], []).append({
            "source": "ariba_attachment",
            "name": leaf,
            "path": att["path"],
            "type": _ext(leaf),
            "size_bytes": att["file_size"],
            "url": None,
        })
    for form in _rows(conn, "SELECT document_number, url FROM background_pdf "
                            "WHERE kind='award_summary' ORDER BY document_number, url"):
        documents_by_doc.setdefault(form["document_number"], []).append({
            "source": "award_summary",
            "name": "Award Summary Form.pdf",
            "path": "Award Summary Form.pdf",
            "type": "pdf",
            "size_bytes": None,
            "url": form["url"],
        })
    # Nest by reference only when all evidence points to a single document_number.
    bridge_candidates = {}
    for row in _rows(conn, "SELECT DISTINCT reference, document_number FROM bid "
                           "WHERE reference IS NOT NULL AND document_number IS NOT NULL "
                           "ORDER BY reference, document_number"):
        bridge_candidates.setdefault(row["reference"], set()).add(city_alias_targets.get(row['document_number'], row['document_number']))
    for row in _rows(conn, "SELECT reference, document_number FROM solicitation_link ORDER BY reference"):
        bridge_candidates.setdefault(row["reference"], set()).add(city_alias_targets.get(row['document_number'], row['document_number']))
    bridge = {ref: next(iter(docs)) for ref, docs in bridge_candidates.items() if len(docs) == 1}
    for report in _rows(conn, "SELECT reference, url FROM background_pdf "
                              "WHERE kind='bgrd' ORDER BY reference, url"):
        doc = bridge.get(report["reference"])
        if doc in sol_docs:
            name = report["url"].rsplit("/", 1)[-1]
            documents_by_doc.setdefault(doc, []).append({
                "source": "staff_report",
                "name": name,
                "path": name,
                "type": _ext(name),
                "size_bytes": None,
                "url": report["url"],
            })

    # Attach directly proven report URLs without requiring a reference bridge.
    for receipt in _rows(conn, "SELECT document_number,report_url FROM city_native_bid_evidence UNION "
                              "SELECT document_number,report_url FROM city_bulk_bid_evidence ORDER BY document_number,report_url"):
        doc=receipt['document_number'];url=receipt['report_url']
        if doc in sol_docs and not any(d.get('url')==url for d in documents_by_doc.get(doc,[])):
            name=url.rsplit('/',1)[-1]
            documents_by_doc.setdefault(doc,[]).append(dict(source='staff_report',name=name,path=name,type='pdf',size_bytes=None,url=url))

    # Route bids by identifier: matched/bridged/reference-null under solicitation, other council-ref bids under council_item, rest unlinked.
    scoped_participation_ids = {r[0] for r in conn.execute("SELECT DISTINCT bid_id FROM bid_response WHERE bid_id IS NOT NULL")}
    bids_by_ref: dict = {}
    bids_by_doc: dict[str, list] = {}
    unlinked_bids: list = []
    for bid in _rows(conn, "SELECT * FROM bid ORDER BY reference, document_number, bidder_name_raw, id"):
        cleaned = _drop(bid, "id")
        ref, doc = bid["reference"], bid["document_number"]
        bridged = bridge.get(ref) if ref is not None else None
        alias_target = city_alias_targets.get(doc)
        if alias_target in sol_docs:
            bids_by_doc.setdefault(alias_target, []).append(cleaned)  # keep original published document_number
        elif bid['id'] in scoped_participation_ids and doc in sol_docs:
            bids_by_doc.setdefault(doc, []).append(_drop(cleaned, 'document_number'))
        elif ref is not None and doc is None and bridged in sol_docs:
            bids_by_doc.setdefault(bridged, []).append(_drop(cleaned, "document_number"))
        elif ref is not None:
            bids_by_ref.setdefault(ref, []).append(cleaned)
        elif doc in sol_docs:
            bids_by_doc.setdefault(doc, []).append(_drop(cleaned, "document_number"))
        else:
            unlinked_bids.append(cleaned)

    from toronto_bids.export.scoped_bids import build_scoped_export
    scoped_by_doc = build_scoped_export(conn)
    solicitations = []
    for sol in _rows(conn, "SELECT * FROM solicitation ORDER BY document_number"):
        sol = _drop(sol, "odata_id")
        doc = sol["document_number"]
        sol["awards"] = awards_by_doc.get(doc, [])
        sol["ariba_postings"] = postings_by_doc.get(doc, [])
        sol["documents"] = documents_by_doc.get(doc, [])
        sol["bids"] = bids_by_doc.get(doc, [])
        if doc in scoped_by_doc:
            sol['bid_scopes'] = scoped_by_doc[doc]['scopes']
            sol['bid_scope_summary'] = scoped_by_doc[doc]['summary']
        solicitations.append(sol)

    noncompetitive = [
        _drop(nc, "odata_id")
        for nc in _rows(conn, "SELECT * FROM noncompetitive ORDER BY workspace_number")
    ]

    capital_projects = _rows(conn, "SELECT * FROM capital_project ORDER BY name")

    # 2009-2012 pre-Ariba awards keyed on Call Number.
    composite_awards = _rows(
        conn, "SELECT * FROM composite_award ORDER BY call_number, supplier_name_raw")

    suspended_firms = [
        _drop(firm, "id")
        for firm in _rows(conn, "SELECT * FROM suspended_firm ORDER BY supplier_name_raw, council_authority")
    ]

    # Preserve supplier_key as the stable permalink; supplier_id is rebuilt every sync.
    suppliers = [
        _parse_json(dict(s), "variants")
        for s in _rows(conn, "SELECT * FROM supplier ORDER BY display_name")
    ]

    sources = _rows(
        conn,
        "SELECT source, status, finished_at, rows_fetched, rows_upserted "
        "FROM sync_run ORDER BY id",
    )

    pdfs_by_ref: dict[str, list] = {}
    for pdf in _rows(conn, "SELECT * FROM background_pdf ORDER BY reference, url"):
        pdfs_by_ref.setdefault(pdf["reference"], []).append(_drop(pdf, "id", "text", "local_path"))

    council_items = []
    for ci in _rows(conn, "SELECT * FROM council_item ORDER BY reference"):
        ci["background_pdfs"] = pdfs_by_ref.get(ci["reference"], [])
        ci["bids"] = bids_by_ref.get(ci["reference"], [])
        council_items.append(ci)

    # Agency/corporation buyers, separate from the City solicitation spine.
    buyers_out = []
    for buyer in _rows(conn, "SELECT * FROM buyer ORDER BY slug"):
        bid_ = buyer["id"]
        buyers_out.append(_drop(buyer, "id") | {
            "solicitations": [_drop(r, "id", "buyer_id") for r in _rows(
                conn, f"SELECT * FROM agency_solicitation WHERE buyer_id={bid_} "
                      "ORDER BY native_ref")],
            "award_publications": [_parse_json(_drop(r, "buyer_id"), "labels") for r in _rows(
                conn, f"SELECT * FROM award_publication WHERE buyer_id={bid_} ORDER BY publication_month,source_position,source_key")],
            "published_awards": [_parse_json(_drop(r, "buyer_id"), "evidence_json") for r in _rows(
                conn, f"SELECT * FROM published_award WHERE buyer_id={bid_} ORDER BY source_key")],
            "publication_extractions": [_parse_json(_drop(r, "buyer_id"), "errors") for r in _rows(
                conn, f"SELECT * FROM publication_extraction WHERE buyer_id={bid_} ORDER BY publication_month")],
            "merx_captures": [_parse_json(_drop(r, "id", "buyer_id"), "report_json") for r in _rows(
                conn, f"SELECT * FROM merx_capture WHERE buyer_id={bid_} ORDER BY id")],
            "portal_listings": [_drop(r, "id", "buyer_id") for r in _rows(
                conn, f"SELECT * FROM portal_listing WHERE buyer_id={bid_} ORDER BY portal_url")],
            "awards": [_drop(r, "id", "buyer_id") for r in _rows(
                conn, f"SELECT * FROM agency_award WHERE buyer_id={bid_} "
                      "ORDER BY native_ref, supplier_name_raw")],
            "bids": [_drop(r, "id", "buyer_id") for r in _rows(
                conn, f"SELECT * FROM agency_bid WHERE buyer_id={bid_} "
                      "ORDER BY native_ref, bidder_name_raw")],
        })

    return {
        "meta": {
            "generated_at": generated_at,
            "counts": db.counts(conn),
            "sources": sources,
            "attribution": _attribution(),
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
        "unlinked_bids": unlinked_bids,
        "buyers": buyers_out,
        "procurements": _rows(conn, "SELECT * FROM procurement ORDER BY id"),
        "procurement_keys": _rows(conn, "SELECT * FROM procurement_key ORDER BY namespace,value"),
        "procurement_documents": _rows(conn, "SELECT * FROM procurement_document ORDER BY procurement_id,url"),
        "procurement_council_items": _rows(conn, "SELECT * FROM procurement_council_item ORDER BY procurement_id,reference"),
        "pdf_text": [_parse_json(row, "provenance_json") for row in _rows(
            conn, "SELECT * FROM pdf_text ORDER BY sha256")],
        "city_scoped_bid_evidence": [_parse_json(row, "proof_json") for row in _rows(
            conn, "SELECT * FROM city_scoped_bid_evidence ORDER BY proof_sha256")],
        "city_bulk_bid_evidence": [_parse_json(row, "proof_json") for row in _rows(
            conn, "SELECT * FROM city_bulk_bid_evidence ORDER BY proof_sha256")],
        "city_bid_evidence": [_parse_json(row, "proof_json") for row in _rows(
            conn, "SELECT * FROM city_bid_evidence ORDER BY proof_sha256")],
        "city_native_bid_evidence": [_parse_json(row, "proof_json") for row in _rows(
            conn, "SELECT * FROM city_native_bid_evidence ORDER BY proof_sha256")],
        "city_document_alias_evidence": [_parse_json(row, "proof_json") for row in _rows(
            conn, "SELECT * FROM city_document_alias_evidence ORDER BY proof_sha256")],
    }
