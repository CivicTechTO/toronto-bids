"""Archive and index solicitation documents behind Ariba's Respond gate (#117).

Respond registers the supplier account as a participant (without bidding) and is disabled once
a posting closes, so this runs as a recurring job via `tb enrich-ariba-attachments` (not `tb sync`).
Documents are captured one file at a time from the event content tree (`ariba_files` +
`AribaFileSource`) to avoid Ariba's 500 MB server-zip limit.

Pure indexing/storage (`index_zip`, `store_bundle`, `ingest_downloads`) is separated from the
Playwright browser flow (`login`, `capture_event`, `capture_attachments`, `AribaFileSource`).
"""
import hashlib
import json
import re
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from toronto_bids import config
from toronto_bids.linking.document_number import bridge_document_number
from toronto_bids.models import AribaAttachment
from toronto_bids.sources import ariba_files
from toronto_bids.store import db

# Authenticated preview URL (no `/public/` or `?anId=ANONYMOUS`) so the login session cookie
# carries over and Respond opens the Sourcing event directly.
DISCOVERY_PREVIEW_URL = (
    "https://portal.us.bn.cloud.ariba.com/dashboard/appext/"
    "comsapsbncdiscoveryui#/RfxEvent/preview/{rfx_id}"
)


def document_number_from_zip_name(name: str) -> str | None:
    """Extract the 10-digit document number from a `Doc##########.zip` name."""
    return bridge_document_number(None, name)


_MAX_ZIP_DEPTH = 8
_MAX_ZIP_ENTRIES = 10000


def index_zip(zip_path) -> list[dict]:
    """Recursively index zip leaf entries using the shared zip_index limits (#199)."""
    from toronto_bids.sources.zip_index import index_zip as read_zip

    return read_zip(zip_path, max_depth=_MAX_ZIP_DEPTH, max_entries=_MAX_ZIP_ENTRIES)


