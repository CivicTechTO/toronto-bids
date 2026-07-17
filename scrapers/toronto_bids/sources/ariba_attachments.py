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
import io
import shutil
import zipfile
from pathlib import Path

from toronto_bids import config
from toronto_bids.linking.document_number import bridge_document_number
from toronto_bids.models import AribaAttachment
from toronto_bids.store import db

# The AUTHENTICATED preview path — no `/public/`, no `?anId=ANONYMOUS`. The anonymous URL does
# not reliably carry the logged-in session, so Respond there pops a "Register/Login" modal
# instead of opening the Sourcing event (the whole source of the earlier flakiness). This host
# holds the session cookie set at login, so the authed path shows a working Respond.
DISCOVERY_PREVIEW_URL = (
    "https://portal.us.bn.cloud.ariba.com/dashboard/appext/"
    "comsapsbncdiscoveryui#/RfxEvent/preview/{rfx_id}"
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


_MAX_ZIP_DEPTH = 8
_MAX_ZIP_ENTRIES = 10000


def index_zip(zip_path) -> list[dict]:
    """Recursive central-directory listing of a bundle: one dict per LEAF file.

    Nested zips are descended to any depth (a bundle's real documents often live inside
    "Appendix ….zip"), each leaf carrying the full nested `path`. Sizes and CRC32 come from
    each level's central directory; a nested zip must be read (inflated) to reach its own
    directory, so depth and a per-bundle entry budget bound zip bombs. A nested zip that is
    empty, corrupt, encrypted, or past the depth cap degrades to a single leaf rather than
    being lost. The entry budget (`_MAX_ZIP_ENTRIES`) is a different, harder backstop: once it
    hits zero every remaining entry — including one that would otherwise become a nested-zip
    leaf — is skipped outright, not indexed in any form. That is a deliberate truncation for a
    pathological bundle, not a leaf fallback.
    """
    with zipfile.ZipFile(zip_path) as zf:
        return _index_zipfile(zf, prefix="", depth=0, budget=[_MAX_ZIP_ENTRIES])


def _index_zipfile(zf, prefix: str, depth: int, budget: list) -> list[dict]:
    out = []
    for zi in zf.infolist():
        if zi.is_dir() or budget[0] <= 0:
            continue                                   # budget exhausted: hard stop, skip outright
        path = prefix + zi.filename
        if zi.filename.lower().endswith(".zip") and depth < _MAX_ZIP_DEPTH:
            try:
                # ponytail: caps bound zip count/depth, not per-entry inflated size; add a size
                # cap if a real bundle ever needs it
                with zipfile.ZipFile(io.BytesIO(zf.read(zi.filename))) as nested:
                    children = _index_zipfile(nested, path + "/", depth + 1, budget)
                if children:                       # expandable: contribute its leaves, not it
                    out.extend(children)
                    continue
                # empty zip falls through and is kept as a leaf, so nothing is silently dropped
            except (zipfile.BadZipFile, RuntimeError, OSError):
                pass                               # corrupt/encrypted: keep as an opaque leaf
        out.append({
            "filename": zi.filename,
            "path": path,
            "file_size": zi.file_size,
            "crc32": format(zi.CRC & 0xFFFFFFFF, "08x"),
        })
        budget[0] -= 1
    return out


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

    Credentials come from scrapers/.env (never the repo). SAP's supplier sign-in is TWO steps —
    username (`#userid`) then password (`#Password`), split by a `.next-button-text` link (an
    `<a>`, not a `<button>`, so role locators miss it). The page re-renders once just after load
    and wipes an early fill, so we wait for network-idle plus a settle, then verify the value
    stuck and re-enter it if not — selectors and this race were both read off the live page.

    The account has no MFA — if that changes, this lands on a challenge page and raises rather
    than hanging: an unattended login cannot answer a 2FA prompt, and a CAPTCHA is a policy stop.
    """
    # Step 1 (username -> Next) is flaky: SAP rotates a CSRF token on a re-render just after
    # load, and a submit that races it bounces back to a fresh username page. Reloading the
    # whole page gives a fresh token, so retry the entire step — reload, wait for the URL to
    # STOP rotating (the tell that the re-render settled), fill, submit — rather than re-poking
    # a page mid-rotation. Selectors (#userid, the <a> around .next-button-text, #Password) and
    # this race were all read off the live page.
    for attempt in range(5):
        page.goto(config.ARIBA_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#userid", state="visible", timeout=30000)
        _wait_url_stable(page)                        # let the token-rotation reload finish
        page.fill("#userid", username)
        if page.input_value("#userid") != username:
            continue
        page.click("a:has(.next-button-text)")
        try:
            page.wait_for_selector("#Password", state="visible", timeout=15000)
            break
        except Exception:
            continue                                  # bounced — reload for a fresh token
    else:
        raise RuntimeError("Could not reach the Ariba password step (username step kept bouncing).")

    page.fill("#Password", password)
    page.press("#Password", "Enter")

    # The supplier dashboard polls, so it never reaches network-idle — wait on the sign-in form
    # leaving instead. On success the whole document navigates and #Password detaches; on
    # failure it stays put and the wait times out, which the checks below then explain.
    try:
        page.wait_for_selector("#Password", state="detached", timeout=45000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    _dismiss_cookie_banner(page)

    body = page.inner_text("body").lower()
    if "verification code" in body or "two-factor" in body or "captcha" in body:
        raise RuntimeError(
            "Ariba presented an MFA/CAPTCHA challenge; unattended login cannot proceed. "
            "Re-authenticate manually or disable 2FA on the archival account.")
    # Still on the sign-in page (password field present) means the credentials were rejected.
    if page.query_selector("#Password") is not None:
        raise RuntimeError("Ariba did not accept the sign-in; check the credentials in scrapers/.env.")
    log("  logged in")


def _wait_url_stable(page, checks: int = 4, interval: int = 800) -> None:
    """Block until the URL stops changing — the sign-in page rotates its CSRF token via a
    re-render right after load, and its awssk query param changes each time. A URL unchanged
    across two polls means that settled and it is safe to fill the form."""
    last = page.url
    stable = 0
    for _ in range(checks):
        page.wait_for_timeout(interval)
        if page.url == last:
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 0
            last = page.url


def _dismiss_cookie_banner(page) -> None:
    """Decline non-essential cookies if SAP shows the consent dialog — the privacy-preserving
    choice, and it otherwise overlays the buttons the capture flow needs to click.

    Labels are matched EXACTLY and kept to unambiguous consent wording. A loose "Decline"
    match once hit the event's "Decline to Respond" button, which withdraws participation —
    never widen these to a substring that a destructive event control could satisfy.
    """
    for label in ("Deny All", "Reject All"):
        try:
            btn = page.get_by_role("button", name=label, exact=True)
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            pass


def capture_event(page, event: dict, dest_dir, log=lambda _m: None) -> Path | None:
    """Drive one open event through Respond -> Download Content -> Download Attachments.

    Returns the saved bundle path, or None if the event could not be captured (Respond
    disabled = already closed; bundle over the 500 MB single-zip ceiling). Never raises for
    those expected outcomes — the caller isolates real errors per event.
    """
    rfx_id, document_number = event["rfx_id"], event["document_number"]
    if not _open_authed_preview(page, rfx_id):
        raise RuntimeError(
            f"Doc{document_number}: the authenticated event preview never loaded "
            f"(rfx {rfx_id}) — session/SSO did not settle.")

    respond = page.get_by_role("button", name="Respond", exact=True)
    if respond.is_disabled():
        log(f"  Doc{document_number}: Respond disabled (closed) — skipped")
        return None
    respond.click()

    # Respond opens the Sourcing event, but some events refuse access even so — invite-only, or
    # tied to a different account — and Ariba shows "You do not have the correct permission to
    # view the event". Those are a clean skip, not a failure. Poll for the event's Download
    # Content button, that denial, or the anonymous Register/Login modal, whichever lands first.
    download_content = page.get_by_role("button", name="Download Content")
    outcome = _wait_post_respond(page, download_content)
    if outcome == "denied":
        log(f"  Doc{document_number}: no permission to view the event — skipped")
        return None
    if outcome == "anonymous":
        raise RuntimeError(
            f"Doc{document_number}: Ariba served the anonymous view (Register/Login modal); "
            f"the session did not carry to the preview.")
    if outcome != "event":
        raise RuntimeError(f"Doc{document_number}: the Sourcing event never loaded after Respond.")
    _dismiss_cookie_banner(page)

    # Download Content -> the Export-to-Excel page -> Download Attachments -> the picker. The
    # first Download Content click is sometimes a no-op if the event page is still settling, so
    # wait for the export page's Download Attachments button and retry the click if it does not
    # appear rather than clicking blindly into the event page.
    dl_attachments = page.get_by_role("button", name="Download Attachments")
    download_content.click()
    try:
        dl_attachments.first.wait_for(state="visible", timeout=30000)
    except Exception:
        download_content.click()                          # no-op first click — try once more
        dl_attachments.first.wait_for(state="visible", timeout=30000)
    dl_attachments.first.click()
    page.wait_for_selector("text=Selected Attachments Summary", timeout=45000)
    _select_all_attachments(page, log=log)

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


def _open_authed_preview(page, rfx_id: str, attempts: int = 3) -> bool:
    """Load the event preview in the AUTHENTICATED Discovery app, returning True once it shows.

    The first navigation to the authed URL triggers an SSO redirect that consumes the
    `#/RfxEvent/preview/<id>` fragment and lands on the app shell (no event). Navigating again,
    with SSO now settled, routes to the event — verified live: nav1 shows nothing, nav2 shows
    the event with an enabled Respond. So retry until the `ID - <rfx>` marker appears, dismissing
    the per-origin cookie banner each pass.
    """
    for _ in range(attempts):
        page.goto(DISCOVERY_PREVIEW_URL.format(rfx_id=rfx_id),
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)                       # let the SSO redirect chain settle
        _dismiss_cookie_banner(page)
        if page.query_selector(f"text=ID - {rfx_id}") is not None:
            return True
    return False


_PERMISSION_DENIED = "do not have the correct permission to view the event"


def _wait_post_respond(page, download_content, timeout_ms: int = 60000) -> str:
    """After Respond, report which page landed: 'event', 'denied', 'anonymous', or 'timeout'.

    The redirect chain varies and three outcomes are all normal-ish: the Sourcing event (has a
    Download Content button), an access denial (invite-only / wrong account), or the anonymous
    Register/Login modal. Poll for whichever appears rather than assuming the event and hanging
    the full timeout on the two that never show a Download Content button.
    """
    waited = 0
    while waited < timeout_ms:
        try:
            if download_content.count() and download_content.first.is_visible():
                return "event"
            body = (page.inner_text("body") or "").lower()
            if _PERMISSION_DENIED in body:
                return "denied"
            if page.get_by_role("button", name="Register/Login").count():
                return "anonymous"
        except Exception:
            pass                                          # mid-navigation DOM churn — retry
        page.wait_for_timeout(1000)
        waited += 1000
    return "timeout"


def _select_all_attachments(page, log=lambda _m: None) -> None:
    """Tick the picker's header checkbox to select every file, and verify it took.

    The widget is `<div class="w-chk-container"><input class="w-chk-native"><label
    class="w-chk"></label></div>`. The real <input> is hidden and empty of size; the visible box
    is the CSS-drawn sibling `<label class="w-chk">`, and AribaWeb's select-all action fires on a
    real positional click there (a Playwright `.click()` on the empty label only FOCUSES it, and
    setting the input's checked flag skips the cascade that ticks every row). So click at the
    widget's bounding-box centre with the mouse — the first checkbox is the header select-all —
    and confirm it took by reading the size total (a silent no-select downloads an empty bundle).
    """
    def mouse_click_first(selector):
        box = page.locator(selector).first.bounding_box()
        if not box:
            raise RuntimeError(f"no bounding box for {selector}")
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    def checked_count():
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('input.w-chk-native'))"
            ".filter(e => e.checked).length")

    strategies = (
        ("mouse-label", lambda: mouse_click_first("label.w-chk")),
        ("mouse-container", lambda: mouse_click_first("div.w-chk-container")),
        ("label-click", lambda: page.locator("label.w-chk").first.click(timeout=6000)),
    )
    for name, attempt in strategies:
        try:
            attempt()
            page.wait_for_timeout(3000)                   # the cascade recomputes over an AJAX call
            # Verify by counting ticked rows, not by parsing the total: the header cascade ticks
            # every row, so >1 checked box means it took. Parsing the size total proved brittle
            # (label and value live in different columns), and a silent no-select is worse.
            if checked_count() > 1:
                log(f"    select-all via {name}")
                return
        except Exception:
            pass
    raise RuntimeError("Could not select the attachments (header checkbox did not register).")


def _selected_total_mb(page) -> float | None:
    """The 'Total Size (MB): N' the picker shows once items are selected, or None if unread.

    `inner_text` reorders label and value (they sit in separate columns) so the number never
    follows the colon in the flat rendered text. Scan raw `textContent` in DOM order instead:
    on the smallest element that wraps both, "Total Size (MB):" and "161.76" are adjacent, so
    match them together on whichever element that is (the deepest match wins by scanning all).
    """
    return page.evaluate(
        """() => {
            let best = null;
            for (const e of document.querySelectorAll('td,div,span,label,p,tr,table,body')) {
                const m = (e.textContent || '').match(/Total Size \\(MB\\):\\s*([\\d,]+(?:\\.\\d+)?)/);
                if (m) best = parseFloat(m[1].replace(/,/g, ''));
            }
            return best;
        }"""
    )


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
