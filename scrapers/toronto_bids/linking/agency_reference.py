"""Conservative normalization inside each agency's identifier space (#217)."""
import re
import uuid


def normalize_agency_reference(buyer_slug, raw):
    text = " ".join(str(raw or "").split()).strip()
    if text.lower() in {"", "unknown", "none", "null", "n/a", "not stated", "not provided",
                        "rfp", "rft", "rfq", "request for quotation", "new agreement"}:
        return None
    if re.match(r"https?://", text, re.I):
        return text  # report-URL fallbacks are case-sensitive identities
    # Portal GUIDs are opaque identifiers, not embedded eight-digit TRCA call numbers.
    try:
        uuid.UUID(text)
        return text.upper()
    except ValueError:
        pass
    if buyer_slug == "trca":
        refs = set(re.findall(r"(?<![A-Za-z0-9_])\d{8}(?:[_-]\d+)?(?![A-Za-z0-9_])", text))
        if len(refs) > 1:
            raise ValueError(f"ambiguous TRCA reference {text!r}")
        if refs:
            return refs.pop()
    elif buyer_slug == "exhibition-place":
        refs = set(re.findall(r"\bEP\d+-\d{4}\b", text.upper()))
        if len(refs) > 1:
            raise ValueError(f"ambiguous EP reference {text!r}")
        if refs:
            return refs.pop()
        match = re.fullmatch(r"Contract\s+(?:No\.?|#)\s*([0-9][0-9A-Za-z-]{3,})", text, re.I)
        if match:
            return match[1].upper()
    # Keep unfamiliar printed identifiers; normalization must not invent equivalences.
    return text.upper()


def report_reference(council_reference, url):
    """An item may identify a single contract; a meeting reference cannot identify a report."""
    ref = " ".join((council_reference or "").split()).upper()
    if re.fullmatch(r"\d{4}\.(?:EP|ZB)\d+\.\d+(?:\.\d+)*", ref):
        return ref
    return url


def normalize_stored_references(conn, buyer_slug, buyer_id, source):
    """Re-key only deterministic aliases, preserving row history inside the caller's transaction.

    These are identifier-format equivalences, never supplier/title/amount matching. When an
    alias collides with its canonical row, retain the canonical id and non-null values, fill
    missing fields from the alias, and retain the earliest first_seen/latest last_seen.
    """
    keys = {
        "agency_solicitation": ("buyer_id", "native_ref"),
        "agency_bid": ("buyer_id", "native_ref", "bidder_name_raw", "source"),
        "agency_award": ("buyer_id", "native_ref", "supplier_name_raw", "award_amount", "source"),
    }
    counts = {"normalized": 0, "merged": 0}
    for table, key in keys.items():
        ids = conn.execute(f"SELECT id FROM {table} WHERE buyer_id=? AND source=? ORDER BY id",
                           (buyer_id, source)).fetchall()
        for (row_id,) in ids:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
            if row is None:
                continue
            old = dict(row)
            try:
                ref = normalize_agency_reference(buyer_slug, old["native_ref"])
            except ValueError:
                continue  # unresolved identity stays held; never guess a merge
            if not ref or ref == old["native_ref"]:
                continue
            new = old | {"native_ref": ref}
            where = " AND ".join(f"{column} IS ?" for column in key)
            found = conn.execute(f"SELECT * FROM {table} WHERE {where}",
                                 tuple(new[column] for column in key)).fetchone()
            if found is None:
                conn.execute(f"UPDATE {table} SET native_ref=? WHERE id=?", (ref, row_id))
            else:
                canonical = dict(found)
                merged = {column: value if value is not None else old[column]
                          for column, value in canonical.items() if column != "id"}
                merged["first_seen"] = min(old["first_seen"], canonical["first_seen"])
                merged["last_seen"] = max(old["last_seen"], canonical["last_seen"])
                conn.execute(f"UPDATE {table} SET " + ", ".join(f"{c}=?" for c in merged)
                             + " WHERE id=?", (*merged.values(), canonical["id"]))
                conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
                counts["merged"] += 1
            counts["normalized"] += 1
    return counts
