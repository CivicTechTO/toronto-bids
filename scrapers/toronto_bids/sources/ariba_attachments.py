"""Archive the solicitation documents behind Ariba's "Respond" gate (#117).

The City posts every competitive solicitation to SAP Ariba Discovery, but the actual documents
— RFP parts, drawings, addenda, pricing forms, environmental assessments — live inside the
Sourcing event, downloadable only "as a participating Supplier", i.e. after clicking Respond.
The Discovery preview shows `Attachments (0)`; the files are genuinely not there (verified
across the corpus, #117). Respond registers our account as a participant — we never submit a
bid — and unlocks a server-zipped bundle of every document.

Authorized by PMMD (2026-07, on the City's own open-by-default policy). Two hard limits shape
the design, both observed live, not assumed:

  * Respond is DISABLED once a posting closes. So this only ever reaches solicitations OPEN at
    capture time — a recurring job, not a backfill. Whatever closes before we look is gone.
  * The bundle download hard-stops above 500 MB as a single zip (>100 MB only warns). Events
    over 500 MB are logged and skipped rather than silently truncated — per-Part download is
    the upgrade path, not built until an event needs it.

Two halves, split the way the rest of the package splits fetch from normalize:

  * PURE / testable (no browser, no network): index a downloaded bundle's central directory
    and store the manifest — `document_number_from_zip_name`, `index_zip`, `store_bundle`,
    `ingest_downloads`. This is the INDEX the DB holds; the bytes stay on disk under
    <DATA_DIR>/ariba/attachments/ and are never committed.
  * BROWSER-bound (headed Chromium behind the `council` extra, logged into a real supplier
    account from scrapers/.env): drive Respond -> Download Content -> Download Attachments and
    capture the zip — `login`, `capture_event`, `capture_attachments`.

Not part of `tb sync`. Run via `tb enrich-ariba-attachments`.
"""
import hashlib
import shutil
import zipfile
from pathlib import Path

from toronto_bids import config
from toronto_bids.linking.document_number import bridge_document_number
from toronto_bids.models import AribaAttachment
from toronto_bids.store import db

DISCOVERY_PREVIEW_URL = (
    "https://portal.us.bn.cloud.ariba.com/dashboard/public/appext/"
    "comsapsbncdiscoveryui#/RfxEvent/preview/{rfx_id}?anId=ANONYMOUS"
)
# Above this a single-zip download is refused by Ariba (the >100 MB warning is only advisory).
MAX_BUNDLE_MB = 500


# --- pure: manifest + storage -------------------------------------------------------------

def document_number_from_zip_name(name: str) -> str | None:
    """The 10-digit document number Ariba names a bundle after: `Doc5660182540.zip` -> `5660182540`.

    Reuses the spine's own `Doc##########` bridge, so a stray-digit filename can't fabricate a
    key: it matches the `Doc<10 digits>` token, not "strip everything non-digit".
    """
    return bridge_document_number(None, name)


def index_zip(zip_path) -> list[dict]:
    """Central-directory listing of a bundle: one dict per file, no decompression.

    file_size and CRC32 both come from the zip's central directory, so indexing a 160 MB
    bundle never inflates a single byte. Directory entries are dropped. CRC32 is rendered as
    the fixed 8-hex-digit string SQLite will store.
    """
    with zipfile.ZipFile(zip_path) as zf:
        return [
            {
                "filename": zi.filename,
                "file_size": zi.file_size,
                "crc32": format(zi.CRC & 0xFFFFFFFF, "08x"),
            }
            for zi in zf.infolist()
            if not zi.is_dir()
        ]


