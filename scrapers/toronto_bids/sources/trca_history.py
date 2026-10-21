"""Preserve pre-2019 public Laserfiche minute books, without model extraction (#224)."""

import hashlib
import io
import json
import re
import time
import zipfile
from collections import deque
from datetime import UTC, datetime

from toronto_bids import config
from toronto_bids.models import BackgroundPdf
from toronto_bids.store import db

BASE = "https://laserfiche.trca.ca/WebLink/"
REPO = "LASERFICHE"
MINUTE_BOOKS = 1030915  # Public 0335 LIBRARY / Corporate / Minute Books series.
KIND = "trca_history"  # Deliberately outside the paid extraction corpora.
PAGE_SIZE = 100


def _data(reply):
    if not isinstance(reply, dict) or not isinstance(reply.get("data"), dict):
        raise ValueError("Laserfiche response lacks data object")
    data = reply["data"]
    if data.get("failed") or data.get("errMsg") or data.get("errorMessage"):
        raise ValueError(f"Laserfiche error: {data.get('errMsg') or data.get('errorMessage')}")
    return data


class PublicArchive:
    """The anonymous UI's HTTP requests, spaced out even while polling exports."""

    def __init__(self, http, interval=1.5):
        self.http = http
        self.interval = interval
        self.last_request = None

    def _pace(self):
        if self.last_request is not None:
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()

    def open(self):
        self._pace()
        html = self.http.get_text(BASE)
        if '"hasExportRights":true' not in html:
            raise ValueError("Anonymous WebLink export is unavailable; no login attempted")

    def post(self, endpoint, payload):
        self._pace()
        return _data(self.http.post_json(BASE + endpoint, json=payload))

    def folder(self, folder_id):
        entries, ids, total = [], set(), None
        while total is None or len(entries) < total:
            page = self.post("FolderListingService.aspx/GetFolderListing2", {
                "repoName": REPO, "folderId": folder_id,
                "getNewListing": not entries, "start": len(entries),
                "end": len(entries) + PAGE_SIZE, "sortColumn": "Name", "sortAscending": True,
            })
            count, rows = page.get("totalEntries"), page.get("results")
            if (type(count) is not int or count < 0 or not isinstance(rows, list)
                    or page.get("folderId") != folder_id):
                raise ValueError(f"Malformed folder listing {folder_id}")
            if total is not None and total != count:
                raise ValueError(f"Folder {folder_id} changed during pagination")
            total = count
            if not rows and len(entries) < total:
                raise ValueError(f"Premature empty page for folder {folder_id}")
            for row in rows:
                if (not isinstance(row, dict) or type(row.get("entryId")) is not int
                        or row["entryId"] <= 0 or row["entryId"] in ids
                        or not isinstance(row.get("name"), str)
                        or type(row.get("type")) is not int):
                    raise ValueError(f"Malformed/duplicate entry in folder {folder_id}")
                ids.add(row["entryId"])
                entries.append(row)
            if len(entries) > total:
                raise ValueError(f"Folder {folder_id} returned more entries than its total")
        return entries

    def pdf(self, entry_id, extension, *, timeout=1800):
        if extension.lower() == "pdf":
            self._pace()
            blob = self.http.get_bytes(document_url(entry_id))
        else:
            # Image-only documents have no native file. Use the same single-document
            # export as Download in the public UI, never a recursive folder export.
            result = self.post("ZipEntriesHandler.aspx/StartExport", {
                "vdirName": "WebLink", "repoName": REPO, "ids": [entry_id],
                "key": -1, "watermarkIdx": -1,
            })
            token = result.get("token")
            if (result.get("needAuditReason") or result.get("needWatermarkSelection")
                    or not isinstance(token, str)
                    or not re.fullmatch(r"[0-9a-fA-F-]{36}", token)):
                raise ValueError("Export requires intervention or lacks a valid token")
            deadline = time.monotonic() + timeout
            while result.get("finished") is not True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Export timed out for entry {entry_id}")
                time.sleep(3)
                result = self.post("ZipEntriesHandler.aspx/CheckExportStatus", {"token": token})
            self._pace()
            exported = self.http.get_bytes(BASE + "ExportJobHandler.aspx/GetExportJob/?token=" + token)
            # Single scanned documents return PDF directly. Also accept an unambiguous
            # one-PDF ZIP; folder exports can silently omit descendants, so never use them.
            # Archive member names never become filesystem paths.
            if exported.startswith(b"%PDF-"):
                blob = exported
            else:
                with zipfile.ZipFile(io.BytesIO(exported)) as archive:
                    files = [info for info in archive.infolist() if not info.is_dir()]
                    if len(files) != 1 or not files[0].filename.lower().endswith(".pdf"):
                        raise ValueError("Single-document export did not contain exactly one PDF")
                    blob = archive.read(files[0])  # CRC checked by zipfile.
        if not blob.startswith(b"%PDF-"):
            raise ValueError(f"Entry {entry_id} returned non-PDF bytes")
        return blob


def document_url(entry_id):
    return BASE + f"ElectronicFile.aspx?docid={entry_id}&dbid=0"


