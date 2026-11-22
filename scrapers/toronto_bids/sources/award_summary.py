"""Download Award Summary Form PDFs from the Toronto Bids OData API (post-BAP, >= 2025-10-01)."""

from pathlib import Path
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
    """Page through awarded-contract records from the OData API."""
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
    """Return (url, name) pairs for Award Summary Form attachments on a record."""
    out = []
    for f in record.get("uploadedFilesStaff") or []:
        bin_id = f.get("bin_id")
        if bin_id and "award summary" in str(f.get("name", "")).lower():
            out.append((_UPLOAD + bin_id, f.get("name")))
    return out


def _held_award_summary_is_valid(row, log=lambda _m: None) -> bool:
    """Return False when a cached Award Summary Form on disk is corrupt/non-PDF so it can be recaptured."""
    from toronto_bids.sources.council import validate_pdf_file

    local_path = row["local_path"]
    if not local_path:
        return True
    path = Path(local_path)
    if not path.is_file():
        return row["text"] is not None
    try:
        # Validate structure; also run pdftotext when text is NULL to distinguish scanned PDFs from corrupt ones.
        validate_pdf_file(path, layout=True, check_text=(row["text"] is None))
        return True
    except ValueError as exc:
        log(f"    invalid cached form {row['url'].rsplit('/', 1)[-1]} ({exc}); scheduling recapture")
        return False


def download_award_summaries(conn, http, dest_dir=None, log=lambda _m: None, failures=None) -> int:
    """Download and archive uncached or invalid Award Summary Form PDFs. Idempotent."""
    from toronto_bids.sources.council import download_pdf

    dest_dir = dest_dir if dest_dir is not None else config.AWARD_SUMMARY_DIR
    have = set()
    for r in conn.execute(
        "SELECT url, kind, local_path, text FROM background_pdf WHERE sha256 IS NOT NULL"
    ):
        if r["kind"] != "award_summary" or _held_award_summary_is_valid(r, log=log):
            have.add(r["url"])
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
            bin_id = url.rsplit("/", 1)[-1]
            log(f"    skipped {bin_id}: {exc}")
            if failures is not None:
                failures.append((f"award_summary_download:{bin_id}", str(exc)))
        if i % 25 == 0:
            log(f"    {i}/{len(wanted)}")
    return stored


def store_award_summary_bids(conn, log=lambda _m: None, failures=None) -> int:
    """Extract and backfill bid rows from cached LLM extractions."""
    from toronto_bids.extraction import extract_and_backfill

    result = extract_and_backfill(conn, "award_summary", log=log, failures=failures)
    return result["bids_written"]
