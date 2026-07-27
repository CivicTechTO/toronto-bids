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
  * The bundle download hard-stops above 500 MB as a single zip (>100 MB only warns). Ariba's
    own advice there is "select specific items and perform multiple downloads", and that is
    what happens: an oversized event is captured in BATCHES (`ariba_batch`, #174), each under
    the ceiling, merged into the canonical `Doc<n>.zip` only once every planned row is captured
    or recorded un-capturable. Nothing is silently truncated, and an incomplete capture stays
    pending rather than being canonicalised.

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
import re
import shutil
import zipfile
from pathlib import Path

from toronto_bids import config
from toronto_bids.linking.document_number import bridge_document_number
from toronto_bids.models import AribaAttachment
from toronto_bids.sources import ariba_batch
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
                with zipfile.ZipFile(io.BytesIO(zf.read(zi))) as nested:
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
    overwrites the bytes in place, and the document's rows are deleted then re-inserted from the
    freshly indexed bytes — a rebuild, not an upsert, so a leaf that no longer exists (e.g. one
    from before recursion) cannot survive a re-store. Returns the number of files indexed.
    """
    dest_dir = Path(dest_dir if dest_dir is not None else config.ARIBA_ATTACHMENTS_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    canonical = dest_dir / f"Doc{document_number}.zip"

    zip_path = Path(zip_path)
    if zip_path.resolve() != canonical.resolve():
        shutil.copy2(zip_path, canonical)

    zip_sha = sha256_of_file(canonical)
    entries = index_zip(canonical)
    # Rebuild this document's index from the bytes: ariba_attachment is a derived index of the
    # on-disk zips (like the supplier dimension), so clear the document's rows — dropping any
    # stale top-level-only rows from before recursion — then insert the current recursive set.
    conn.execute("DELETE FROM ariba_attachment WHERE document_number = ?", (document_number,))
    for entry in entries:
        db.upsert_row(conn, AribaAttachment(
            document_number=document_number,
            filename=entry["filename"],
            path=entry["path"],
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


def reindex_bundles(conn, dest_dir=None, log=lambda _m: None) -> int:
    """Rebuild the index from the bundles already in the store. Offline, no browser.

    ariba_attachment is a derived index of the on-disk zips, so it can be regenerated whenever the
    indexing changes (e.g. #123's recursion). Just re-ingest the store into itself — store_bundle
    skips the copy when the source is already the canonical path and rebuilds the rows from the
    bytes. Idempotent. Returns the number of bundles reindexed.
    """
    root = dest_dir if dest_dir is not None else config.ARIBA_ATTACHMENTS_DIR
    return ingest_downloads(conn, root, root, log=log)


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

    Returns the saved bundle path, or None if the event could not be captured this run (Respond
    disabled = already closed; an incomplete batched capture that will resume next run). Never
    raises for those expected outcomes — the caller isolates real errors per event.

    A bundle over the 500 MB single-zip ceiling is NOT skipped: it is captured in batches
    (`ariba_batch.capture_in_batches`, #174), which resumes across runs and writes the canonical
    `Doc<n>.zip` only on completion.
    """
    rfx_id, document_number = event["rfx_id"], event["document_number"]
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not _open_authed_preview(page, rfx_id):
        raise RuntimeError(
            f"Doc{document_number}: the authenticated event preview never loaded "
            f"(rfx {rfx_id}) — session/SSO did not settle.")

    respond = page.get_by_role("button", name="Respond", exact=True)
    if respond.is_disabled():
        # Respond dies the moment a posting closes, so any batches we already hold can never
        # be completed. Merging 3 of 5 is permanently better than nothing -- this is the whole
        # reason partial captures are retained (#174).
        #
        # But salvage is DESTRUCTIVE in one direction: finalise_partial writes the canonical
        # Doc<n>.zip, and capture_attachments then treats the event as archived forever. This is
        # a single-page app that renders Respond disabled until the event data lands, so ONE
        # instantaneous read is not evidence a posting closed. Two guards, cheapest first:
        # nothing to salvage means nothing a spurious read can damage, so skip without paying
        # for the confirmation wait; and where partials do exist, require the disabled state to
        # hold across several reads before canonicalising anything.
        pdir = ariba_batch.partial_dir(dest_dir, document_number)
        if not pdir.exists():
            log(f"  Doc{document_number}: Respond disabled (closed) — skipped")
            return None
        if _respond_stably_disabled(page, respond):
            # posting_open=False is the assertion, not a formality: finalise_partial refuses to
            # canonicalise a capture that could still complete, and this branch is the one place
            # that knows the posting is closed.
            salvaged = ariba_batch.finalise_partial(
                document_number, dest_dir, posting_open=False, log=log)
            if salvaged is not None:
                log(f"  Doc{document_number}: closed mid-capture — salvaged what we had")
                return salvaged
            log(f"  Doc{document_number}: Respond disabled (closed) — skipped")
            return None
        # Disabled once, enabled on a later read: the page was still settling. Fall through and
        # capture normally — the partials stay partials and resume below.
        log(f"  Doc{document_number}: Respond read disabled once then enabled — treating the "
            f"posting as open, partials kept")
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
    page.wait_for_selector(f"text={PICKER_HEADING}", timeout=45000)
    _select_all_attachments(page, log=log)

    total_mb = _selected_total_mb(page)
    if total_mb is not None and total_mb > MAX_BUNDLE_MB:
        # Ariba disables its own Download button over 500 MB and says "select specific items
        # and perform multiple downloads" -- so do exactly that (#174).
        log(f"  Doc{document_number}: bundle {total_mb:.0f} MB > {MAX_BUNDLE_MB} MB — "
            f"capturing in batches")
        picker = AribaPicker(page, log=log)
        # Read while everything from _select_all_attachments is still selected -- these are the
        # only values that distinguish a complete row_keys() enumeration from a short one, and
        # a complete merge from one missing files.
        expected_count = picker.selected_count()
        file_count = picker.file_count()
        if file_count is None:
            # `file_count` lands in the fingerprint, and the fingerprint is compared verbatim
            # against the one the partials were planned under: a run that reads the count and a
            # run that does not would disagree, and a disagreement DISCARDS every downloaded
            # batch. So an unreadable count refuses the whole batched capture for this run
            # rather than entering the fingerprint as a guess -- partials on disk are untouched
            # and the event simply stays pending (#174).
            log(f"  Doc{document_number}: could not read the picker's 'Total Number' — "
                f"refusing to plan a batched capture against an unknown file count")
            return None
        fingerprint = ariba_batch.make_fingerprint(
            picker.row_keys(expected_count=expected_count), file_count, total_mb)
        # The batching loop accumulates from an empty picker; the header-checkbox toggle is
        # ~10s versus ~2 minutes of deselecting rows one at a time, and raises if it doesn't
        # actually land on zero.
        picker.clear_selection()
        # Reached only past the Respond check above, which concluded the posting is OPEN (either
        # enabled outright, or disabled on one read and enabled on the next) -- that is what lets
        # capture_in_batches discard partials it cannot identify.
        return ariba_batch.capture_in_batches(
            picker, document_number, dest_dir, fingerprint, posting_open=True, log=log)

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


def _respond_stably_disabled(page, respond, reads: int = 3, interval_ms: int = 1500) -> bool:
    """Whether Respond is disabled on several reads a short wait apart, not just once.

    Only called where partials exist, because the answer decides whether to CANONICALISE an
    incomplete capture (see capture_event): the Discovery preview is a single-page app that
    renders its buttons disabled until the event data lands, so one instantaneous `is_disabled()`
    is a state the page passes THROUGH, not a fact about the posting. Agreement across reads is
    the cheapest evidence that separates the two.

    A read that raises (the button re-rendering out from under the locator mid-poll) is not
    evidence of a closed posting either, so it answers False. That errs toward keeping the
    partials and retrying next run — the recoverable direction.
    """
    for _ in range(max(reads - 1, 1)):
        page.wait_for_timeout(interval_ms)
        try:
            if not respond.is_disabled():
                return False
        except Exception:
            return False
    return True


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


def _select_all_attachments(page, log=lambda _m: None, timeout_ms: int = 90000) -> None:
    """Tick the picker's header checkbox to select every file, and wait for the cascade to land.

    The widget is `<div class="w-chk-container"><input class="w-chk-native"><label
    class="w-chk"></label></div>`. The real <input> is hidden and empty of size; the visible box
    is the CSS-drawn sibling `<label class="w-chk">`, and AribaWeb's select-all action fires on a
    real positional click there (a Playwright `.click()` on the empty label only FOCUSES it, and
    setting the input's checked flag skips the cascade that ticks every row). So click at the
    widget's bounding-box centre with the mouse — the first checkbox is the header select-all.

    **The cascade is an AJAX call whose duration scales with the item count, so POLL for it —
    never sleep a guess (#174).** This slept a flat 3000ms and then counted. Measured live on
    Doc5713434353 (51 attachments): the header ticks instantly, the other 50 land at **~10.3s**.
    So the count was read mid-cascade, a working click looked like a dead checkbox, and the loop
    then ran the *next* strategy — whose click toggled the header back off and restarted the
    cascade. Three strategies x 3s meant that event could never be captured, while the 49
    smaller ones cascaded inside 3s and always were. The bug scaled with the event, which is
    exactly why it looked like one broken posting rather than a broken wait.

    Verify by counting ticked rows, not by parsing the size total: the header cascade ticks every
    row, so >1 checked box means it took. Parsing the total proved brittle (label and value live
    in different columns) and a silent no-select downloads an empty bundle.
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
    poll_ms = 500
    # A click that lands ticks the HEADER almost at once; only the row cascade is slow. So a
    # click still showing zero ticked after this grace never registered — that, and only that,
    # is grounds to try the next strategy. Falling through while a cascade is in flight is the
    # #174 bug itself.
    grace_ms = 3000

    for name, attempt in strategies:
        try:
            attempt()
        except Exception:
            continue                                      # e.g. no bounding box — try the next
        waited = 0
        while waited < timeout_ms:
            page.wait_for_timeout(poll_ms)
            waited += poll_ms
            count = checked_count()
            if count > 1:
                log(f"    select-all via {name} (cascade landed in {waited / 1000:.0f}s)")
                return
            if count == 0 and waited >= grace_ms:
                break                                     # never registered — next strategy
    raise RuntimeError(
        f"Could not select the attachments (the header checkbox did not select any rows "
        f"within {timeout_ms / 1000:.0f}s).")


# The picker writes the summary label with NON-BREAKING spaces and separates the value with
# tabs -- "Total\xa0Size\xa0(MB):\t\t792.41". A literal space in the pattern therefore never
# matches, which is how the 500 MB guard went blind on Doc5713434353 (#174): the page showed
# 792.41, `_selected_total_mb` returned None, the ceiling check was skipped, and capture_event
# clicked a disabled Download button until it timed out. `\s` covers space, tab and \xa0 alike.
_TOTAL_MB = re.compile(r"Total\s*Size\s*\(MB\)\s*:\s*([\d,]+(?:\.\d+)?)")

# The picker's summary also carries "Selected Items: 85" alongside "Total Size (MB)" and
# "Total Number" (probed live, docs/superpowers/specs/2026-07-27-oversized-ariba-bundle-capture-
# design.md) -- same NON-BREAKING-space rendering as `_TOTAL_MB`, so `\s` here too.
_SELECTED_ITEMS = re.compile(r"Selected\s*Items\s*:\s*([\d,]+)")


def parse_total_mb(text: str | None) -> float | None:
    """The 'Total Size (MB): N' figure from the picker's summary text, or None. PURE."""
    m = _TOTAL_MB.search(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def parse_selected_items(text: str | None) -> int | None:
    """The 'Selected Items: N' figure from the picker's summary text, or None. PURE.

    Ground truth for how many logical rows the picker holds -- but only while everything is
    selected (`Total Number`, and this figure with it, reads 0 with nothing selected, per the
    probe findings). Meant to be read right after `_select_all_attachments` and passed as
    `AribaPicker.row_keys`'s `expected_count`, so a scroll-enumeration that comes up short
    raises instead of silently planning over a partial row list.
    """
    m = _SELECTED_ITEMS.search(text or "")
    return int(m.group(1).replace(",", "")) if m else None


def _selected_total_mb(page) -> float | None:
    """The 'Total Size (MB): N' the picker shows once items are selected, or None if unread.

    Two reads, because neither alone is reliable. `inner_text` can reorder label and value (they
    sit in separate columns) so the number does not always follow the colon; the raw `textContent`
    scan in DOM order finds the smallest element wrapping both. Try the flat text first -- it is
    what the summary actually renders -- then fall back to the DOM-order scan.
    """
    try:
        total = parse_total_mb(page.inner_text("body"))
    except Exception:
        total = None
    if total is not None:
        return total
    return page.evaluate(
        """() => {
            let best = null;
            for (const e of document.querySelectorAll('td,div,span,label,p,tr,table,body')) {
                const m = (e.textContent || '').match(/Total\\s*Size\\s*\\(MB\\)\\s*:\\s*([\\d,]+(?:\\.\\d+)?)/);
                if (m) best = parseFloat(m[1].replace(/,/g, ''));
            }
            return best;
        }"""
    )


_ROW_KEY = re.compile(r"^\s*(\d+(?:\.\d+)*)\s")


def _outline_sort_key(key: str) -> list:
    """An outline number as a numeric tuple, so `4.10` sorts after `4.9` rather than before it.

    `row_keys` orders its result this way; `_locate` reuses the exact same function to decide
    which way to scroll (#174) — the two must never disagree about what "before"/"after" means
    for an outline number, and a second hand-rolled comparison would risk exactly that drift.
    """
    return [int(p) for p in key.split(".")]

# The picker identifies itself by this heading -- `capture_event` waits for it before touching
# anything in the widget, so it is the same evidence throughout.
PICKER_HEADING = "Selected Attachments Summary"


def _on_picker(page, timeout_ms: int = 5000) -> bool:
    """Whether the attachment picker is still the page in front of us.

    Every read the batching layer makes is a `page.evaluate`, and a navigation turns those into
    `Execution context was destroyed, most likely because of a navigation` -- an error that names
    neither what navigated nor when. The picker carries `Done` buttons at top-right AND
    bottom-right, so a positional click that misses its target can leave the widget entirely, and
    that is what a live run did (#174). Checking the heading right after each click attributes the
    navigation to the action that caused it.

    Polled, never slept: a navigation in flight makes `query_selector` raise, which is "not known
    yet", not "gone". Only a clean read with no heading is evidence of absence.
    """
    waited = 0
    while True:
        try:
            if page.query_selector(f"text={PICKER_HEADING}") is not None:
                return True
        except Exception:
            pass                                   # mid-navigation DOM churn — no answer yet
        if waited >= timeout_ms:
            return False
        page.wait_for_timeout(250)
        waited += 250


# The row-list scroll container, found the same way every time it is needed (#174 fix5).
#
# NOT by class name -- `div.yScroll.tableBody` is one release's CSS, brittle by nature.
# NOT by "the element with the most checkboxes" -- that returns the outer wrapper, which
# contains the header select-all row too and measured a scroll range of only ~221 px on the
# live page. The right discriminator, measured live: walk each row checkbox's ancestor chain
# (stopping before <html>/<body>, so the page's OWN 221 px scroller is never a candidate even
# though it technically has one too) and keep the ancestor with the LARGEST scroll range
# (scrollHeight - clientHeight) among those holding more than one row checkbox. On the live
# page that is unambiguous: the row list at ~3129 px versus the page/wrapper at ~221 px.
# Inlined into the body of each arrow function below (rather than declared at top level and
# concatenated in front of them) so each of _CONTAINER_STATE_JS / _PROGRAMMATIC_SCROLL_JS
# stays a single, self-contained function literal -- two top-level statements (a `function`
# declaration followed by an `() => {}` expression) parse ambiguously depending on how the
# embedding evaluate() call wraps the string, and one shape of that ambiguity really did fail
# ("Malformed arrow function parameter list") when tried standalone.
_FIND_CONTAINER_BODY_JS = """
    function tbFindContainer() {
        const EDGE = 1;
        const checkboxes = Array.from(document.querySelectorAll('input.w-chk-native'));
        const seen = new Set();
        let best = null, bestRange = -1;
        for (const cb of checkboxes) {
            let el = cb.parentElement;
            while (el && el !== document.documentElement && el !== document.body) {
                if (!seen.has(el)) {
                    seen.add(el);
                    const range = el.scrollHeight - el.clientHeight;
                    const count = el.querySelectorAll('input.w-chk-native').length;
                    if (count > 1 && range > EDGE && range > bestRange) {
                        bestRange = range;
                        best = el;
                    }
                }
                el = el.parentElement;
            }
        }
        return best;
    }
"""

# Returns the container's current scrollTop/scrollHeight/clientHeight plus a hover point,
# or null if no container qualifies -- callers must fail loudly on null rather than scroll
# blindly. The container is scrolled into view FIRST (the page scrolls too, per the class
# docstring, so a hover point computed before that could be slid out from under the cursor by
# the time the wheel fires), and the hover point comes only from the container's OWN checkbox
# descendants -- never `.first` on the page, which is the header sitting outside the list --
# clamped to the container's visible rect so it is provably over the strip, not merely
# "rendered somewhere on the page".
_CONTAINER_STATE_JS = """
() => {""" + _FIND_CONTAINER_BODY_JS + """
    const best = tbFindContainer();
    if (!best) return null;
    let rect = best.getBoundingClientRect();
    if (rect.top < 0 || rect.bottom > window.innerHeight) {
        best.scrollIntoView({block: 'center', inline: 'nearest'});
        rect = best.getBoundingClientRect();
    }
    const rowBoxes = Array.from(best.querySelectorAll('input.w-chk-native'));
    let hoverX = rect.left + rect.width / 2;
    let hoverY = rect.top + rect.height / 2;
    if (rowBoxes.length > 0) {
        const mid = rowBoxes[Math.floor(rowBoxes.length / 2)];
        const row = mid.closest('tr') || mid.closest('.w-chk-container') || mid;
        const rb = row.getBoundingClientRect();
        let x = rb.left + rb.width / 2;
        let y = rb.top + rb.height / 2;
        y = Math.max(rect.top + 1, Math.min(rect.bottom - 1, y));
        y = Math.max(1, Math.min(window.innerHeight - 1, y));
        x = Math.max(rect.left + 1, Math.min(rect.right - 1, x));
        hoverX = x;
        hoverY = y;
    }
    return {
        scrollTop: best.scrollTop,
        scrollHeight: best.scrollHeight,
        clientHeight: best.clientHeight,
        hoverX: hoverX,
        hoverY: hoverY,
    };
}
"""

# One-time geometry self-check (#174 retrospective). Reports the picker's actual structure
# rather than assuming it: which element scrolls, how far, and -- the fact that mattered --
# how many row checkboxes live on the PAGE versus INSIDE the container.
#
# This exists because seven live runs were spent fixing the scroll mechanism without once
# measuring the structure it operated on. The decisive fact was a single count: 51 checkboxes
# on the page, 50 in the list, so the header select-all sits OUTSIDE the scrollable strip and
# every `.first` hover parked the cursor off the list. A probe surfaced it in one line; four
# fixes had already shipped without it. Printing it on every capture costs one evaluate() and
# means the next person to touch this -- or the next release that reshapes the DOM -- reads the
# geometry instead of inferring it from failures.
_GEOMETRY_JS = """
() => {""" + _FIND_CONTAINER_BODY_JS + """
    const best = tbFindContainer();
    const onPage = document.querySelectorAll('input.w-chk-native').length;
    const doc = document.scrollingElement || document.documentElement;
    return {
        found: !!best,
        onPage: onPage,
        inContainer: best ? best.querySelectorAll('input.w-chk-native').length : 0,
        range: best ? best.scrollHeight - best.clientHeight : 0,
        clientHeight: best ? best.clientHeight : 0,
        pageRange: doc ? doc.scrollHeight - doc.clientHeight : 0,
    };
}
"""

# The OPTIONAL programmatic path (#174): assigning scrollTop directly removes the mouse from
# the equation entirely, but whether the virtualised list re-renders on a programmatic
# assignment (versus only on a real wheel/scroll gesture) has NOT been verified live. So this
# only ever reports whether the assignment moved scrollTop at all; `_wheel_step` additionally
# checks the rendered keys actually changed before trusting it, and disables further attempts
# on this picker the first time that verification fails.
_PROGRAMMATIC_SCROLL_JS = """
(deltaY) => {""" + _FIND_CONTAINER_BODY_JS + """
    const best = tbFindContainer();
    if (!best) return null;
    const before = best.scrollTop;
    best.scrollTop = before + deltaY;
    const after = best.scrollTop;
    return {
        before: before,
        after: after,
        moved: after !== before,
        maxScroll: Math.max(best.scrollHeight - best.clientHeight, 0),
    };
}
"""


class AribaPicker:
    """Playwright adapter satisfying ariba_batch's Picker protocol (#174).

    Three hazards this class exists to contain, all measured live:

    * **The row list is virtualised.** A fixed 51 checkboxes render as a sliding window over
      ~85 logical rows -- at the top of the list index 9 is "4.1 Form A", after scrolling it is
      "5 Part 5 - Pricing Form". So rows are addressed by OUTLINE NUMBER, and enumeration has
      to scroll the whole list. Reading only what is rendered would silently plan over ~51 of
      ~85 rows, which looks like a clean capture that is quietly missing files.
    * **Handles detach.** The picker re-renders after every selection, so a locator is
      re-resolved at the moment of use and never held across a click.
    * **There are TWO scrollers, and the header checkbox sits outside the one that matters
      (measured, fix5, #174).** The page itself scrolls (941/720 px, range 221) and so does the
      row list, a ~225 px strip holding ~3354 px of rows (range ~3129). A wheel event lands
      wherever the mouse last was, so a wheel not over the strip silently scrolls the *page* and
      leaves the list untouched -- the "dead wheel" that sank five earlier fixes. Worse, the page
      carries 51 `input.w-chk-native` elements but the row list contains only 50: the header
      select-all checkbox is structurally OUTSIDE the scrollable strip. Every earlier hover used
      `.first`, which is precisely the header -- parking the cursor off the list on every attempt.
      `_container_state`/`_hover_row_list`/`_wheel_step` fix this at the root: the container is
      found by the largest scroll range among elements holding more than one row checkbox
      (excluding `<html>`/`<body>`, and NOT "most checkboxes" -- that returns the outer wrapper,
      range ~221, which contains the header too), a hover point comes only from the container's
      own checkbox descendants, and every scroll is verified against the container's own
      `scrollTop` -- a native, immediate signal, unlike inferring success from the rendered row
      window, which is what let this stay broken through five prior guesses.
    """

    def __init__(self, page, log=lambda _m: None):
        self.page = page
        self.log = log
        # Set True the first time a programmatic scrollTop assignment fails verification (moved
        # scrollTop but nothing re-rendered, or found no container at all) -- once disproved on
        # this page instance there is no reason to keep paying for the extra evaluate() on every
        # subsequent scroll; the hover+wheel path below is the mechanism actually proven to work.
        self._programmatic_scroll_disabled = False

    # --- reads ---------------------------------------------------------------------------
    def _rendered_split(self) -> tuple[dict, dict]:
        """Split the rows currently in the DOM into (keyed, unkeyed) by outline number.

        A keyed row maps its outline number to its rendered checkbox index -- exactly what
        `_rendered()` returned before this split existed. An unkeyed row has no leading
        outline number (the header "Title" select-all row, the trailing "Totals" summary
        row) -- it is neither an attachment nor addressable by outline number, so it is
        keyed on its own trimmed text instead: stable enough not to double-count as the
        virtualised window slides the same row past this read many times, unlike a DOM
        index, which is reused by whatever row next occupies that slot. A row with no text
        at all (an empty virtualisation placeholder) is dropped from both -- it was never
        "seen", just reserved DOM space.
        """
        rows = self.page.evaluate(
            """() => Array.from(document.querySelectorAll('input.w-chk-native'))
                 .map((e, i) => { const tr = e.closest('tr');
                                  return [i, ((tr ? tr.innerText : '') || '').trim()]; })""")
        keyed: dict = {}
        unkeyed: dict = {}
        for index, text in rows:
            text = text.replace("\xa0", " ").strip()
            m = _ROW_KEY.match(text)
            if m:
                keyed.setdefault(m.group(1), index)
            elif text:
                unkeyed.setdefault(text, index)
        return keyed, unkeyed

    def _rendered(self) -> dict:
        """{outline key: rendered index} for the rows currently in the DOM."""
        keyed, _ = self._rendered_split()
        return keyed

    def _container_state(self) -> dict:
        """Fresh geometry + scrollTop of the row-list container, re-found on every call.

        Never a held handle -- the picker re-renders on every selection (the class docstring's
        second hazard), so this runs `_CONTAINER_STATE_JS` (walk each row checkbox's own
        ancestor chain, keep the one with the largest scroll range) fresh each time rather than
        caching an element reference that a re-render could invalidate underneath it.

        Raises rather than returning something to scroll blindly against: a caller with no
        container has nothing safe to hover or wheel, and every prior guess that tried anyway
        (guessing a class name, guessing "most checkboxes") is exactly what five earlier fixes
        got wrong (#174 fix5).
        """
        state = self.page.evaluate(_CONTAINER_STATE_JS)
        if state is None:
            raise RuntimeError(
                "the attachment picker's scrollable row-list container could not be found -- "
                "no element besides <html>/<body> has both a nonzero scroll range and more "
                "than one row checkbox descendant (#174 fix5). Refusing to scroll blindly.")
        return state

    def _log_geometry(self) -> None:
        """State the picker's actual structure once per capture, instead of assuming it.

        Seven live runs were spent fixing this widget's scrolling without measuring the DOM it
        scrolls. The fact that resolved it was one count -- checkboxes on the page versus inside
        the container -- which says outright whether the header select-all sits outside the row
        list (it does), and therefore whether a `.first` hover can ever land on the strip (it
        cannot). Every one of those runs would have printed the answer in its first line.

        Best-effort by design: a geometry read that fails must never be what stops a capture,
        so it logs and returns. The guards that actually protect correctness -- the dead-wheel
        check, the enumeration count, the completeness gate -- are elsewhere and are not
        best-effort.
        """
        try:
            g = self.page.evaluate(_GEOMETRY_JS)
        except Exception as exc:                       # noqa: BLE001 — diagnostics never block
            self.log(f"    picker geometry: unreadable ({exc})")
            return
        if not g or not g.get("found"):
            self.log("    picker geometry: NO scroll container found "
                     f"({(g or {}).get('onPage', '?')} checkboxes on the page)")
            return
        outside = g["onPage"] - g["inContainer"]
        self.log(
            f"    picker geometry: list {g['clientHeight']}px tall, scroll range {g['range']}px; "
            f"{g['inContainer']}/{g['onPage']} checkboxes inside it "
            f"({outside} outside — the header select-all); page range {g['pageRange']}px")

    def _hover_row_list(self) -> dict:
        """Move the mouse over a row genuinely inside the row-list container, and return the
        container's freshly-read state (scrollTop, scroll range, the hover point used).

        Playwright dispatches wheel events wherever the mouse last was; nothing else positions
        it. **The header select-all checkbox sits OUTSIDE the scrollable row list** (measured:
        51 `input.w-chk-native` on the page, 50 inside the list container -- #174 fix5), so a
        `.first` locator -- what every earlier attempt hovered -- parks the cursor on the
        header every time: a wheel from there can move the *page* (which also scrolls, per the
        class docstring) and leave the list completely untouched. That is the "dead wheel" that
        made this take six live runs.

        The hover point comes from `_container_state`, which builds it only from the
        container's OWN checkbox descendants and clamps it to the container's visible rect, so
        it is provably over the strip rather than merely "rendered somewhere on the page". The
        container is also scrolled into view there before the point is computed, so the page's
        own scroll cannot slide the strip out from under the cursor between the read and the
        wheel that follows.
        """
        state = self._container_state()
        self.page.mouse.move(state["hoverX"], state["hoverY"])
        return state

    def _wheel_step(self, delta_y: int) -> dict:
        """Scroll the row-list container by one wheel step, verified by ITS OWN scrollTop.

        This is the single scrolling primitive `row_keys`'s sweeps and `_locate`'s directional
        search both now share (#174 fix5) -- the divergence between two ad-hoc scroll
        implementations was itself part of why this took six runs to fix.

        **Verified by scrollTop, not by the rendered row window.** scrollTop is a native
        browser property that updates the instant an actual scroll happens, wheel or
        programmatic; the virtualised re-render that repaints new rows can lag a frame behind
        it. Inferring "did the scroll land" from the rendered keys (what every earlier attempt
        did) conflates a real no-op with a render that just hasn't caught up yet -- this reads
        the one signal that cannot be fooled that way.

        Tries an OPTIONAL programmatic `scrollTop` assignment first -- it would remove the
        mouse from the equation entirely, but whether this virtualised list re-renders on a
        programmatic assignment (as opposed to only a genuine wheel/scroll gesture) has not
        been verified live. So it is trusted only if BOTH the container's scrollTop moved AND
        the rendered keys actually changed; otherwise `_programmatic_scroll_disabled` is
        latched so later calls do not keep paying for an evaluate() already shown not to work,
        and control falls through to the hover+wheel path, which is the mechanism actually
        proven to work against the live page.

        Returns `{before, after, moved, at_edge, max_scroll}`. `at_edge` means the container
        was already at that end of its scroll range (scrollTop 0 for an upward step, or the max
        for a downward one) *before* this step -- a legitimate reason for `moved` to be False,
        as opposed to a dead wheel, which is `moved=False` while NOT at an edge.
        """
        EDGE_TOL = 2

        if not self._programmatic_scroll_disabled:
            before_keys = tuple(sorted(self._rendered()))
            programmatic = self.page.evaluate(_PROGRAMMATIC_SCROLL_JS, delta_y)
            if programmatic is None:
                # No container at all -- not a fact about the programmatic mechanism, just
                # nothing to scroll. Disable it (the hover+wheel path below raises its own,
                # clearer "container not found" error via `_container_state`) rather than
                # paying for this evaluate() again on every later call.
                self._programmatic_scroll_disabled = True
            elif not programmatic["moved"]:
                at_edge = (
                    (delta_y < 0 and programmatic["before"] <= EDGE_TOL)
                    or (delta_y > 0
                        and programmatic["before"] >= programmatic["maxScroll"] - EDGE_TOL)
                )
                if at_edge:
                    # A legitimate no-op (already at that end of the range) -- not evidence the
                    # mechanism doesn't work, so it stays enabled for later calls.
                    return {"before": programmatic["before"], "after": programmatic["after"],
                            "moved": False, "at_edge": True,
                            "max_scroll": programmatic["maxScroll"]}
                self._programmatic_scroll_disabled = True
                self.log("    programmatic scrollTop assignment did not move the container "
                         "while not at an edge -- using hover+wheel for the rest of this "
                         "capture")
            else:
                self.page.wait_for_timeout(300)
                after_keys = tuple(sorted(self._rendered()))
                # Verified only if the rendered keys actually changed -- a scrollTop move with
                # no re-render is exactly the "not verified live" case the docstring warns
                # about, so it is not trusted even though the assignment technically moved.
                if after_keys != before_keys:
                    return {"before": programmatic["before"], "after": programmatic["after"],
                            "moved": True, "at_edge": False,
                            "max_scroll": programmatic["maxScroll"]}
                self._programmatic_scroll_disabled = True
                self.log("    programmatic scrollTop assignment unverified (scrollTop moved "
                         "but nothing re-rendered) -- using hover+wheel for the rest of this "
                         "capture")

        before = self._hover_row_list()
        self.page.mouse.wheel(0, delta_y)
        self.page.wait_for_timeout(300)
        after = self._container_state()
        max_scroll = max(after["scrollHeight"] - after["clientHeight"], 0)
        moved = abs(after["scrollTop"] - before["scrollTop"]) > 0.5
        at_edge = (
            (delta_y < 0 and before["scrollTop"] <= EDGE_TOL)
            or (delta_y > 0 and before["scrollTop"] >= max_scroll - EDGE_TOL)
        )
        return {"before": before["scrollTop"], "after": after["scrollTop"], "moved": moved,
                "at_edge": at_edge, "max_scroll": max_scroll}

    def row_keys(self, expected_count: int | None = None) -> list:
        """Every row's outline number, in order, scrolling to defeat virtualisation.

        Each sweep direction stops on the container's own `scrollTop` reaching that end of its
        scroll range (`_wheel_step`'s `at_edge`), not on the rendered row window going quiet --
        scrollTop is a native property that updates the instant a scroll actually lands, so it
        cannot be foxed by the virtualised re-render lagging a frame behind, the way inferring
        exhaustion from "no new keys this pass" could (and did -- see `_wheel_step` and the
        class docstring, #174 fix5). A wheel that neither moves scrollTop nor sits at an edge is
        a dead wheel and `_wheel_step`/this sweep raise rather than silently under-enumerating.

        A live run reaching the true count does not by itself prove this logic is sound: it
        cannot distinguish "found all rows because the algorithm is right" from "found them
        because the fixed wait happened to be enough that day". `expected_count`, read
        elsewhere from the picker's own "Selected Items" total while everything is selected
        (`selected_count`), closes that gap -- pass it here to turn a short enumeration into a
        raised error instead of a quietly incomplete plan handed to the batching loop.

        **"Selected Items" counts rows this method deliberately excludes.** A live run
        against 85 selected items enumerated 84 outline-keyed rows and tripped the guard --
        but two rows the picker renders carry no outline number at all: the "Title"
        header select-all and a trailing "Totals" summary row. Neither is an attachment
        and neither belongs in the batching loop, yet the picker's own count apparently
        includes at least one of them. Comparing `expected_count` against the keyed count
        alone conflates "row I never saw" with "row I saw and rightly threw away", so the
        guard is validated against keyed + unkeyed-but-seen instead (see `_rendered_split`).

        **The top of the list is established by evidence, not by a keypress.** This used to
        press `Home` and assume the list moved; `keyboard.press` goes to whatever has focus, and
        after `_select_all_attachments`'s positional mouse click nothing establishes that the
        row list is the focus target, so any row above the starting scroll position was simply
        never enumerated. Nothing guarantees the list starts at the top -- the select-all click,
        and any scrolling the picker did while its cascade landed, leave it wherever they leave
        it. So sweep UP first, under the same consecutive-no-growth discipline as the downward
        pass, and keep what that finds: reaching the top becomes an observation, and the result
        no longer depends on focus or on where the list happened to be.

        **This runs exactly once per capture.** Its result is the fingerprint's row list, which
        `ariba_batch.accumulate_batches` is then HANDED -- it does not re-enumerate. A second
        sweep could only diverge from the list everything downstream is checked against, and
        live it did: 84 rows here, 50 on the re-read (#174).
        """
        MAX_PASSES = 45
        STALL_LIMIT = 3           # consecutive dead (non-edge, non-moving) wheels before raising

        self._log_geometry()

        seen, order = set(), []
        unkeyed: set = set()                        # trimmed text of seen-but-unkeyed rows

        def collect():
            keyed_rows, unkeyed_rows = self._rendered_split()
            for key in keyed_rows:
                if key not in seen:
                    seen.add(key)
                    order.append(key)
            unkeyed.update(unkeyed_rows)

        def sweep(delta_y: int) -> None:
            """Wheel one direction until the container's own scrollTop reaches that edge."""
            collect()
            stalled = 0
            for _ in range(MAX_PASSES):
                step = self._wheel_step(delta_y)
                collect()
                if step["moved"]:
                    stalled = 0
                    continue
                if step["at_edge"]:
                    self.page.wait_for_timeout(300)      # let a trailing re-render land
                    collect()
                    return
                stalled += 1
                if stalled >= STALL_LIMIT:
                    raise RuntimeError(
                        f"row_keys: the row-list wheel (delta={delta_y}) did not move the "
                        f"container's scrollTop across {stalled} consecutive attempts while "
                        f"not at an edge (scrollTop stuck at {step['before']}, range "
                        f"0..{step['max_scroll']}) -- the wheel is not reaching the row list")
            raise RuntimeError(
                f"row_keys: exhausted {MAX_PASSES} scroll passes (delta={delta_y}) without "
                "the container's scrollTop ever reaching an edge -- refusing to hand a "
                "possibly-incomplete row list to the batching loop")

        sweep(-2000)                     # up to the top, collecting on the way
        sweep(2000)                      # then the downward sweep, from a known position
        # The sweeps move a real mouse over the widget. Attribute a navigation to them here
        # rather than let the next caller's page.evaluate report a destroyed execution context.
        self._require_picker("the row-list scroll sweep")
        order.sort(key=_outline_sort_key)
        excluded = sorted(unkeyed)
        detail = f" (+{len(excluded)} excluded, no outline number: {excluded})" if excluded else ""
        self.log(f"    picker rows: {len(order)}{detail}")
        total_seen = len(order) + len(excluded)
        if expected_count is not None and total_seen < expected_count:
            raise RuntimeError(
                f"row_keys enumerated {len(order)} keyed + {len(excluded)} unkeyed "
                f"{excluded} = {total_seen} of {expected_count} row(s) the picker reports "
                "selected -- refusing to hand a short row list to the batching loop")
        return order

    def selected_count(self) -> int | None:
        """The picker's own 'Selected Items' total, or None if genuinely unread.

        Only meaningful while everything is selected (see `parse_selected_items`) -- the
        intended use is right after `_select_all_attachments`, feeding the result into
        `row_keys(expected_count=...)` as the ground truth enumeration is checked against.

        **Two reads, and a loud None.** This figure sits in the same summary block as
        `Total Size (MB)`, whose reader needs a second DOM-order pass for exactly one reason:
        `inner_text` can reorder label and value across columns, so the number does not always
        follow the colon. Reading only `inner_text` here meant that reordering (or any
        exception, all swallowed) returned None, `row_keys(expected_count=None)` then skipped
        its guard entirely, and the one mechanism whose whole job is to make under-enumeration
        LOUD went silent at the moment it was needed. So: same two-read shape, and when the
        count is truly unreadable, say so rather than passing None on unremarked.
        """
        try:
            count = parse_selected_items(self.page.inner_text("body"))
        except Exception:
            count = None
        if count is None:
            try:
                count = self.page.evaluate(
                    """() => {
                        let best = null;
                        for (const e of document.querySelectorAll(
                                'td,div,span,label,p,tr,table,body')) {
                            const m = (e.textContent || '').match(
                                /Selected\\s*Items\\s*:\\s*([\\d,]+)/);
                            if (m) best = parseInt(m[1].replace(/,/g, ''), 10);
                        }
                        return best;
                    }""")
            except Exception:
                count = None
        if count is None:
            self.log("    warning: could not read the picker's 'Selected Items' total — "
                     "row_keys will run without its short-enumeration guard")
        return count

    def total_mb(self):
        return _selected_total_mb(self.page)

    def file_count(self) -> int | None:
        """The picker's 'Total Number' of files, or None if unread.

        Whitespace is matched the way `_TOTAL_MB` matches it (`\\s*` before the colon): the
        picker writes these labels with NON-BREAKING spaces and tabs, and a pattern demanding a
        literal `Total Number:` is the same blindness that made the 500 MB guard miss 792.41 MB.

        **None, never 0.** This value goes into the fingerprint, which is compared verbatim
        against the one the partials were planned under, and it goes into the durable
        `.omitted.json` as `expected_files`. A miss returning 0 would therefore either flip the
        fingerprint comparison on a later run -- deleting every downloaded batch of an event too
        big to download in one piece -- or record `expected_files: 0` against a real count. The
        caller refuses to plan a capture at all rather than let either happen (capture_event).
        """
        n = self.page.evaluate(
            """() => { const m = document.body.innerText.match(
                           /Total\\s*Number\\s*:\\s*([\\d,]+)/);
                       return m ? m[1].replace(/,/g, '') : null; }""")
        if n is None:
            self.log("    warning: could not read the picker's 'Total Number' of files")
            return None
        return int(n)

    # --- writes --------------------------------------------------------------------------
    def _direction_to(self, target: list, rendered: dict) -> int:
        """Which way to wheel to bring `target` (an `_outline_sort_key`) into view, given the
        keys currently rendered. -1 is up, +1 is down.

        Compares numerically (`_outline_sort_key`, the same ordering `row_keys` sorts its
        result by) rather than as plain strings, since `"4.10" < "4.9"` as strings but not as
        outline numbers. If `target` sorts before everything rendered it lies above the window;
        after everything, below. Called fresh on every `_locate` pass rather than decided once,
        because each scroll moves the window and a direction chosen from a stale read can carry
        the search straight past a target the last scroll just brought into reach.
        """
        keys = sorted(rendered, key=_outline_sort_key)
        if target < _outline_sort_key(keys[0]):
            return -1
        if target > _outline_sort_key(keys[-1]):
            return 1
        # Falls between two rendered keys without matching either -- not expected against a
        # contiguous virtualised window, but if it happens, head toward the nearer half rather
        # than defaulting to a fixed direction that could walk away from the target.
        mid = _outline_sort_key(keys[len(keys) // 2])
        return -1 if target < mid else 1

    def _locate(self, key: str):
        """Re-resolve the row's checkbox, scrolling it into the window first.

        Returns `(locator, rendered index)` -- the index is how the row's OWN checked state is
        read (`_row_checked`), which a count of ticked boxes cannot tell you.

        **Searches toward the target, not just downward (#174).** `row_keys` sweeps the whole
        list to enumerate it and therefore always leaves the window scrolled to the BOTTOM; the
        batching loop then asks for rows in ascending outline order starting at `1`, at the TOP.
        A one-directional (downward) search wheels the target further away on every pass and
        never finds it. So each pass compares `key` against whatever is currently rendered
        (`_direction_to`, ordered numerically -- a string compare would put `4.10` before `4.9`)
        and wheels whichever way the target actually lies, re-deriving the direction after every
        scroll since the window moves. If the window renders no keyed rows at all (just the
        header / "Totals" row, or a transient empty render), there is nothing to compare
        against, so this tries whichever edge direction hasn't been ruled out yet -- the same
        one wheel-step-at-a-time approach as the normal case, since `_wheel_step`'s `at_edge`
        now answers "is there anything more that way" directly rather than needing a separate
        multi-pass rescue.

        **Cheap on the access pattern this is actually called with.** The batching loop calls
        this ~84 times, once per row, in ascending outline order -- so besides the very first
        call (which walks from row_keys' bottom back to the top), each target is usually already
        rendered or one short scroll from the last one. This never rescans the whole list to
        find a row; it only ever asks "which way from here", which is what keeps the amortised
        cost near one scroll per row instead of one sweep per row.

        The scroll is re-verified rather than trusted: the list is virtualised, so scrolling
        changes which logical row each rendered index holds, and an index read before the scroll
        can address a different row after it. So the rendering is read again afterwards and the
        pair is only returned once `key` still sits at the index the locator was resolved from;
        otherwise the loop simply re-resolves against the new rendering (the row is in view by
        then, so `scroll_into_view_if_needed` is a no-op and the second pass agrees).

        **Every scroll goes through `_wheel_step` -- the one shared, scrollTop-verified
        primitive (#174 fix5), the same one `row_keys`'s sweeps use.** Two ad-hoc scroll
        implementations that could silently disagree about what "landed" meant is what made
        this take six live runs; there is now exactly one. A dead wheel -- `_wheel_step` reports
        `moved=False` while NOT at an edge -- is raised immediately rather than retried into the
        generic "never appeared" exhaustion case below, because the two failures have different
        causes and only one of them means the row is actually absent.
        """
        target = _outline_sort_key(key)
        searched_up = searched_down = False
        last_window = "(never read)"
        stalled = 0
        STALL_LIMIT = 2                    # consecutive dead wheels before calling it a dead wheel
        for _ in range(40):
            keyed, unkeyed = self._rendered_split()
            if key in keyed:
                index = keyed[key]
                loc = self.page.locator("div.w-chk-container").nth(index)
                loc.scroll_into_view_if_needed(timeout=10000)
                self.page.wait_for_timeout(200)
                if self._rendered().get(key) == index:
                    return loc, index
                continue                       # the scroll slid the window — re-resolve

            if keyed:
                direction = self._direction_to(target, keyed)
                last_window = f"keys {sorted(keyed, key=_outline_sort_key)}"
            else:
                # Nothing keyed rendered -- try whichever edge hasn't been ruled out yet.
                direction = -1 if not searched_up else 1
                last_window = f"no keyed rows (unkeyed: {sorted(unkeyed)})"
            searched_up = searched_up or direction < 0
            searched_down = searched_down or direction > 0

            step = self._wheel_step(2000 * direction)
            if step["moved"]:
                stalled = 0
                continue
            if step["at_edge"]:
                # Genuinely nothing further that way -- not evidence of a dead wheel, and not
                # evidence the row is absent either (it may still be found from the other
                # direction, or the window may re-render with a keyed row on the next pass).
                stalled = 0
                continue
            stalled += 1
            if stalled >= STALL_LIMIT:
                raise RuntimeError(
                    f"row {key}: the row-list wheel (direction="
                    f"{'up' if direction < 0 else 'down'}) did not move the container's "
                    f"scrollTop across {stalled} consecutive attempts while not at an edge "
                    f"(scrollTop stuck at {step['before']}, range 0..{step['max_scroll']}) -- "
                    f"the wheel is not reaching the row list. window last held {last_window}")

        directions = ", ".join(
            d for d, tried in (("up", searched_up), ("down", searched_down)) if tried
        ) or "neither direction"
        raise RuntimeError(
            f"row {key} never appeared in the picker window after searching {directions} -- "
            f"window last held {last_window}")

    def _row_checked(self, index: int) -> bool | None:
        """Whether the row at `index` is ticked, or None if that index no longer exists."""
        return self.page.evaluate(
            "(i) => { const e = document.querySelectorAll('input.w-chk-native')[i];"
            "         return e ? e.checked : null; }", index)

    def set_selected(self, key: str, value: bool) -> None:
        """Put row `key` into state `value` -- idempotent, and verified on the row itself.

        Two things this must not be. It must not be a **blind toggle**: the picker is an outline
        TREE whose header demonstrably cascades to descendants, so if a parent row cascades to
        its children too, iterating in outline order would tick `4` (turning `4.1` on with it)
        and then UNTICK `4.1`. Reading the target row's own `checked` state and clicking only on
        a mismatch makes the method mean what its name says regardless of what the caller
        believes the picker's state to be.

        And its settle predicate must not be a **count**: `_checked()` counts only the ~51
        RENDERED inputs, and `_locate` scrolls, which changes which logical rows those are. A
        baseline sampled before the scroll (as it was) could be satisfied by the scroll alone --
        returning from a click that never landed, and letting the batching layer record a row in
        a sidecar it is not actually in. Keyed on the row reaching the requested state, no
        baseline is needed at all.
        """
        loc, index = self._locate(key)
        if self._row_checked(index) is value:
            return                                     # already there (e.g. a parent cascade)
        box = loc.bounding_box()
        if not box:
            raise RuntimeError(f"row {key} has no bounding box")
        self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

        def reached(_count):
            rendered = self._rendered()
            return key in rendered and self._row_checked(rendered[key]) is value

        self._settle(reached)
        # _settle only warns on timeout, and a silently unlanded click is precisely the failure
        # that puts a row in a sidecar naming bytes we never downloaded. Re-locate (so a row
        # merely scrolled out of the window is not mistaken for a failed click) and refuse.
        _loc, index = self._locate(key)
        if self._row_checked(index) is not value:
            raise RuntimeError(
                f"row {key} did not reach selected={value} — refusing to plan batches against "
                f"a selection the picker did not accept")

    def _require_picker(self, action: str) -> None:
        """Refuse to continue if `action` left the attachment picker.

        Raised HERE rather than at the next `page.evaluate`, which reports only
        `Execution context was destroyed, most likely because of a navigation` -- true, opaque,
        and several steps removed from whatever navigated (#174).
        """
        if not _on_picker(self.page):
            raise RuntimeError(
                f"{action} navigated away from the attachment picker (its "
                f"'{PICKER_HEADING}' heading is gone, and the page is at {self.page.url}). The "
                f"picker has Done buttons at both top-right and bottom-right, so a positional "
                f"click that misses its target leaves the widget — refusing rather than reading "
                f"a picker that is no longer there.")

    def clear_selection(self) -> None:
        """Untick every row via the header checkbox, never row by row.

        The batching loop needs an empty selection to start accumulating from once the initial
        select-all shows the bundle is over the ceiling. Deselecting ~85 rows individually would
        cost ~2 minutes (each toggle settles in ~1.5s); the header checkbox is the same cascade
        `_select_all_attachments` rides to turn every row ON, and it is just as fast in reverse
        (~10s) -- so this is that select-all's mirror image, not a loop over `set_selected`.

        **It clicks what `_select_all_attachments` clicks: `label.w-chk`.** That is the visible,
        CSS-drawn box, and it is the one locator on this widget measured working against the live
        site ("select-all via mouse-label" is what the log prints). This used to take the box of
        the enclosing `div.w-chk-container` instead — a larger box, whose centre is not
        necessarily over the checkbox, on a page carrying a `Done` button at each end of the
        picker. A live run went straight from this click to `Execution context was destroyed`
        (#174), which is what leaving the picker looks like. Re-resolved and scrolled into view
        at the moment of use, as `_locate` does: this runs straight after `row_keys`, which leaves
        the list wherever its sweeps ended, and `bounding_box()` on an element above the viewport
        yields a negative y — the click would land on nothing, `_settle` would burn its full 45s,
        and the guard below would raise.

        The header checkbox TOGGLES the whole cascade rather than forcing it off, so a blind
        click here is only correct when something is already selected. Called with nothing
        selected, it would select everything instead of clearing it -- and the failure is
        nearly silent: `_settle`'s predicate (`n == 0`) then waits for a count moving the wrong
        direction, times out at 45s, logs a warning, and returns normally, leaving the picker
        fully selected with no signal to the caller. So this reads the count first and returns
        immediately if it is already zero, and if the click-and-settle doesn't land on zero,
        raises rather than returning -- the batching loop assumes an empty picker to accumulate
        into, and handing it an unknown selection state is worse than stopping here.
        """
        if self._checked() == 0:
            return
        loc = self.page.locator("label.w-chk").first
        loc.scroll_into_view_if_needed(timeout=10000)
        self.page.wait_for_timeout(200)
        box = loc.bounding_box()
        if not box:
            raise RuntimeError("no bounding box for the header checkbox")
        self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        # Before anything reads the page again: every read below is a page.evaluate, and one on a
        # navigated page fails with an error that names neither the cause nor the click.
        self._require_picker("clear_selection's header-checkbox click")
        self._settle(lambda n: n == 0)
        if self._checked() != 0:
            raise RuntimeError(
                "clear_selection did not reach zero -- picker left in an unknown selection "
                "state, refusing to hand it to the batching loop")

    def _checked(self) -> int:
        return self.page.evaluate(
            "() => Array.from(document.querySelectorAll('input.w-chk-native'))"
            ".filter(e => e.checked).length")

    def _settle(self, ready, timeout_ms: int = 45000) -> None:
        """Poll until `ready(checked_count)` holds for two consecutive readings.

        NEVER sleep a guess -- that was #174's root cause. Requiring two stable ticks, not one,
        guards against reading mid-cascade, the same reason `_select_all_attachments` waits for
        more than one checked row rather than trusting the first non-zero count.
        """
        waited, prev, stable = 0, None, 0
        while waited < timeout_ms:
            self.page.wait_for_timeout(500)
            waited += 500
            cur = (self._checked(), _selected_total_mb(self.page))
            stable = stable + 1 if cur == prev else 0
            prev = cur
            if stable >= 2 and ready(cur[0]):
                return
        self.log("    warning: selection never settled within "
                 f"{timeout_ms / 1000:.0f}s — continuing on the last reading")

    def download_to(self, path):
        """Click Download Attachments and block until a complete file sits at `path`.

        `save_as` resolves only once the download stream has finished writing, which is what
        makes this safe: capture_in_batches validates the zip as a zip immediately after this
        returns, so a partially-arrived file must never be observable at `path`. Nothing here
        creates `path` ahead of the download landing.
        """
        with self.page.expect_download(timeout=300000) as dl:
            self.page.get_by_role("button", name="Download Attachments").last.click()
        dl.value.save_as(str(path))
        return path


def capture_attachments(conn, dest_dir=None, log=lambda _m: None, headless=False,
                        virtual_display=False) -> int:
    """Log in, walk every open solicitation, capture and index each bundle. Resumable.

    A bundle already on disk is not re-downloaded — the expensive half is the download, and
    Respond is idempotent (re-responding just re-opens the event). One event's failure is
    logged and never ends the run, exactly as pipeline.run_source isolates a source.
    """
    from playwright.sync_api import sync_playwright

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
    # A headed browser is required (Ariba blocks headless login), so a headless server needs a
    # virtual framebuffer — same Xvfb wrapper as sources/bid_award_panel.py:agenda_fetcher.
    display = None
    if virtual_display:
        from pyvirtualdisplay import Display
        display = Display(visible=False, size=(1440, 900))
        display.start()
    try:
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
    finally:
        if display is not None:
            display.stop()
    return captured