def _save_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def capture_history(conn, http, *, log=print, discover=True, limit=None):
    """Discover all pre-2019 minute-book descendants; download only unheld PDFs.

    Discovery failures are visible, but already indexed downloads still run. Each document
    commits independently. Native non-PDF attachments remain in the inventory as exclusions.
    PDF shortcuts are queued too: public export decides whether their targets are accessible.
    """
    root = config.TRCA_HISTORY_DIR
    root.mkdir(parents=True, exist_ok=True)
    archive = PublicArchive(http)
    archive.open()
    failures = []
    inventory_path = root / "inventory.json"
    prior_inventory = json.loads(inventory_path.read_text()) if inventory_path.exists() else {}
    previous = prior_inventory.get("entries", [])
    inventory_by_id = {item["entryId"]: item for item in previous}
    inventory = list(inventory_by_id.values())
    if discover:
        _save_json(inventory_path, {"entries": inventory, "failures": [],
                                   "discovery_complete": False})
        queue = deque()
        try:
            books = archive.folder(MINUTE_BOOKS)
            if not books:
                raise ValueError("Public minute-book series returned no years")
            for book in books:
                year = re.fullmatch(r"(\d{4}) TRCA Minute Book", book["name"])
                if not year or book["type"] not in (0, 1):
                    raise ValueError(f"Unrecognized minute-book collection: {book['name']}")
                if int(year[1]) < 2019:
                    queue.append((book["entryId"], book["name"]))
            if not queue:
                raise ValueError("No pre-2019 minute-book collections found")
        except Exception as exc:
            failures.append(("discovery", str(exc)))
        visited = set()
        while queue:
            folder_id, path = queue.popleft()
            if folder_id in visited:
                failures.append(("discovery", f"Repeated folder {folder_id}"))
                continue
            visited.add(folder_id)
            try:
                rows = archive.folder(folder_id)
                log(f"Laserfiche {path}: {len(rows)} entries")
                for entry in rows:
                    item = {key: entry.get(key) for key in
                            ("entryId", "name", "type", "extension", "shortcutTargetId")}
                    item["path"] = path + "/" + entry["name"]
                    inventory_by_id[item["entryId"]] = item
                    inventory = list(inventory_by_id.values())
                    if entry["type"] in (0, 1):
                        queue.append((entry["entryId"], item["path"]))
                    elif entry["type"] in (-2, -1):
                        extension = (entry.get("extension") or "").lower()
                        if extension not in ("", "pdf"):
                            log(f"Excluded native non-PDF: {item['path']} ({extension})")
                            continue
                        db.upsert_row(conn, BackgroundPdf(
                            url=document_url(entry["entryId"]), reference=item["path"], kind=KIND,
                        ), overwrite=False)
                    else:
                        failures.append(("discovery", f"Unsupported entry type: {item}"))
                conn.commit()
            except Exception as exc:
                failures.append(("discovery", f"Folder {folder_id} ({path}): {exc}"))
            _save_json(inventory_path, {"entries": inventory, "failures": failures,
                                       "discovery_complete": False})
        _save_json(inventory_path, {"entries": inventory, "failures": failures,
                                   "discovery_complete": not failures})
    elif prior_inventory.get("discovery_complete") is not True:
        failures.extend(prior_inventory.get("failures") or
                        [("discovery", "Incomplete or absent inventory; rerun without --resume")])

    # Reference names do not reliably contain an extension; save the native extension in
    # inventory, then consult it on --resume. The stable URL is the persistent queue key.
    saved = json.loads(inventory_path.read_text())["entries"] if inventory_path.exists() else []
    extensions = {item["entryId"]: ("" if item["type"] == -1 else item.get("extension") or "")
                  for item in saved}
    held = 0
    for row in conn.execute(
        "SELECT id, url FROM background_pdf WHERE kind=? AND sha256 IS NULL ORDER BY id",
        (KIND,),
    ).fetchall():
        if limit is not None and held >= limit:
            break
        try:
            match = re.fullmatch(re.escape(BASE) + r"ElectronicFile\.aspx\?docid=(\d+)&dbid=0", row["url"])
            if not match:
                raise ValueError("Unexpected historical queue URL")
            entry_id = int(match[1])
            if entry_id not in extensions:
                raise ValueError("Missing inventory metadata; run discovery before resuming")
            blob = archive.pdf(entry_id, extensions[entry_id])
            sha = hashlib.sha256(blob).hexdigest()
            path = root / f"{sha}.pdf"
            temp = path.with_suffix(".pdf.tmp")
            temp.write_bytes(blob)
            temp.replace(path)
            conn.execute("UPDATE background_pdf SET sha256=?, local_path=? WHERE id=?",
                         (sha, str(path), row["id"]))
            conn.commit()
            held += 1
            log(f"Held {entry_id}: {len(blob)} bytes, sha256={sha}")
        except Exception as exc:
            failures.append(("download", f"{row['url']}: {exc}; left queued"))
            log(f"FAILED {failures[-1][1]}")
    counts = dict(conn.execute(
        "SELECT COUNT(*) total, COUNT(sha256) held FROM background_pdf WHERE kind=?", (KIND,)
    ).fetchone())
    result = {"at": datetime.now(UTC).isoformat(), "newly_held": held, **counts,
              "pending": counts["total"] - counts["held"], "failures": failures,
              "excluded_native_files": [e for e in saved if e["type"] == -2
                                         and (e.get("extension") or "").lower() not in ("", "pdf")]}
    _save_json(root / "capture.json", result)
    return result
