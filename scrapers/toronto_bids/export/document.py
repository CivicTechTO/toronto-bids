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


def _ext(name: str | None) -> str | None:
    if not name:
        return None
    leaf = name.rsplit("/", 1)[-1]
    dot = leaf.rfind(".")
    return leaf[dot + 1:].lower() if dot != -1 else None


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
    # Staff reports join a solicitation through the bid-bridge (#126): an Ariba-era bid row
    # carries BOTH the council reference and the document_number, so the link is exact — no
    # fuzzy matching. Derived from `bid` at query time; the reference side is 1:1 (verified), so
    # a plain dict is exact and the ORDER BY makes any future many-to-one deterministic.
    bridge: dict[str, str] = {}
    for row in _rows(conn, "SELECT DISTINCT reference, document_number FROM bid "
                           "WHERE reference IS NOT NULL AND document_number IS NOT NULL "
                           "ORDER BY reference, document_number"):
        bridge[row["reference"]] = row["document_number"]
    # Pre-Ariba items have no dual-key bid to derive from; solicitation_link records the
    # (winner,value) match instead (#124). setdefault so a bid-derived bridge is never overridden.
    for row in _rows(conn, "SELECT reference, document_number FROM solicitation_link "
                           "ORDER BY reference"):
        bridge.setdefault(row["reference"], row["document_number"])
    for report in _rows(conn, "SELECT reference, url FROM background_pdf "
                              "WHERE kind='bgrd' ORDER BY reference, url"):
        doc = bridge.get(report["reference"])
        if doc in sol_docs:                           # attach only to a real solicitation
            name = report["url"].rsplit("/", 1)[-1]
            documents_by_doc.setdefault(doc, []).append({
                "source": "staff_report",
                "name": name,
                "path": name,
                "type": _ext(name),
                "size_bytes": None,
                "url": report["url"],
            })

    # Bids split by which identifier they carry. A bid with a council `reference` (Bid Award
    # Panel era) nests under its council item, below. A reference-null bid (Award Summary Form,
    # the post-panel successor #145) has no council item — it nests under its solicitation by
    # document_number, or, matching no solicitation, lands in unlinked_bids. Same
    # nested-or-unlinked contract as awards/postings — nothing is dropped.
    bids_by_ref: dict = {}
    bids_by_doc: dict[str, list] = {}
    unlinked_bids: list = []
    for bid in _rows(conn, "SELECT * FROM bid ORDER BY reference, document_number, bidder_name_raw, id"):
        cleaned = _drop(bid, "id")
        ref, doc = bid["reference"], bid["document_number"]
        bridged = bridge.get(ref) if ref is not None else None          # pre-Ariba: ref -> sol
        if ref is not None and bridged in sol_docs:
            bids_by_doc.setdefault(bridged, []).append(_drop(cleaned, "document_number"))   # under solicitation
        elif ref is not None:
            bids_by_ref.setdefault(ref, []).append(cleaned)             # under council item (unchanged)
        elif doc in sol_docs:
            bids_by_doc.setdefault(doc, []).append(_drop(cleaned, "document_number"))       # #145 reference-null
        else:
            unlinked_bids.append(cleaned)

    solicitations = []
    for sol in _rows(conn, "SELECT * FROM solicitation ORDER BY document_number"):
        sol = _drop(sol, "odata_id")
        doc = sol["document_number"]
        sol["awards"] = awards_by_doc.get(doc, [])
        sol["ariba_postings"] = postings_by_doc.get(doc, [])
        sol["documents"] = documents_by_doc.get(doc, [])
        sol["bids"] = bids_by_doc.get(doc, [])
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

    # Keep supplier_key: it is the frontend's only stable supplier permalink (#144). supplier_id
    # is rebuilt from scratch every sync, and display_name shifts as variants accrue.
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

    # Agency buyers (#135): a fourth keyspace, in its own section so no City-spine
    # count changes meaning. Partnered buyers carry their flag and funding share so a
    # consumer can segment — the TRCA scope decision is the reader's, made visible.
    buyers_out = []
    for buyer in _rows(conn, "SELECT * FROM buyer ORDER BY slug"):
        bid_ = buyer["id"]
        buyers_out.append(_drop(buyer, "id") | {
            "solicitations": [_drop(r, "id", "buyer_id") for r in _rows(
                conn, f"SELECT * FROM agency_solicitation WHERE buyer_id={bid_} "
                      "ORDER BY native_ref")],
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
    }