def sha256_of_file(path, _chunk=1 << 20) -> str:
    """Streamed sha256 so a 160 MB bundle never lands in memory whole."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_chunk), b""):
            h.update(block)
    return h.hexdigest()


def store_bundle(conn, zip_path, document_number: str, dest_dir=None) -> int:
    """Archive one event bundle under <dest_dir>/<Docnnnn>.zip and index every file in it.

    Idempotent: the canonical path is keyed on document_number, so re-storing the same event
    overwrites in place and the UNIQUE(document_number, filename) upserts refresh rather than
    duplicate. Returns the number of files indexed.
    """
    dest_dir = Path(dest_dir if dest_dir is not None else config.ARIBA_ATTACHMENTS_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    canonical = dest_dir / f"Doc{document_number}.zip"

    zip_path = Path(zip_path)
    if zip_path.resolve() != canonical.resolve():
        shutil.copy2(zip_path, canonical)

    zip_sha = sha256_of_file(canonical)
    entries = index_zip(canonical)
    for entry in entries:
        db.upsert_row(conn, AribaAttachment(
            document_number=document_number,
            filename=entry["filename"],
            file_size=entry["file_size"],
            crc32=entry["crc32"],
            zip_name=canonical.name,
            zip_sha256=zip_sha,
        ), overwrite=True)
    conn.commit()
    return len(entries)


def ingest_downloads(conn, source_dir, dest_dir=None, log=lambda _m: None) -> int:
    """Index every `Doc*.zip` sitting in source_dir (e.g. a browser's download folder).

    The manual path and the scraper's own post-download step share this: the browser half
    saves a bundle, then hands it here. A zip whose name yields no document number is skipped
    loudly, never guessed. Returns the number of bundles ingested.
    """
    source_dir = Path(source_dir)
    ingested = 0
    for zip_path in sorted(source_dir.glob("Doc*.zip")):
        document_number = document_number_from_zip_name(zip_path.name)
        if document_number is None:
            log(f"  skipped {zip_path.name}: no Doc########## in the name")
            continue
        n = store_bundle(conn, zip_path, document_number, dest_dir)
        log(f"  {zip_path.name}: {n} files -> Doc{document_number}")
        ingested += 1
    return ingested


# --- browser: log in and capture ----------------------------------------------------------

def open_solicitation_events(conn) -> list[dict]:
    """The still-open, modern-linked solicitations whose Respond is (probably) still live.

    submission_deadline in the future is the best signal the spine carries for "still open";
    Respond being disabled on the page is the real gate, and capture_event re-checks it there.
    Only the modern `RfxEvent/preview/<id>` links carry an rfx id we can drive.
    """
    from toronto_bids.linking.ariba import rfx_id_from_link
    rows = conn.execute(
        "SELECT document_number, ariba_posting_link FROM solicitation "
        "WHERE submission_deadline >= date('now') "
        "AND ariba_posting_link LIKE '%RfxEvent/preview/%' "
        "ORDER BY submission_deadline"
    ).fetchall()
    events = []
    for row in rows:
        rfx = rfx_id_from_link(row["ariba_posting_link"])
        if rfx and row["document_number"]:
            events.append({"rfx_id": rfx, "document_number": row["document_number"]})
    return events


def login(page, username: str, password: str, log=lambda _m: None) -> None:
    """Sign the headed browser into the supplier account so Respond reaches the event.

    Credentials come from scrapers/.env (never the repo). The account has no MFA — if that
    changes, this step lands on a challenge page and raises rather than hanging: an unattended
    login cannot answer a 2FA prompt, and a CAPTCHA is a hard stop by policy.
    """
    page.goto(config.ARIBA_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    page.fill("input[name='UserName']", username)
    page.fill("input[name='Password']", password)
    page.click("button:has-text('Login'), input[type='submit']")
    page.wait_for_load_state("networkidle", timeout=60000)
    body = page.inner_text("body").lower()
    if "two-factor" in body or "verification code" in body or "captcha" in body:
        raise RuntimeError(
            "Ariba presented an MFA/CAPTCHA challenge; unattended login cannot proceed. "
            "Re-authenticate manually or disable 2FA on the archival account.")
    if "incorrect" in body or "invalid" in body and "password" in body:
        raise RuntimeError("Ariba rejected the credentials in scrapers/.env.")
    log("  logged in")


def capture_event(page, event: dict, dest_dir, log=lambda _m: None) -> Path | None:
    """Drive one open event through Respond -> Download Content -> Download Attachments.

    Returns the saved bundle path, or None if the event could not be captured (Respond
    disabled = already closed; bundle over the 500 MB single-zip ceiling). Never raises for
    those expected outcomes — the caller isolates real errors per event.
    """
    rfx_id, document_number = event["rfx_id"], event["document_number"]
    page.goto(DISCOVERY_PREVIEW_URL.format(rfx_id=rfx_id),
              wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(f"text=ID - {rfx_id}", timeout=45000)

    respond = page.get_by_role("button", name="Respond", exact=True)
    if respond.is_disabled():
        log(f"  Doc{document_number}: Respond disabled (closed) — skipped")
        return None

    respond.click()
    page.wait_for_url("**/Sourcing/**", timeout=60000)     # Discovery -> Ariba Sourcing

    # Download Content -> the Export-to-Excel page -> Download Attachments -> the picker.
    page.get_by_role("button", name="Download Content").click()
    page.get_by_role("button", name="Download Attachments").first.click()
    page.wait_for_selector("text=Selected Attachments Summary", timeout=45000)

    # Select every item; the header checkbox is the first checkbox on the picker.
    page.locator("input[type='checkbox']").first.check()
    page.wait_for_timeout(1500)                            # totals recompute after select-all

    total_mb = _selected_total_mb(page)
    if total_mb is not None and total_mb > MAX_BUNDLE_MB:
        # ponytail: single-zip only; per-Part download is the upgrade path when one is needed.
        log(f"  Doc{document_number}: bundle {total_mb:.0f} MB > {MAX_BUNDLE_MB} MB single-zip "
            f"limit — skipped, needs per-Part capture")
        return None

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"Doc{document_number}.zip"
    # The server assembles the zip ("Processing request …") before the download starts, so the
    # wait is generous; expect_download resolves when the stream begins, not when it finishes.
    with page.expect_download(timeout=300000) as dl:
        page.get_by_role("button", name="Download Attachments").last.click()
    dl.value.save_as(str(target))
    log(f"  Doc{document_number}: captured {target.name}")
    return target


def _selected_total_mb(page) -> float | None:
    """The 'Total Size (MB): N' the picker shows once items are selected, or None if unread."""
    import re
    text = page.inner_text("body")
    match = re.search(r"Total Size \(MB\):\s*([\d,.]+)", text)
    return float(match.group(1).replace(",", "")) if match else None


def capture_attachments(conn, dest_dir=None, log=lambda _m: None, headless=False) -> int:
    """Log in, walk every open solicitation, capture and index each bundle. Resumable.

    A bundle already on disk is not re-downloaded — the expensive half is the download, and
    Respond is idempotent (re-responding just re-opens the event). One event's failure is
    logged and never ends the run, exactly as pipeline.run_source isolates a source.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Ariba attachment capture needs the optional 'council' extra. "
            "Install it with: uv sync --extra council && uv run playwright install chromium"
        ) from exc

    if not (config.ARIBA_USERNAME and config.ARIBA_PASSWORD):
        raise RuntimeError(
            "ARIBA_USERNAME / ARIBA_PASSWORD are unset. Put them in scrapers/.env "
            "(gitignored — the repo is public).")

    dest_dir = Path(dest_dir if dest_dir is not None else config.ARIBA_ATTACHMENTS_DIR)
    events = open_solicitation_events(conn)
    pending = [e for e in events if not (dest_dir / f"Doc{e['document_number']}.zip").exists()]
    log(f"  open events: {len(events)}  already archived: {len(events) - len(pending)}  "
        f"to capture: {len(pending)}")
    if not pending:
        return 0

    captured = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless, args=["--disable-blink-features=AutomationControlled"])
        try:
            page = browser.new_context(accept_downloads=True).new_page()
            login(page, config.ARIBA_USERNAME, config.ARIBA_PASSWORD, log=log)
            for i, event in enumerate(pending, 1):
                try:
                    saved = capture_event(page, event, dest_dir, log=log)
                    if saved is not None:
                        store_bundle(conn, saved, event["document_number"], dest_dir)
                        captured += 1
                except Exception as exc:
                    log(f"  Doc{event['document_number']}: FAILED — {exc}")
                log(f"    {i}/{len(pending)}")
        finally:
            browser.close()
    return captured
