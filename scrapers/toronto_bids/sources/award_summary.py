"""Award Summary Forms — the losing bidders, after the Bid Award Panel was abolished (#114).

By-law 766-2025 eliminated the Bid Award Panel effective 2025-10-01, and its agendas were the
only published record of who *lost*. `sources/bid_award_panel.py` therefore stops dead at
2025.BA151 (2025-09-25) and will never find another agenda — 891 cached pages is the complete
and final corpus.

The bidders did not stop. Every awarded contract over $500,000 now carries an **Award Summary
Form** PDF on the Toronto Bids Portal, and it publishes more than the panel ever did.

No browser. The portal's table is driven by `feis_solicitation_published` — the same OData
spine sources/odata.py already reads — and `secure.toronto.ca` is not Akamai-gated the way
TMMIS is. The PDF hangs off the record itself in `uploadedFilesStaff`, so the whole path is
plain HTTP. Parsing is handled by LLM extraction (#205).
"""

from urllib.parse import quote

from toronto_bids import config
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import BackgroundPdf
from toronto_bids.store import db

_DATA = (
    "https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/"
    "feis_solicitation_published"
)
_UPLOAD = "https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/"

_AWARDED_FILTER = (
    "Ready_For_Posting eq 'Yes' and Solicitation_Form_Type eq 'Awarded "
    "Contracts' and Awarded_Cancelled eq 'No'"
)
_PAGE = 500


def fetch_awarded_records(http, log=lambda _m: None) -> list:
    """Every awarded-contract record the portal's API will serve, paged. Plain HTTP."""
    out, skip = [], 0
    while True:
        page = http.get_json(
            f"{_DATA}?$format=application/json;odata.metadata=none&$count=true"
            f"&$skip={skip}&$top={_PAGE}&$filter={quote(_AWARDED_FILTER)}"
            f"&$orderby=Latest_Date_Awarded desc"
        )
        rows = page.get("value") or []
        out.extend(rows)
        total = page.get("@odata.count", len(out))
        skip += _PAGE
        log(f"    {min(skip, total)}/{total}")
        if skip >= total or not rows:
            return out


def award_summary_files(record: dict) -> list:
    """(url, name) for each Award Summary Form on a record."""
    out = []
    for f in record.get("uploadedFilesStaff") or []:
        bin_id = f.get("bin_id")
        if bin_id and "award summary" in str(f.get("name", "")).lower():
            out.append((_UPLOAD + bin_id, f.get("name")))
    return out


def download_award_summaries(conn, http, dest_dir=None, log=lambda _m: None) -> int:
    """Archive every Award Summary Form. Idempotent and resumable."""
    from toronto_bids.sources.council import download_pdf

    dest_dir = dest_dir if dest_dir is not None else config.AWARD_SUMMARY_DIR
    have = {
        r["url"]
        for r in conn.execute("SELECT url FROM background_pdf WHERE sha256 IS NOT NULL")
    }
    log("  award summary forms: querying the portal")
    records = fetch_awarded_records(http, log=log)
    wanted = [
        (url, name, rec)
        for rec in records
        for url, name in award_summary_files(rec)
        if url not in have
    ]
    log(f"  award summary forms to fetch: {len(wanted)}")
    stored = 0
    for i, (url, _name, rec) in enumerate(wanted, 1):
        try:
            info = download_pdf(http, url, dest_dir, layout=True)
            db.upsert_row(
                conn,
                BackgroundPdf(
                    url=url,
                    kind="award_summary",
                    reference=None,
                    document_number=normalize_document_number(
                        rec.get("Solicitation_Document_Number")
                    ),
                    local_path=info["local_path"],
                    sha256=info["sha256"],
                    text=info["text"],
                ),
                overwrite=True,
            )
            conn.commit()
            stored += 1
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            log(f"    skipped {url.rsplit('/', 1)[-1]}: {exc}")
        if i % 25 == 0:
            log(f"    {i}/{len(wanted)}")
    return stored


def store_award_summary_bids(conn, log=lambda _m: None) -> int:
    """Extract and backfill bid rows from cached LLM extractions (#205)."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "award_summary", log=log)
    return result["bids_written"]
