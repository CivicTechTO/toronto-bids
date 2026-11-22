"""Conservative per-agency reference normalization."""
import re
import uuid


_TCHC_RFX = re.compile(
    r"\b(RF(?:P|Q|VQ|PQ|T|R|S|QL|EOI|I))[\s-]*"
    r"([0-9]{4,5}(?:\s*-\s*[0-9]+)?|RP-?[0-9]+|[0-9]{2}-[0-9]{3})"
    r"(?:[\s-]+(PP))?\b",
    re.I,
)
_TPL_MODERN_RFX = re.compile(
    r"\b(\d{2,4}-\d{2}-TPL-RF(?:P|T|Q|SQ|VQ|PQ|I|EOI))\b",
    re.I,
)
_TPL_LEGACY_RFX = re.compile(r"\bTPL[\s-]+(\d{2,4}-20\d{2})\b", re.I)
_TPS_RFX = re.compile(r"\b(\d{8})\s*(?:[-–—]|â\x80\x93)\s*(\d{2})\b")


def normalize_agency_reference(buyer_slug, raw):
    text = " ".join(str(raw or "").split()).strip()
    if text.lower() in {"", "unknown", "none", "null", "n/a", "not stated", "not provided",
                        "not applicable", "rfp", "rft", "rfq", "request for quotation",
                        "new agreement", "direct award", "sole source", "single source",
                        "change order", "non-competitive"}:
        return None
    if re.match(r"https?://", text, re.I):
        return text  # Report-URL fallbacks are case-sensitive.
    # Portal GUIDs are opaque; do not match embedded 8-digit TRCA numbers.
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
    elif buyer_slug == "toronto-community-housing":
        refs = set()
        for prefix, num, suffix in _TCHC_RFX.findall(text):
            clean_num = re.sub(r"\s*-\s*", "-", num.upper())
            if clean_num.startswith("RP") and not clean_num.startswith("RP-"):
                clean_num = "RP-" + clean_num[2:]
            canon = f"{prefix.upper()} {clean_num}"
            if suffix:
                canon += f"-{suffix.upper()}"
            refs.add(canon)
        if len(refs) > 1:
            raise ValueError(f"ambiguous TCHC reference {text!r}")
        if refs:
            return refs.pop()
    elif buyer_slug == "toronto-public-library":
        refs = {m.upper() for m in _TPL_MODERN_RFX.findall(text)}
        refs.update(f"TPL {m.upper()}" for m in _TPL_LEGACY_RFX.findall(text))
        if len(refs) > 1:
            raise ValueError(f"ambiguous TPL reference {text!r}")
        if refs:
            return refs.pop()
    elif buyer_slug == "toronto-police-service":
        refs = {f"{num}-{yr}" for num, yr in _TPS_RFX.findall(text)}
        if len(refs) > 1:
            raise ValueError(f"ambiguous TPS reference {text!r}")
        if refs:
            return refs.pop()
    return text.upper()


def report_reference(council_reference, url):
    """Return an uppercase council item reference if present, else the report URL."""
    ref = " ".join((council_reference or "").split()).upper()
    if re.fullmatch(r"\d{4}\.(?:EP|ZB|PA)\d+\.\d+(?:\.\d+)*", ref):
        return ref
    return url


def normalize_stored_references(conn, buyer_slug, buyer_id, source, *, proven_aliases=()):
    """Re-key deterministic reference aliases inside the caller's transaction.

    Merges colliding rows by keeping canonical non-null values, filling NULLs from the
    alias, and spanning min(first_seen)/max(last_seen). Reviewed proven_aliases apply only
    to agency_bid and agency_award rows matching report_url and (name, amount).
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
                continue  # Keep ambiguous references unchanged.
            if table in ('agency_bid', 'agency_award'):
                kind, name, amount = (('bids', 'bidder_name_raw', 'bid_price') if table == 'agency_bid'
                                      else ('awards', 'supplier_name_raw', 'award_amount'))
                proven = {a['native_ref'] for a in proven_aliases
                          if a['old_native_ref'] == old['native_ref'] and a['report_url'] == old['report_url']
                          and (old[name], old[amount]) in a[kind]}
                if len(proven) > 1:
                    raise ValueError('conflicting reviewed agency history aliases')
                if proven:
                    ref = proven.pop()
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