def sha256_of_file(path, _chunk=1 << 20) -> str:
    """Streamed sha256 so large bundles never load into memory whole."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_chunk), b""):
            h.update(block)
    return h.hexdigest()


def store_bundle(conn, zip_path, document_number: str, dest_dir=None) -> int:
    """Copy a bundle to `<dest_dir>/Doc<document_number>.zip` and rebuild its DB index rows.

    Returns the number of leaf files indexed.
    """
    dest_dir = Path(dest_dir if dest_dir is not None else config.ARIBA_ATTACHMENTS_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    canonical = dest_dir / f"Doc{document_number}.zip"

    zip_path = Path(zip_path)
    if zip_path.resolve() != canonical.resolve():
        shutil.copy2(zip_path, canonical)

    zip_sha = sha256_of_file(canonical)
    entries = index_zip(canonical)
    # Rebuild all rows for this document so stale entries cannot survive.
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
    """Archive and index every `Doc*.zip` in `source_dir`, skipping names without a valid Doc number."""
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
    """Rebuild `ariba_attachment` rows offline from canonical bundles already in `dest_dir`."""
    root = dest_dir if dest_dir is not None else config.ARIBA_ATTACHMENTS_DIR
    return ingest_downloads(conn, root, root, log=log)


# Verified training events (#242); still visited each run, but explicit source rejections
# for matching document/posting pairs are reported as notices. Evidence: docs/ariba-attachments.md.
VERIFIED_TRAINING_EVENTS = {
    "3861692164": "15346406",
    "3861744322": "15346471",
    "3949177873": "15889645",
    "3949149442": "15889504",
    "4044346425": "16516115",
}

_EVENT_REJECTIONS = {
    "public_sector_disabled": "Ariba bidding rule: public sector sourcing is not enabled for this event",
    "closed": "Ariba reports the event is closed after Respond",
    "denied": "Ariba denied permission to view the event (or reports an invalid event link)",
}


class EventUnavailable(RuntimeError):
    """An explicit source rejection; callers decide whether it is a verified training gap."""

    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


# Warn in nightly summaries when open uncaptured postings close within this many days.
NEAR_CLOSE_DAYS = 3

# Matches leading YYYY-MM-DD in spine dates and Ariba ISO timestamps.
_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*"


def bundle_archived(dest_dir, document_number: str) -> bool:
    """Whether canonical bytes exist; completeness additionally requires no gap record."""
    return (Path(dest_dir) / f"Doc{document_number}.zip").exists()


def bundle_complete(dest_dir, document_number: str) -> bool:
    """Whether held bytes have no recorded gaps; malformed evidence stays retryable."""
    if not bundle_archived(dest_dir, document_number):
        return False
    try:
        target = Path(dest_dir) / f"Doc{document_number}.zip"
        record = ariba_files.read_omitted(target)
        from toronto_bids.sources.ariba_versions import read_provenance
        with zipfile.ZipFile(target) as archive:
            provenance, _ = read_provenance(archive)
        if provenance and (provenance["document_number"] != document_number
                           or any(item["path"] is None for item in provenance["listing"])):
            return False
    except (RuntimeError, ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile):
        return False
    return not (record["omitted"] or record["collided"])


def open_solicitation_events(conn, today: str | None = None) -> list[dict]:
    """Return still-open solicitations (`{rfx_id, document_number, closes}`), soonest-closing first.

    Unions `solicitation` (modern `RfxEvent/preview/` links) with bridged `ariba_posting` rows as a
    backstop when the spine row is missing, dateless, or has an earlier deadline (#223).
    """
    from toronto_bids.linking.ariba import rfx_id_from_link

    today = today or conn.execute("SELECT date('now')").fetchone()[0]
    events: list[dict] = []
    seen: set[str] = set()

    def _add(rfx, doc, closes):
        if not (rfx and doc) or doc in seen:
            return
        seen.add(doc)
        events.append({"rfx_id": rfx, "document_number": doc, "closes": closes})

    spine = conn.execute(
        "SELECT document_number, ariba_posting_link, substr(submission_deadline, 1, 10) AS closes "
        "FROM solicitation "
        f"WHERE submission_deadline GLOB '{_DATE_GLOB}' "
        "AND substr(submission_deadline, 1, 10) >= ? "
        "AND ariba_posting_link LIKE '%RfxEvent/preview/%' "
        "ORDER BY closes, document_number",
        (today,),
    ).fetchall()
    for row in spine:
        _add(rfx_id_from_link(row["ariba_posting_link"]), row["document_number"], row["closes"])

    backstop = conn.execute(
        "SELECT p.rfx_id, p.document_number, substr(p.close_date, 1, 10) AS closes "
        "FROM ariba_posting p LEFT JOIN solicitation s ON s.document_number = p.document_number "
        "WHERE p.document_number IS NOT NULL "
        f"AND p.close_date GLOB '{_DATE_GLOB}' "
        "AND substr(p.close_date, 1, 10) >= ? "
        "AND (s.document_number IS NULL "
        f"     OR s.submission_deadline IS NULL OR s.submission_deadline NOT GLOB '{_DATE_GLOB}' "
        "     OR substr(s.submission_deadline, 1, 10) < substr(p.close_date, 1, 10)) "
        "ORDER BY closes, p.document_number, p.rfx_id",
        (today,),
    ).fetchall()
    for row in backstop:
        _add(row["rfx_id"], row["document_number"], row["closes"])

    events.sort(key=lambda e: (e["closes"], e["document_number"]))
    return events


def near_close_uncaptured(conn, dest_dir=None, days: int = NEAR_CLOSE_DAYS,
                          today: str | None = None) -> list[dict]:
    """Return open postings closing within `days` days whose bundle is missing or incomplete (#223/#243)."""
    from datetime import date

    dest_dir = Path(dest_dir if dest_dir is not None else config.ARIBA_ATTACHMENTS_DIR)
    today = today or conn.execute("SELECT date('now')").fetchone()[0]
    day0 = date.fromisoformat(today)
    out = []
    for event in open_solicitation_events(conn, today=today):
        try:
            left = (date.fromisoformat(event["closes"]) - day0).days
        except ValueError:
            continue  # skip malformed dates like 2026-13-45
        if 0 <= left <= days and not bundle_complete(dest_dir, event["document_number"]):
            out.append({"document_number": event["document_number"],
                        "closes": event["closes"], "days_left": left})
    return out


def login(page, username: str, password: str, log=lambda _m: None) -> None:
    """Sign into the Ariba supplier account (2-step `#userid` -> `.next-button-text` -> `#Password`).

    Raises immediately on MFA/CAPTCHA challenges or rejected credentials.
    """
    # Wait for SAP's post-load CSRF token rotation to settle before submitting username;
    # reload on bounce for a fresh token.
    for attempt in range(5):
        page.goto(config.ARIBA_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#userid", state="visible", timeout=30000)
        _wait_url_stable(page)
        page.fill("#userid", username)
        if page.input_value("#userid") != username:
            continue
        page.click("a:has(.next-button-text)")
        try:
            page.wait_for_selector("#Password", state="visible", timeout=15000)
            break
        except Exception:
            continue
    else:
        raise RuntimeError("Could not reach the Ariba password step (username step kept bouncing).")

    page.fill("#Password", password)
    page.press("#Password", "Enter")

    # Dashboard polls continuously so networkidle never fires; wait for #Password to detach.
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
    if page.query_selector("#Password") is not None:
        raise RuntimeError("Ariba did not accept the sign-in; check the credentials in scrapers/.env.")
    log("  logged in")


def _wait_url_stable(page, checks: int = 4, interval: int = 800) -> None:
    """Wait until the URL stops changing across two polls (post-load CSRF token rotation)."""
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
    """Dismiss SAP's cookie banner using exact labels so destructive controls like
    'Decline to Respond' can never be matched."""
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
    """Open one event preview, click Respond, and capture its documents one file at a time.

    Returns the bundle path, or None if closed/deferred without error. Raises `EventUnavailable`
    on explicit post-Respond rejections.
    """
    rfx_id, document_number = event["rfx_id"], event["document_number"]
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not _open_authed_preview(page, rfx_id):
        raise RuntimeError(
            f"Doc{document_number}: the authenticated event preview never loaded "
            f"(rfx {rfx_id}) — session/SSO did not settle.")

    respond = _preview_respond(page, rfx_id)
    if respond.is_disabled():
        # Salvage partial captures only if Respond stays disabled across reads (SPA renders
        # disabled while loading); skip immediately when no partial directory exists.
        pdir = ariba_files.partial_dir(dest_dir, document_number)
        if not pdir.exists():
            log(f"  Doc{document_number}: Respond disabled (closed) — skipped")
            return None
        if _respond_stably_disabled(page, respond):
            salvaged = ariba_files.finalise_partial(
                document_number, dest_dir, posting_open=False, log=log)
            if salvaged is not None:
                log(f"  Doc{document_number}: closed mid-capture — salvaged what we had")
                return salvaged
            log(f"  Doc{document_number}: Respond disabled (closed) — skipped")
            return None
        log(f"  Doc{document_number}: Respond read disabled once then enabled — treating the "
            f"posting as open, partials kept")
    respond.click()

    download_content = page.get_by_role("button", name="Download Content")
    outcome = _wait_post_respond(page, download_content)
    if outcome in _EVENT_REJECTIONS:
        raise EventUnavailable(outcome, f"Doc{document_number}: {_EVENT_REJECTIONS[outcome]}")
    if outcome == "anonymous":
        raise RuntimeError(
            f"Doc{document_number}: Ariba served the anonymous view (Register/Login modal); "
            f"the session did not carry to the preview.")
    if outcome != "event":
        evidence = _respond_timeout_evidence(page, rfx_id, document_number, dest_dir)
        raise RuntimeError(
            f"Doc{document_number}: the Sourcing event never loaded after Respond. {evidence}")
    _dismiss_cookie_banner(page)

    source = AribaFileSource(page, rfx_id=rfx_id, log=log)
    # Posting is open here, so capture_files may safely retire stale partials and re-fetch.
    return ariba_files.capture_files(source, document_number, dest_dir, log=log)


# Subdir outside the Doc*.zip glob used by ingest/reindex.
RESPOND_TIMEOUT_DIRNAME = "respond-timeouts"
_EXCERPT_CHARS = 400
_EVIDENCE_TIMEOUT_MS = 5000  # bound diagnostics when the page is already hung


def _best_effort(fn, default="?"):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def _save_tab(tab, out: Path, stem: str) -> list[str]:
    """Write one tab's HTML and screenshot; each independently, so one failure keeps the other."""
    saved = []
    if _best_effort(lambda: (out / f"{stem}.html").write_text(tab.content(), encoding="utf-8"),
                    None) is not None:
        saved.append(f"{stem}.html")
    if _best_effort(lambda: tab.screenshot(path=str(out / f"{stem}.png"), full_page=True,
                                           timeout=_EVIDENCE_TIMEOUT_MS), False) is not False:
        saved.append(f"{stem}.png")
    return saved


def _respond_timeout_evidence(page, rfx_id: str, document_number: str, dest_dir: Path) -> str:
    """Capture best-effort page/tab URLs, titles, excerpt, and local HTML+PNG artifacts on Respond timeout (#304)."""
    url = _best_effort(lambda: page.url)
    title = _best_effort(lambda: page.title())
    visible = _best_effort(
        lambda: page.locator(f"text=ID - {rfx_id}").first.is_visible(), None)
    if visible is None:
        preview = "preview unknown"
    else:
        preview = "preview still visible" if visible else "preview not visible"
    text = _best_effort(lambda: " ".join(
        (page.inner_text("body", timeout=_EVIDENCE_TIMEOUT_MS) or "").split()), "")
    excerpt = text[:_EXCERPT_CHARS] + ("…" if len(text) > _EXCERPT_CHARS else "")
    tabs = _best_effort(lambda: list(page.context.pages), [])
    others = [t for t in tabs if t is not page]

    out = Path(dest_dir) / RESPOND_TIMEOUT_DIRNAME
    stem = f"Doc{document_number}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    saved = []
    if _best_effort(lambda: out.mkdir(parents=True, exist_ok=True), False) is not False:
        saved += _save_tab(page, out, stem)
        for i, tab in enumerate(others, 1):
            saved += _save_tab(tab, out, f"{stem}-tab{i}")
    described = [(_best_effort(lambda t=t: t.url), _best_effort(lambda t=t: t.title()))
                 for t in others]
    other_tabs = f"other tabs={described!r}" if described else "no other tabs"
    return (f"After Respond: url={url!r} title={title!r} {preview}; {other_tabs}; "
            f"text={excerpt!r}; evidence={saved or 'not saved'}")


def _open_authed_preview(page, rfx_id: str, attempts: int = 3) -> bool:
    """Load the authenticated Discovery preview, retrying until `ID - <rfx_id>` appears.

    First navigation can drop the hash fragment during SSO redirects; retrying routes cleanly.
    """
    for _ in range(attempts):
        page.goto(DISCOVERY_PREVIEW_URL.format(rfx_id=rfx_id),
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)  # wait for SSO redirect chain
        _dismiss_cookie_banner(page)
        if page.query_selector(f"text=ID - {rfx_id}") is not None:
            return True
    return False


def _preview_respond(page, rfx_id: str, timeout_ms: int = 15000):
    """Wait for the Respond button, opening the 'Additional Options' overflow menu once if needed (#241)."""
    respond = page.get_by_role("button", name="Respond", exact=True)
    options = page.get_by_role("button", name="Additional Options", exact=True)
    opened = False
    waited = 0
    while True:
        if respond.is_visible():
            return respond
        if waited >= timeout_ms:
            context = " after opening Additional Options" if opened else ""
            raise RuntimeError(
                f"rfx {rfx_id}: preview loaded but Respond did not become visible{context}")
        if not opened and options.is_visible() and options.is_enabled():
            options.click(timeout=timeout_ms - waited)
            opened = True
        else:
            page.wait_for_timeout(250)
            waited += 250


def _respond_stably_disabled(page, respond, reads: int = 3, interval_ms: int = 1500) -> bool:
    """Confirm Respond remains disabled across multiple polls before salvaging partials.

    Returns False on transient errors so partials stay retryable.
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
    """Poll after clicking Respond and classify outcome: event, rejection reason, anonymous, or timeout."""
    waited = 0
    while waited < timeout_ms:
        try:
            if download_content.count() and download_content.first.is_visible():
                return "event"
            body = " ".join((page.inner_text("body") or "").lower().split())
            if _PERMISSION_DENIED in body:
                return "denied"
            if "this event is no longer accepting responses" in body:
                if "global bidding rule: public sector sourcing is not enabled for this event" in body:
                    return "public_sector_disabled"
                if "event is closed" in body:
                    return "closed"
            if page.get_by_role("button", name="Register/Login").count():
                return "anonymous"
        except Exception:
            pass  # mid-navigation DOM churn
        page.wait_for_timeout(1000)
        waited += 1000
    return "timeout"


# Heading that identifies the attachment picker; `_on_event_view` requires its absence.
PICKER_HEADING = "Selected Attachments Summary"


# Locate the scrollable ancestor holding multiple `marker` elements with the largest scroll range.
# With `allowDocument`, falls back to the document unless an inner container scrolls >=20px and holds all markers.
# Inlined inside each arrow function so Playwright evaluates a single function literal.
_FIND_CONTAINER_BODY_JS = """
    function tbFindContainer(marker, allowDocument) {
        const EDGE = 1;
        const markers = Array.from(document.querySelectorAll(marker));
        const seen = new Set();
        let best = null, bestRange = -1, bestCount = 0;
        for (const cb of markers) {
            let el = cb.parentElement;
            while (el && el !== document.documentElement && el !== document.body) {
                if (!seen.has(el)) {
                    seen.add(el);
                    const range = el.scrollHeight - el.clientHeight;
                    const count = el.querySelectorAll(marker).length;
                    if (count > 1 && range > EDGE && range > bestRange) {
                        bestRange = range;
                        bestCount = count;
                        best = el;
                    }
                }
                el = el.parentElement;
            }
        }
        // The picker (`allowDocument === false`) never reaches past here -- `best`, found only
        // among inner ancestors exactly as before, is the whole answer. Do not touch this
        // return for that caller; everything below is new and scoped to `allowDocument`.
        if (!allowDocument) return best;
        // The event's content tree scrolls the PAGE, not an inner strip -- so here the document
        // is a genuine competitor, scored on the same two questions as any inner element: does
        // it actually scroll, and does it hold the markers being traversed. MIN_MEANINGFUL_RANGE
        // excludes noise like the measured 6 px element (every real scroller measured live
        // clears it by 10x or more: this page's own 465 px, the picker's inner list at 3129 px).
        // An inner element only outranks the document when it clears that bar AND contains
        // EVERY marker being traversed -- a partial container (25 of 72, measured) must lose
        // even though it technically scrolls, because a traversal that silently walks 25 of 72
        // anchors is exactly the failure this exists to prevent. Requiring full containment
        // rather than "most of them" errs toward the document whenever it is in doubt, which is
        // the safe direction: the document can never miss a marker that is on the page at all.
        const MIN_MEANINGFUL_RANGE = 20;
        const docCount = document.querySelectorAll(marker).length;
        if (best && bestRange >= MIN_MEANINGFUL_RANGE && bestCount === docCount) {
            return best;
        }
        return document.scrollingElement || document.documentElement;
    }
    function tbIsDocument(el) {
        return el === document.scrollingElement || el === document.documentElement
            || el === document.body;
    }
"""

# Return scroll container metrics and a clamped hover point over an interior marker row, or null.
_CONTAINER_STATE_JS = """
(opts) => {""" + _FIND_CONTAINER_BODY_JS + """
    const best = tbFindContainer(opts.marker, opts.allowDocument);
    if (!best) return null;
    const isDoc = tbIsDocument(best);
    // scrollIntoView on the document scroller would jump the page to the top -- fighting the
    // very sweep that called this. The viewport IS the document scroller's visible rect.
    let rect = isDoc
        ? {top: 0, left: 0, right: window.innerWidth, bottom: window.innerHeight,
           width: window.innerWidth, height: window.innerHeight}
        : best.getBoundingClientRect();
    if (!isDoc && (rect.top < 0 || rect.bottom > window.innerHeight)) {
        best.scrollIntoView({block: 'center', inline: 'nearest'});
        rect = best.getBoundingClientRect();
    }
    const rowBoxes = Array.from(best.querySelectorAll(opts.marker));
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

# One-time DOM scroll geometry summary logged per traversal.
_GEOMETRY_JS = """
(opts) => {""" + _FIND_CONTAINER_BODY_JS + """
    const best = tbFindContainer(opts.marker, opts.allowDocument);
    const onPage = document.querySelectorAll(opts.marker).length;
    const doc = document.scrollingElement || document.documentElement;
    return {
        found: !!best,
        isDocument: best ? tbIsDocument(best) : false,
        onPage: onPage,
        inContainer: best ? best.querySelectorAll(opts.marker).length : 0,
        range: best ? best.scrollHeight - best.clientHeight : 0,
        clientHeight: best ? best.clientHeight : 0,
        pageRange: doc ? doc.scrollHeight - doc.clientHeight : 0,
    };
}
"""

# Direct scrollTop adjustment; callers verify the rendered signature changes before trusting it.
_PROGRAMMATIC_SCROLL_JS = """
(opts) => {""" + _FIND_CONTAINER_BODY_JS + """
    const best = tbFindContainer(opts.marker, opts.allowDocument);
    if (!best) return null;
    const deltaY = opts.deltaY;
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


class _ListScroller:
    """Container-aware, scrollTop-verified scroller for Ariba lists.

    `signature` returns a hashable view snapshot used to verify programmatic scrollTop updates.
    """

    def __init__(self, page, marker: str, signature, log=lambda _m: None,
                 allow_document: bool = False, label: str = "list"):
        self.page = page
        self.marker = marker
        self.signature = signature
        self.log = log
        self.allow_document = allow_document
        self.label = label
        # Latched True once programmatic scrollTop fails verification; falls back to hover+wheel.
        self._programmatic_scroll_disabled = False

    def _args(self, **extra) -> dict:
        return {"marker": self.marker, "allowDocument": self.allow_document, **extra}

    def state(self) -> dict:
        """Re-find the scroll container on each call (handles DOM re-renders) and return geometry + hover point."""
        state = self.page.evaluate(_CONTAINER_STATE_JS, self._args())
        if state is None:
            raise RuntimeError(
                f"{self.label}: the scrollable container could not be found -- no element "
                f"besides <html>/<body> has both a nonzero scroll range and more than one "
                f"'{self.marker}' descendant (#174 fix5). Refusing to scroll blindly.")
        return state

    def log_geometry(self) -> None:
        """Best-effort log of container scroll range and marker counts."""
        try:
            g = self.page.evaluate(_GEOMETRY_JS, self._args())
        except Exception as exc:  # noqa: BLE001
            self.log(f"    {self.label} geometry: unreadable ({exc})")
            return
        if not g or not g.get("found"):
            self.log(f"    {self.label} geometry: NO scroll container found "
                     f"({(g or {}).get('onPage', '?')} '{self.marker}' on the page)")
            return
        outside = g["onPage"] - g["inContainer"]
        where = "the page itself" if g.get("isDocument") else "an inner container"
        self.log(
            f"    {self.label} geometry: scrolls {where}, {g['clientHeight']}px tall, scroll "
            f"range {g['range']}px; {g['inContainer']}/{g['onPage']} '{self.marker}' inside it "
            f"({outside} outside); page range {g['pageRange']}px")

    def hover(self) -> dict:
        """Move the mouse over a row inside the scroll container (avoiding outer headers) and return its state."""
        state = self.state()
        self.page.mouse.move(state["hoverX"], state["hoverY"])
        return state

    def step(self, delta_y: int) -> dict:
        """Advance the container by `delta_y`, verified by `scrollTop` rather than rendered row lag.

        Tries programmatic `scrollTop` first (requiring `signature()` to change), then falls back to
        hover+wheel. Returns `{before, after, moved, at_edge, max_scroll}`.
        """
        EDGE_TOL = 2

        if not self._programmatic_scroll_disabled:
            before_keys = self.signature()
            programmatic = self.page.evaluate(
                _PROGRAMMATIC_SCROLL_JS, self._args(deltaY=delta_y))
            if programmatic is None:
                self._programmatic_scroll_disabled = True
            elif not programmatic["moved"]:
                at_edge = (
                    (delta_y < 0 and programmatic["before"] <= EDGE_TOL)
                    or (delta_y > 0
                        and programmatic["before"] >= programmatic["maxScroll"] - EDGE_TOL)
                )
                if at_edge:
                    # Already at edge; keep programmatic scrolling enabled.
                    return {"before": programmatic["before"], "after": programmatic["after"],
                            "moved": False, "at_edge": True,
                            "max_scroll": programmatic["maxScroll"]}
                self._programmatic_scroll_disabled = True
                self.log(f"    {self.label}: programmatic scrollTop assignment did not move the "
                         f"container while not at an edge -- using hover+wheel from here")
            else:
                self.page.wait_for_timeout(300)
                # Require rendered signature to change before trusting programmatic scroll.
                if self.signature() != before_keys:
                    return {"before": programmatic["before"], "after": programmatic["after"],
                            "moved": True, "at_edge": False,
                            "max_scroll": programmatic["maxScroll"]}
                self._programmatic_scroll_disabled = True
                self.log(f"    {self.label}: programmatic scrollTop assignment unverified "
                         f"(scrollTop moved but nothing re-rendered) -- using hover+wheel "
                         f"from here")

        before = self.hover()
        self.page.mouse.wheel(0, delta_y)
        self.page.wait_for_timeout(300)
        after = self.state()
        max_scroll = max(after["scrollHeight"] - after["clientHeight"], 0)
        moved = abs(after["scrollTop"] - before["scrollTop"]) > 0.5
        at_edge = (
            (delta_y < 0 and before["scrollTop"] <= EDGE_TOL)
            or (delta_y > 0 and before["scrollTop"] >= max_scroll - EDGE_TOL)
        )
        return {"before": before["scrollTop"], "after": after["scrollTop"], "moved": moved,
                "at_edge": at_edge, "max_scroll": max_scroll}


# Read all `<a>` elements with row text, DOM id, AribaWeb kind metadata, and document-only within-row ordinal.
# Keep the extension regex in sync with `ariba_files._DOCUMENT_EXTENSION`.
_ANCHORS_JS = """
() => {
    const isDocLabel = (raw) => {
        const t = (raw || '').replace(/\\s+/g, ' ').trim();
        if (!t) return false;
        const lower = t.toLowerCase();
        if (lower.startsWith('http://') || lower.startsWith('https://')
                || lower.startsWith('www.')) {
            return false;
        }
        return /\\.(7z|bmp|csv|dgn|docx?|dwf|dwg|dxf|eml|gif|gz|jpe?g|kmz|msg|odt|pdf|png|pptx?|rar|rtf|tar|tiff?|txt|xlsm|xlsx?|xml|zip)(?![a-z0-9])/i.test(t);
    };
    return Array.from(document.querySelectorAll('a')).map((e, i) => {
        const row = e.closest('tr') || e.parentElement;
        const anchors = row ? Array.from(row.querySelectorAll('a')) : [];
        const docAnchors = anchors.filter(
            (a) => isDocLabel((a.innerText || a.textContent) || ''));
        return {
            index: i,
            // The anchor's own DOM id -- the LIVE half of the identity split (ariba_files
            // `document_key`). Measured unique across every document anchor on this event.
            id: e.id || '',
            // What KIND of document control this is, read from what the DOM publishes rather
            // than inferred (ariba_files `anchor_kind`). PMI items download on a direct click;
            // PML links open a menu first. Treating all of them as PML failed all 36 PMIs.
            role: e.getAttribute('role') || '',
            bh: e.getAttribute('bh') || '',
            cls: (e.className || '').toString(),
            name: ((e.innerText || e.textContent) || '').replace(/\\s+/g, ' ').trim(),
            row: ((row ? (row.innerText || row.textContent) : '') || '')
                    .replace(/\\s+/g, ' ').trim().slice(0, 120),
            ordinal: Math.max(docAnchors.indexOf(e), 0),
        };
    });
}
"""

# Tag a single anchor by index after verifying its text still matches.
_TAG_ANCHOR_JS = """
(want) => {
    for (const e of document.querySelectorAll('[data-tb-file]')) {
        e.removeAttribute('data-tb-file');
    }
    const a = document.querySelectorAll('a')[want.index];
    if (!a) return false;
    const text = ((a.innerText || a.textContent) || '').replace(/\\s+/g, ' ').trim();
    if (text !== want.name) return false;
    a.setAttribute('data-tb-file', '1');
    return true;
}
"""

# Find innermost References toggles, assigning persistent `data-tb-refid` so sections are clicked at most once.
_REFERENCE_TOGGLES_JS = """
() => {
    for (const e of document.querySelectorAll('[data-tb-toggle]')) {
        e.removeAttribute('data-tb-toggle');
    }
    window.__tbRefSeq = window.__tbRefSeq || 0;
    const matches = [];
    for (const el of document.querySelectorAll('a,button,span,div,td,th,li')) {
        const txt = ((el.innerText || el.textContent) || '').replace(/\\s+/g, ' ').trim();
        if (!/references/i.test(txt) || txt.length > 40) continue;
        matches.push(el);
    }
    const inner = matches.filter(el => !matches.some(o => o !== el && el.contains(o)));
    return inner.map((el, i) => {
        el.setAttribute('data-tb-toggle', String(i));
        if (!el.hasAttribute('data-tb-refid')) {
            el.setAttribute('data-tb-refid', String(++window.__tbRefSeq));
        }
        const holder = el.closest('[aria-expanded]');
        const rect = el.getBoundingClientRect();
        return {
            id: String(i),
            refid: el.getAttribute('data-tb-refid'),
            text: ((el.innerText || el.textContent) || '').replace(/\\s+/g, ' ').trim(),
            expanded: holder ? holder.getAttribute('aria-expanded') : null,
            visible: !!(rect.width || rect.height),
        };
    });
}
"""

# Locate an innermost 'Expand All' control if present.
_EXPAND_ALL_JS = """
() => {
    for (const e of document.querySelectorAll('[data-tb-bulk-expand]')) {
        e.removeAttribute('data-tb-bulk-expand');
    }
    const rx = /^expand\\s*all\\b/i;
    const els = Array.from(document.querySelectorAll('a,button,span,div'));
    const matches = els.filter(el => {
        const txt = ((el.innerText || el.textContent) || '').replace(/\\s+/g, ' ').trim();
        return rx.test(txt);
    });
    const inner = matches.filter(el => !matches.some(o => o !== el && el.contains(o)));
    if (!inner.length) return null;
    const el = inner[0];
    const rect = el.getBoundingClientRect();
    el.setAttribute('data-tb-bulk-expand', '1');
    return {
        text: ((el.innerText || el.textContent) || '').replace(/\\s+/g, ' ').trim(),
        visible: !!(rect.width || rect.height),
    };
}
"""

_DOWNLOAD_MENU_ITEM = "Download this attachment"


class AribaFileSource:
    """Playwright FileSource adapter for the Sourcing event's All Content tree (#174).

    Pure listing, identity, and naming logic lives in `ariba_files`; this class handles DOM reads,
    idempotent expansion of References sections, scrolling, and per-file download clicks.
    """

    MAX_SCROLL_PASSES = 40
    STALL_LIMIT = 3  # consecutive non-edge stalled wheels before raising

    def __init__(self, page, rfx_id: str | None = None, log=lambda _m: None):
        self.page = page
        self.rfx_id = rfx_id
        self.log = log
        self._toggled: set = set()
        self._collided_count = 0
        self._resolved_by_handle = 0
        self._resolved_by_key = 0
        # DOM ids already clicked this run (guards pick_unclaimed fallback).
        self._claimed: set = set()
        self._scroller = _ListScroller(
            page, "a", self._anchor_signature, log=log, allow_document=True,
            label="content tree")

    def _read_anchors(self) -> list:
        return self.page.evaluate(_ANCHORS_JS)

    def _anchor_count(self) -> int:
        return self.page.evaluate("() => document.querySelectorAll('a').length")

    def _anchor_signature(self):
        """Return sorted anchor keys currently rendered (re-render check for `_ListScroller`)."""
        try:
            anchors = self._read_anchors()
        except Exception:  # noqa: BLE001
            return ()
        listing = ariba_files.listing_from_anchors(anchors)
        return tuple(sorted(f["key"] for f in listing["files"]))

    def _on_event_view(self) -> bool:
        """Return True when the event's All Content view is active (no picker heading, visible
        Download Content button, plus a positive 'All Content' or outline-row marker)."""
        try:
            if self.page.query_selector(f"text={PICKER_HEADING}") is not None:
                return False
            dc = self.page.get_by_role("button", name="Download Content")
            if not (bool(dc.count()) and dc.first.is_visible()):
                return False
            if self.page.get_by_text("All Content", exact=False).count():
                return True
            return self._has_outline_row()
        except Exception:  # noqa: BLE001
            return False

    def _has_outline_row(self) -> bool:
        """Return True if any current DOM anchor sits in an outline-numbered row."""
        try:
            anchors = self._read_anchors()
        except Exception:  # noqa: BLE001
            return False
        return any(ariba_files.is_outline_row(a.get("row")) for a in anchors)

    def _wait_event_view(self, timeout_ms: int = 15000) -> bool:
        waited = 0
        while True:
            if self._on_event_view():
                return True
            if waited >= timeout_ms:
                return False
            self.page.wait_for_timeout(250)
            waited += 250

    def _require_event_view(self, action: str) -> None:
        if self._wait_event_view(timeout_ms=5000):
            return
        raise RuntimeError(
            f"{action} would run on the wrong page: the event's All Content view is not in "
            f"front of us (no visible 'Download Content' button; the page is at "
            f"{self.page.url}). Traversing or clicking here would silently address whatever "
            f"else is rendered — refusing (#174).")

    def _reference_toggles(self) -> list:
        try:
            return self.page.evaluate(_REFERENCE_TOGGLES_JS) or []
        except Exception as exc:  # noqa: BLE001
            self.log(f"    could not read the References toggles ({exc})")
            return []

    def _await_anchor_count_change(self, baseline: int, timeout_ms: int = 8000) -> int:
        """Poll until the page anchor count changes from `baseline`, then allow 400 ms to settle."""
        waited = 0
        while True:
            try:
                count = self._anchor_count()
            except Exception:  # noqa: BLE001
                count = baseline
            if count != baseline:
                self.page.wait_for_timeout(400)
                try:
                    return self._anchor_count()
                except Exception:  # noqa: BLE001
                    return count
            if waited >= timeout_ms:
                return baseline
            self.page.wait_for_timeout(250)
            waited += 250

    def _open_section(self, cand: dict) -> bool:
        """Click a References toggle and undo if it collapsed an already-open section."""
        before = self._anchor_count()
        try:
            loc = self.page.locator(f'[data-tb-toggle="{cand["id"]}"]').first
            loc.scroll_into_view_if_needed(timeout=5000)
            loc.click(timeout=5000)
        except Exception as exc:  # noqa: BLE001
            self.log(f"    References toggle {cand['text']!r} did not click ({exc})")
            return False
        after = self._await_anchor_count_change(before)
        if after > before:
            return True
        if after < before:
            self.log(f"    References toggle {cand['text']!r} COLLAPSED an open section "
                     f"({before} -> {after} links) — re-opening it")
            try:
                self.page.locator(f'[data-tb-toggle="{cand["id"]}"]').first.click(timeout=5000)
                self._await_anchor_count_change(after)
            except Exception as exc:  # noqa: BLE001
                self.log(f"      could not re-open it ({exc})")
            return False
        self.log(f"    References toggle {cand['text']!r} revealed nothing")
        return False

    def _open_all_content(self) -> bool:
        """Click 'All Content' if visible so Part 2+ rows and References toggles render (#174)."""
        control = self.page.get_by_text("All Content", exact=False)
        try:
            found = bool(control.count())
        except Exception as exc:  # noqa: BLE001
            self.log(f"    content tree: could not check for an 'All Content' control ({exc})")
            return False
        if not found:
            self.log("    content tree: no 'All Content' control found — assuming every "
                     "Part's row already renders")
            return False
        target = control.first
        try:
            if not target.is_visible():
                self.log("    content tree: 'All Content' control present but not visible — "
                         "skipping")
                return False
            before = self._anchor_count()
            target.scroll_into_view_if_needed(timeout=5000)
            target.click(timeout=5000)
        except Exception as exc:  # noqa: BLE001
            self.log(f"    content tree: 'All Content' did not click ({exc})")
            return False
        after = self._await_anchor_count_change(before, timeout_ms=10000)
        if after > before:
            self.log(f"    content tree: 'All Content' opened — {before} -> {after} links")
            return True
        self.log(f"    content tree: 'All Content' clicked but the link count did not grow "
                 f"({before} -> {after}) — continuing regardless (a future layout may already "
                 f"render everything)")
        return False

    def _bulk_expand_references(self) -> bool:
        """Click a bulk 'Expand All' control if present; `_expand_references` still runs afterward."""
        try:
            cand = self.page.evaluate(_EXPAND_ALL_JS)
        except Exception as exc:  # noqa: BLE001
            self.log(f"    content tree: could not check for a bulk expand control ({exc})")
            return False
        if not cand:
            self.log("    content tree: no bulk 'Expand All' control found — expanding "
                     "References sections one at a time")
            return False
        if not cand["visible"]:
            self.log(f"    content tree: bulk control {cand['text']!r} found but not visible "
                     "— expanding References sections one at a time")
            return False
        before = self._anchor_count()
        try:
            loc = self.page.locator('[data-tb-bulk-expand="1"]').first
            loc.scroll_into_view_if_needed(timeout=5000)
            loc.click(timeout=5000)
        except Exception as exc:  # noqa: BLE001
            self.log(f"    content tree: {cand['text']!r} did not click ({exc})")
            return False
        after = self._await_anchor_count_change(before, timeout_ms=10000)
        if after > before:
            self.log(f"    content tree: {cand['text']!r} expanded everything — {before} -> "
                     f"{after} links")
            return True
        self.log(f"    content tree: {cand['text']!r} clicked but the link count did not grow "
                 f"({before} -> {after}) — falling back to per-section expansion")
        return False

    def _outline_row_count(self) -> int:
        """Count distinct outline-numbered rows currently in the DOM (best-effort diagnostic)."""
        try:
            anchors = self._read_anchors()
        except Exception:  # noqa: BLE001
            return 0
        return len({ariba_files.row_identity(a.get("row")) for a in anchors
                    if ariba_files.is_outline_row(a.get("row"))})

    def _expand_references(self, max_rounds: int = 6) -> int:
        """Expand each visible References section at most once using persistent `data-tb-refid` tags.

        Returns the number of sections whose click increased the anchor count.
        """
        expanded = 0
        for _ in range(max_rounds):
            clicked = 0
            for cand in self._reference_toggles():
                if cand["refid"] in self._toggled:
                    continue
                if (cand["expanded"] or "").lower() == "true":
                    self._toggled.add(cand["refid"])
                    continue
                if not cand["visible"]:
                    continue
                self._toggled.add(cand["refid"])
                clicked += 1
                if self._open_section(cand):
                    expanded += 1
            if not clicked:
                break
        return expanded

    def _sweep(self) -> tuple:
        """Sweep up then down to scroll edges, merging every DOM read by anchor key.

        Raises on stalled wheels or pass exhaustion rather than returning a partial list.
        """
        by_key: dict = {}
        rejected: dict = {}
        collided: dict = {}

        def collect():
            listing = ariba_files.listing_from_anchors(self._read_anchors())
            for entry in listing["files"]:
                by_key.setdefault(entry["key"], entry)
            for name in listing["rejected"]:
                rejected[name] = rejected.get(name, 0) + 1
            for hit in listing["collided"]:
                collided[hit["key"]] = hit["name"]

        def sweep(delta_y: int) -> None:
            collect()
            stalled = 0
            for _ in range(self.MAX_SCROLL_PASSES):
                step = self._scroller.step(delta_y)
                collect()
                if step["moved"]:
                    stalled = 0
                    continue
                if step["at_edge"]:
                    self.page.wait_for_timeout(300)
                    collect()
                    return
                stalled += 1
                if stalled >= self.STALL_LIMIT:
                    raise RuntimeError(
                        f"the content tree's wheel (delta={delta_y}) did not move the "
                        f"container's scrollTop across {stalled} consecutive attempts while "
                        f"not at an edge (stuck at {step['before']}, range "
                        f"0..{step['max_scroll']}) — the wheel is not reaching the tree")
            raise RuntimeError(
                f"the content tree exhausted {self.MAX_SCROLL_PASSES} scroll passes "
                f"(delta={delta_y}) without ever reaching an edge — refusing to plan a capture "
                f"against a possibly-incomplete file list")

        sweep(-2000)
        sweep(2000)
        return list(by_key.values()), rejected, collided

    def list_files(self) -> list:
        """Expand 'All Content' and References sections, sweep the tree, and return files in tree order."""
        self._require_event_view("the content-tree traversal")
        self._scroller.log_geometry()

        self._open_all_content()
        self._bulk_expand_references()
        opened = self._expand_references()
        toggles_found = len(self._toggled)
        outline_rows = self._outline_row_count()

        files, rejected, collided = self._sweep()
        files = ariba_files.order_listing(files)

        self.log(f"    content tree: {len(files)} file(s), {outline_rows} outline row(s) "
                 f"seen, {toggles_found} References toggle(s) found ({opened} expanded)")
        if rejected:
            sample = sorted(rejected)[:10]
            self.log(f"    content tree: {len(rejected)} label(s) not taken for documents "
                     f"(showing {len(sample)}): {sample}")
        self._collided_count = len(collided)
        if collided:
            self.log(f"    content tree: {len(collided)} link(s) indistinguishable from another "
                     f"(same row text, same position, same name) and collapsed: "
                     f"{sorted(collided.values())}")
        return files

    def collided_count(self) -> int:
        """Return the number of indistinguishable links collapsed during `list_files()`."""
        return self._collided_count

    def _match_anchor(self, key: str):
        """Return the unique current DOM anchor matching durable `key`, or None."""
        matches = [a for a in self._read_anchors()
                   if ariba_files.is_document_name(a["name"])
                   and ariba_files.anchor_key(a) == key]
        if len(matches) > 1:
            raise RuntimeError(
                f"{len(matches)} links in the content tree share the identity {key!r} — "
                f"refusing to guess which document to download")
        return matches[0] if matches else None

    def _find_unclaimed(self, file: dict):
        """Find the first unclaimed document anchor matching `file['name']`."""
        anchors = [a for a in self._read_anchors()
                   if ariba_files.is_document_name(a["name"])]
        return ariba_files.pick_unclaimed(anchors, file["name"], self._claimed)

    def _locate_by_handle(self, file: dict):
        """Locate an anchor by its per-session DOM id, verifying its text still matches `file['name']`."""
        handle = (file.get("handle") or "").strip()
        if not handle:
            return None
        try:
            loc = self.page.locator(f'[id="{handle}"]')
            if loc.count() != 1:
                return None
            text = " ".join((loc.first.inner_text() or "").replace("\xa0", " ").split())
            return loc.first if text == file["name"] else None
        except Exception:  # noqa: BLE001
            return None

    def _resolve_anchor(self, file: dict):
        """Resolve the anchor to click for `file`: DOM handle first, then unclaimed-name fallback with scrolling."""
        by_handle = self._locate_by_handle(file)
        if by_handle is not None:
            self._resolved_by_handle += 1
            self._claimed.add(file["handle"])
            return by_handle
        self._resolved_by_key += 1
        self.log(f"    {file['name']}: DOM id {file.get('handle') or '(none)'!r} no longer "
                 f"resolves — falling back to name-among-unclaimed")
        match = self._find_unclaimed(file)
        if match is None:
            for delta in (-3000, 3000):
                for _ in range(self.MAX_SCROLL_PASSES):
                    step = self._scroller.step(delta)
                    match = self._find_unclaimed(file)
                    if match is not None or step["at_edge"] or not step["moved"]:
                        break
                if match is not None:
                    break
        if match is None:
            raise RuntimeError(
                f"{file['name']}: neither its DOM id nor any unclaimed link of that name is in "
                f"the content tree — refusing to download whatever else shares its label")
        self._claimed.add(match.get("id") or "")
        if not self.page.evaluate(
                _TAG_ANCHOR_JS, {"index": match["index"], "name": match["name"]}):
            raise RuntimeError(
                f"{file['name']}: the content tree re-rendered between resolving this link and "
                f"clicking it — refusing rather than clicking a stale position")
        return self.page.locator('a[data-tb-file="1"]').first

    def _await_menu_clear(self, file: dict, timeout_ms: int = 10000) -> None:
        """Wait until no 'Download this attachment' menu item is visible before clicking the next anchor,
        preventing a stale menu from downloading the previous document under the next filename."""
        waited = 0
        while True:
            try:
                item = self.page.get_by_text(_DOWNLOAD_MENU_ITEM, exact=False)
                visible = sum(1 for i in range(item.count()) if item.nth(i).is_visible())
            except Exception:  # noqa: BLE001
                visible = None
            if visible == 0:
                return
            if waited >= timeout_ms:
                raise RuntimeError(
                    f"{file['name']}: a previous attachment's menu is still open "
                    f"({visible if visible is not None else 'an unreadable number of'} "
                    f"visible '{_DOWNLOAD_MENU_ITEM}' item(s)) -- refusing to click the next "
                    f"anchor, which would resolve to the stale menu and save the WRONG "
                    f"document's bytes under this one's name")
            self.page.wait_for_timeout(250)
            waited += 250

    def _await_menu_item(self, file: dict, timeout_ms: int = 15000):
        """Poll for the visible 'Download this attachment' menu item (hidden items exist for every row)."""
        waited = 0
        seen = None
        while True:
            item = self.page.get_by_text(_DOWNLOAD_MENU_ITEM, exact=False)
            try:
                seen = item.count()
                for i in range(seen):
                    candidate = item.nth(i)
                    if candidate.is_visible():
                        return candidate
            except Exception:  # noqa: BLE001
                pass
            if waited >= timeout_ms:
                raise RuntimeError(
                    f"{file['name']}: the menu did not open within {timeout_ms / 1000:.0f}s "
                    f"(no VISIBLE '{_DOWNLOAD_MENU_ITEM}' among "
                    f"{seen if seen is not None else 'an unreadable number of'} candidates)")
            self.page.wait_for_timeout(250)
            waited += 250

    def _dismiss_menu(self) -> None:
        """Best-effort Escape to close the attachment menu (`_await_menu_clear` enforces clearance)."""
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
        except Exception:  # noqa: BLE001
            pass

    def _await_request_idle(self, file: dict, timeout_ms: int = 30000) -> None:
        """Wait for `window.ariba.Request.isRequestInProgress()` to clear after a download (#183);
        Ariba suppresses subsequent menu actions while busy."""
        waited = 0
        while True:
            busy = self.page.evaluate("""() => {
                const request = window.ariba && window.ariba.Request;
                if (!request || typeof request.isRequestInProgress !== 'function') {
                    throw new Error('Ariba request state is unavailable');
                }
                return request.isRequestInProgress();
            }""")
            if busy is False:
                return
            if waited >= timeout_ms:
                raise RuntimeError(
                    f"{file['name']}: Ariba request is still in progress after "
                    f"{timeout_ms / 1000:.0f}s — refusing to click the next document")
            self.page.wait_for_timeout(250)
            waited += 250

    def _popup_owner(self, menu):
        """Resolve a `.awmenu` popup's unique `a[bh="PML"][_mid=...]` trigger."""
        menu_id = menu.get_attribute("id")
        if not menu_id:
            raise RuntimeError("attachment popup has no id — cannot resolve its owner")
        owners = self.page.locator(f'a[bh="PML"][_mid={json.dumps(menu_id)}]')
        if owners.count() != 1:
            raise RuntimeError(
                f"attachment popup {menu_id!r} has {owners.count()} owners — refusing to guess")
        return owners

    def _close_popups(self) -> None:
        """Dismiss open `.awmenu` popups via `window.ariba.Menu.hideActiveMenu()` and wait until hidden."""
        menu_ids = self.page.locator('.awmenu:visible').evaluate_all(
            '(menus) => menus.map(menu => menu.id)')
        if not menu_ids:
            return
        self.page.evaluate("""() => {
            const menu = window.ariba && window.ariba.Menu;
            if (!menu || typeof menu.hideActiveMenu !== 'function') {
                throw new Error('Ariba menu dismissal is unavailable');
            }
            menu.hideActiveMenu();
        }""")
        for menu_id in menu_ids:
            self.page.locator(f'[id={json.dumps(menu_id)}]').wait_for(
                state="hidden", timeout=5000)

    def download(self, file: dict, dest) -> Path:
        """Download one document directly to `dest` (caller manages `.part` staging)."""
        dest = Path(dest)
        self._require_event_view(f"downloading {file['name']}")
        self._await_request_idle(file)
        self._await_menu_clear(file)
        if file.get("kind") != ariba_files.MENU_ITEM:
            self._close_popups()
        link = self._resolve_anchor(file)
        self._ensure_clickable(file, link)

        # PMI items download on direct click without opening a sub-menu.
        if file.get("kind") == ariba_files.MENU_ITEM:
            with self.page.expect_download(timeout=300000) as dl:
                link.click(timeout=15000)
            dl.value.save_as(str(dest))
            return dest

        link.click(timeout=15000)
        try:
            item = self._await_menu_item(file)
            with self.page.expect_download(timeout=300000) as dl:
                item.click()
            dl.value.save_as(str(dest))
        finally:
            self._dismiss_menu()
        return dest

    def _ensure_clickable(self, file: dict, link) -> None:
        """If `link` is hidden inside a mutually exclusive `.awmenu` popup, open its owner trigger first (#183)."""
        if not link.is_visible():
            menu = link.locator(
                'xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), '
                '" awmenu ")][1]')
            if menu.count() != 1:
                raise RuntimeError(
                    f"{file['name']}: hidden control has no unique attachment popup owner")
            owner = self._popup_owner(menu.first)
            self._close_popups()
            owner.click(timeout=5000)
            link.wait_for(state="visible", timeout=10000)
        link.scroll_into_view_if_needed(timeout=10000)


def _capture_with_session_recovery(browser, page, event, dest_dir, log):
    """Retry a single `denied` rejection in a fresh logged-in browser context."""
    try:
        return capture_event(page, event, dest_dir, log=log), page
    except EventUnavailable as exc:
        if exc.reason != "denied":
            raise
    log(f"  Doc{event['document_number']}: denied; retrying once with a fresh login")
    context = browser.new_context(accept_downloads=True)
    try:
        fresh = context.new_page()
        login(fresh, config.ARIBA_USERNAME, config.ARIBA_PASSWORD, log=log)
        saved = capture_event(fresh, event, dest_dir, log=log)
    except Exception:
        context.close()
        raise
    page.context.close()
    return saved, fresh


def capture_attachments(conn, dest_dir=None, log=lambda _m: None, headless=False,
                        virtual_display=False, failures: list | None = None,
                        training_restrictions: list | None = None,
                        only: list[str] | None = None) -> int:
    """Log in and capture+index all open pending solicitations (or those in `only`).

    Skips complete bundles, isolates per-event failures into `failures`, and records verified
    training-event rejections in `training_restrictions` without hiding existing gaps (#223/#242).
    """
    from playwright.sync_api import sync_playwright

    if not (config.ARIBA_USERNAME and config.ARIBA_PASSWORD):
        raise RuntimeError(
            "ARIBA_USERNAME / ARIBA_PASSWORD are unset. Put them in scrapers/.env "
            "(gitignored — the repo is public).")

    dest_dir = Path(dest_dir if dest_dir is not None else config.ARIBA_ATTACHMENTS_DIR)
    events = open_solicitation_events(conn)
    pending = [e for e in events if not bundle_complete(dest_dir, e["document_number"])]
    log(f"  open events: {len(events)}  complete: {len(events) - len(pending)}  "
        f"to capture: {len(pending)}")
    if only is not None:
        wanted = {str(n) for n in only}
        unknown = wanted - {e["document_number"] for e in pending}
        if unknown:
            raise ValueError(
                "not an open event still pending capture (unknown, closed, or already "
                f"complete; give the bare 10-digit number): {', '.join(sorted(unknown))}")
        pending = [e for e in pending if e["document_number"] in wanted]
        log(f"  targeted (--document): {len(pending)}")
    if not pending:
        return 0

    captured = 0
    # Ariba blocks headless login, so headless servers run Chromium under Xvfb.
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
                        last_message = ""

                        def event_log(message):
                            nonlocal last_message
                            last_message = message
                            log(message)

                        restriction = None
                        try:
                            saved, page = _capture_with_session_recovery(
                                browser, page, event, dest_dir, event_log)
                        except EventUnavailable as exc:
                            if (VERIFIED_TRAINING_EVENTS.get(event["document_number"]) != str(event["rfx_id"])
                                    or exc.reason not in _EVENT_REJECTIONS):
                                raise
                            saved = None
                            restriction = exc
                            event_log(f"  {exc} — verified training restriction; remains retryable")
                        if saved is not None:
                            store_bundle(conn, saved, event["document_number"], dest_dir)
                            captured += 1
                        gap = ariba_files.read_omitted(
                            dest_dir / f"Doc{event['document_number']}.zip")
                        if gap["omitted"] or gap["collided"]:
                            reason = (f"unresolved attachment gaps: {gap['omitted']!r}; "
                                      f"{gap['collided']} collapsed link(s)")
                            if saved is None and last_message:
                                reason += f"; {last_message.strip()}"
                            log(f"  Doc{event['document_number']}: FAILED — {reason}")
                            if failures is not None:
                                failures.append((f"ariba:Doc{event['document_number']}", reason))
                        elif restriction is not None and training_restrictions is not None:
                            training_restrictions.append({
                                "document_number": event["document_number"],
                                "rfx_id": str(event["rfx_id"]),
                                "reason": restriction.reason,
                                "detail": str(restriction),
                            })
                    except Exception as exc:
                        log(f"  Doc{event['document_number']}: FAILED — {exc}")
                        if failures is not None:
                            failures.append((f"ariba:Doc{event['document_number']}", str(exc)))
                    log(f"    {i}/{len(pending)}")
            finally:
                browser.close()
    finally:
        if display is not None:
            display.stop()
    return captured
