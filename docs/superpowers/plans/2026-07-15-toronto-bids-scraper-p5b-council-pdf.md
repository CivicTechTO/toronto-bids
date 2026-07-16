# Toronto Bids Scraper — P5b: TMMIS Council + Background-PDF Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich each suspended firm with its full City Council decision and the associated staff-report / communication PDFs, fetched from TMMIS — the richest, most authoritative record of *why* a firm was sanctioned.

**Architecture:** An **opt-in** enrichment (a separate `tb enrich-council` command, kept out of the default browser-free pipeline). It bridges `suspended_firm.council_authority` → the TMMIS agenda-item page (which Akamai only serves to a *real, headed* browser, so a headed Playwright fetch under a virtual display), parses the decision + PDF links (pure lxml), downloads the PDFs over plain HTTP (not Akamai-gated), and extracts their text with `pdftotext`. New `council_item` and `background_pdf` tables link back to the suspended firms.

**Tech Stack:** Python 3.12+, `uv`, `playwright` (headed Chromium), `pyvirtualdisplay` (Xvfb wrapper, new dep), `lxml`, `httpx`, stdlib `hashlib`/`subprocess`, system `pdftotext` (poppler) + `Xvfb`, `pytest`.

## Global Constraints

- **Python 3.12+**, managed with **uv** only. All `uv`/`pytest` from inside `scrapers/`. Package import name `toronto_bids`. Add one new Python dep: `uv add pyvirtualdisplay`.
- **Builds on the merged P0–P5a package.** Reuse, do not duplicate: `store/db.py` (`connect`, `init_db`, `counts`, `upsert_row`, `_upsert_keyed`), `store/schema.sql`, `models.py`, `http.HttpClient` (`_request`, `get_text`, `get_json`), `export/document.py` (`build_export_document`, `_rows`, `_drop`), `cli.py` (`build_parser`, `_open_db`, `main`), `config.py`.
- **OPT-IN / off by default.** The council enrichment is a **separate `tb enrich-council` command**; it is **never** added to `default_sources` or run by `tb sync`. The P0–P5a pipeline stays 100% browser-free and robust. This is the only browser-dependent component in the project.
- **Feasibility is confirmed and fixed (live 2026-07-15):**
  - The agenda-item page `https://secure.toronto.ca/council/agenda-item.do?item={reference}` is **Akamai-gated at the TLS layer**: curl/httpx and **headless Playwright both get HTTP 403**; a **headed** Playwright (real windowed Chromium) gets **HTTP 200**. So the fetch MUST be headed (`headless=False`, `--disable-blink-features=AutomationControlled`).
  - On a headless server there is no real display, so the headed browser runs under a **virtual display (Xvfb)** via `pyvirtualdisplay`. Local macOS runs use the native display (no Xvfb). System prerequisite for servers: `Xvfb` installed (e.g. `apt-get install -y xvfb`).
  - **PDFs are NOT Akamai-gated:** `https://www.toronto.ca/legdocs/mmis/{year}/{committee}/{bgrd|comm}/{file}.pdf` downloads over plain HTTP (200); `pdftotext` (poppler) extracts clean text. System prerequisite: `pdftotext`.
  - The bridge is exact/high-confidence: `suspended_firm.council_authority` (e.g. `2025.GG26.3`) **is** the agenda-item `reference`.
- **Agenda-item page shape:** `<title>` = `Agenda Item History - {reference}`; a `City Council Decision` section holds the decision text; ~9 `<a href>` to `/legdocs/mmis/…/bgrd/backgroundfile-NNNNNN.pdf` (staff reports) and `…/comm/communicationfile-NNNNNN.pdf` (letters), **often duplicated** across committee + council sections (dedup by URL). Classify `kind` by the `/bgrd/` vs `/comm/` path segment.
- **Idempotent, never delete.** `council_item` keyed on `reference`; `background_pdf` keyed on `url`. `first_seen`/`last_seen` on both. Re-running `enrich-council` re-fetches and upserts.
- **Downloaded PDFs** live under `config.DATA_DIR / "documents" / "council"`; store the relative `local_path`, `sha256`, and extracted `text`.
- **No network / no browser in unit tests.** The pure parser and the PDF download+extract are unit-tested (fixture HTML, a tiny fixture PDF, `MockTransport`); the headed-Playwright fetch is validated only by the live smoke. `enrich_council` takes an **injectable `fetch`** so its loop is unit-testable with a stub.

**Reference spec:** `docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md` (§2.3, §3.2, §5, §10 P5).

**Base branch:** `p5b-council` (off `p5-enrichment`).

---

## File Structure

```
scrapers/
  pyproject.toml              # MODIFY: add pyvirtualdisplay (via uv add)
  toronto_bids/
    models.py                 # MODIFY: add CouncilItem, BackgroundPdf
    config.py                 # MODIFY: COUNCIL_ITEM_URL, COUNCIL_DOCS_DIR
    http.py                   # MODIFY: add get_bytes()
    store/
      schema.sql              # MODIFY: council_item + background_pdf tables
      db.py                   # MODIFY: upsert routing + counts
    sources/
      council.py              # CREATE: parse_agenda_item, fetch_agenda_item, download_pdf, enrich_council
    export/
      document.py             # MODIFY: council_item + background_pdf into the export
    cli.py                    # MODIFY: `tb enrich-council` command
  tests/
    fixtures/
      agenda_item.html        # CREATE: real trimmed agenda-item page
      tiny.pdf                # CREATE: a minimal real PDF for download/extract tests
    test_council_parse.py     # CREATE: parse_agenda_item tests
    test_council_download.py  # CREATE: download_pdf + get_bytes tests
    test_council_enrich.py    # CREATE: enrich_council loop (stub fetch) tests
    test_db.py                # MODIFY: council_item / background_pdf upsert + counts
    test_export_document.py   # MODIFY: council data in the export
```

---

### Task 1: `council_item` + `background_pdf` tables + models + store wiring

**Files:**
- Modify: `scrapers/toronto_bids/models.py`
- Modify: `scrapers/toronto_bids/store/schema.sql`
- Modify: `scrapers/toronto_bids/store/db.py`
- Modify: `scrapers/tests/test_db.py`

**Interfaces:**
- Produces: `models.CouncilItem` (`reference: str`, `title/decision_text default None`), `models.BackgroundPdf` (`url: str`, `reference/kind/local_path/sha256/text default None`); `council_item` table (PK `reference`), `background_pdf` table (PK `id` autoincrement, `url` UNIQUE); `db.upsert_row` routes both; `db.counts` includes `"council_item"` and `"background_pdf"`.

- [ ] **Step 1: Add the models**

Append to `scrapers/toronto_bids/models.py`:

```python
@dataclass(frozen=True)
class CouncilItem:
    reference: str
    title: str | None = None
    decision_text: str | None = None


@dataclass(frozen=True)
class BackgroundPdf:
    url: str
    reference: str | None = None
    kind: str | None = None
    local_path: str | None = None
    sha256: str | None = None
    text: str | None = None
```

- [ ] **Step 2: Add the tables**

Append to `scrapers/toronto_bids/store/schema.sql`:

```sql

-- council_item mirrors a TMMIS agenda item (a City Council decision), bridged to
-- suspended_firm via suspended_firm.council_authority = council_item.reference.
CREATE TABLE IF NOT EXISTS council_item (
    reference      TEXT PRIMARY KEY,
    title          TEXT,
    decision_text  TEXT,
    first_seen     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- background_pdf archives a staff-report (bgrd) or communication (comm) PDF linked
-- from a council_item, with its extracted text. Keyed on the URL.
CREATE TABLE IF NOT EXISTS background_pdf (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL UNIQUE,
    reference   TEXT,
    kind        TEXT,
    local_path  TEXT,
    sha256      TEXT,
    text        TEXT,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_background_pdf_reference ON background_pdf (reference);
```

- [ ] **Step 3: Write the failing store tests**

Add to `scrapers/tests/test_db.py` (extend the models import to include `CouncilItem, BackgroundPdf`):

```python
def test_upsert_council_item_is_idempotent(conn):
    from toronto_bids.models import CouncilItem
    it = CouncilItem(reference="2025.GG26.3", title="Suspension of X", decision_text="Adopted.")
    db.upsert_row(conn, it, overwrite=True)
    db.upsert_row(conn, it, overwrite=True)
    assert db.counts(conn)["council_item"] == 1


def test_upsert_background_pdf_is_idempotent(conn):
    from toronto_bids.models import BackgroundPdf
    p = BackgroundPdf(url="https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf",
                      reference="2025.GG26.3", kind="bgrd", sha256="abc", text="REPORT FOR ACTION")
    db.upsert_row(conn, p, overwrite=True)
    db.upsert_row(conn, p, overwrite=True)
    assert db.counts(conn)["background_pdf"] == 1


def test_counts_includes_council_tables(conn):
    c = db.counts(conn)
    assert "council_item" in c and "background_pdf" in c
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_db.py -k "council or background" -v`
Expected: FAIL — models not importable / counts keys missing.

- [ ] **Step 5: Wire the store**

In `scrapers/toronto_bids/store/db.py`:

Extend the models import to add `CouncilItem, BackgroundPdf`.

Add column lists after `_SUPPLIER_COLS`:
```python
_COUNCIL_ITEM_COLS = ["reference", "title", "decision_text"]
_BACKGROUND_PDF_COLS = ["url", "reference", "kind", "local_path", "sha256", "text"]
```

Add branches in `upsert_row` (before the final `else`):
```python
    elif isinstance(row, CouncilItem):
        values = [getattr(row, c) for c in _COUNCIL_ITEM_COLS]
        _upsert_keyed(conn, "council_item", _COUNCIL_ITEM_COLS, values, ["reference"], overwrite)
    elif isinstance(row, BackgroundPdf):
        values = [getattr(row, c) for c in _BACKGROUND_PDF_COLS]
        _upsert_keyed(conn, "background_pdf", _BACKGROUND_PDF_COLS, values, ["url"], overwrite)
```

Add both tables to the `counts` list (before `"sync_run"`):
```python
    tables = ["solicitation", "award", "noncompetitive", "ariba_posting",
              "suspended_firm", "supplier", "council_item", "background_pdf", "sync_run"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_db.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/models.py scrapers/toronto_bids/store scrapers/tests/test_db.py
git commit -m "feat(scraper): add council_item and background_pdf tables, models, store wiring"
```

---

### Task 2: `parse_agenda_item` (pure)

**Files:**
- Create: `scrapers/toronto_bids/sources/council.py`
- Create: `scrapers/tests/fixtures/agenda_item.html`
- Create: `scrapers/tests/test_council_parse.py`

**Interfaces:**
- Produces: `council.parse_agenda_item(html: str, reference: str) -> tuple[CouncilItem, list[dict]]` — the `CouncilItem` (title from `<title>`, `decision_text` from the "City Council Decision" section text) and a **deduplicated** list of `{"url": str, "kind": "bgrd"|"comm"}` for every `/legdocs/mmis/` PDF link.

- [ ] **Step 1: Create the real trimmed fixture**

`scrapers/tests/fixtures/agenda_item.html` (mirrors the real page: title, decision section, duplicated bgrd/comm links):

```html
<html><head><title>Agenda Item History - 2025.GG26.3</title></head>
<body>
<h1>Item - 2025.GG26.3</h1>
<div class="trackingStatus">City Council adopted this item on December 16 and 17, 2025 with amendments.</div>
<h2>City Council Decision</h2>
<div class="decision">
  <p>City Council on December 16 and 17, 2025, adopted the following:</p>
  <p>1. City Council suspend Capital Sewer and any affiliated persons' eligibility to bid on
     or be awarded any City of Toronto contracts for a period of five years.</p>
</div>
<h3>Background Information (Committee)</h3>
<p>(November 26, 2025) Report on Suspension of Capital Sewer Services Inc.
  <a href="https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf">https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf</a></p>
<p>Staff Presentation
  <a href="https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260905.pdf">https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260905.pdf</a></p>
<h3>Communications (Committee)</h3>
<p>(December 4, 2025) Letter from David Beswick
  <a href="https://www.toronto.ca/legdocs/mmis/2025/gg/comm/communicationfile-200766.pdf">https://www.toronto.ca/legdocs/mmis/2025/gg/comm/communicationfile-200766.pdf</a></p>
<h3>Background Information (City Council)</h3>
<p>(duplicate link across sections)
  <a href="https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf">https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf</a></p>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

`scrapers/tests/test_council_parse.py`:

```python
from pathlib import Path

from toronto_bids.models import CouncilItem
from toronto_bids.sources.council import parse_agenda_item

FIXTURES = Path(__file__).parent / "fixtures"


def _html():
    return (FIXTURES / "agenda_item.html").read_text()


def test_parses_item_title_and_decision():
    item, pdfs = parse_agenda_item(_html(), "2025.GG26.3")
    assert isinstance(item, CouncilItem)
    assert item.reference == "2025.GG26.3"
    assert item.title == "Agenda Item History - 2025.GG26.3"
    assert "suspend Capital Sewer" in item.decision_text


def test_dedups_pdf_links_and_classifies_kind():
    _item, pdfs = parse_agenda_item(_html(), "2025.GG26.3")
    urls = {p["url"] for p in pdfs}
    # 3 distinct PDFs (the 4th link duplicates backgroundfile-260581)
    assert len(pdfs) == 3
    assert "https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf" in urls
    kinds = {p["url"].rsplit("/", 2)[-2]: p["kind"] for p in pdfs}
    assert kinds["bgrd"] == "bgrd"
    assert kinds["comm"] == "comm"


def test_missing_decision_section_is_none_safe():
    item, pdfs = parse_agenda_item("<html><head><title>Agenda Item History - X</title></head><body></body></html>", "X")
    assert item.reference == "X"
    assert item.decision_text is None
    assert pdfs == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_council_parse.py -v`
Expected: FAIL — `toronto_bids.sources.council` not importable.

- [ ] **Step 4: Implement the parser**

Create `scrapers/toronto_bids/sources/council.py`:

```python
import re

from lxml import html as _html

from toronto_bids.models import CouncilItem

_LEGDOCS = "/legdocs/mmis/"


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed or None


def parse_agenda_item(html: str, reference: str):
    """Parse a TMMIS agenda-item page into a CouncilItem + a deduped list of PDF links.

    Returns (CouncilItem, [{"url": str, "kind": "bgrd"|"comm"}, ...]).
    """
    root = _html.fromstring(html)

    title = root.xpath("//title/text()")
    title = _clean(title[0]) if title else None

    # Decision text: everything after the "City Council Decision" heading until the next heading.
    decision = None
    heads = root.xpath("//*[self::h1 or self::h2 or self::h3][contains(translate(text(),"
                       "'CITY COUNCIL DECISION','city council decision'),'city council decision')]")
    if heads:
        parts = []
        for sib in heads[0].itersiblings():
            if sib.tag in ("h1", "h2", "h3"):
                break
            parts.append(sib.text_content())
        decision = _clean(" ".join(parts))

    seen = set()
    pdfs = []
    for a in root.xpath("//a[contains(@href, '%s')]" % _LEGDOCS):
        url = a.get("href")
        if not url or not url.lower().endswith(".pdf") or url in seen:
            continue
        seen.add(url)
        kind = "bgrd" if "/bgrd/" in url else ("comm" if "/comm/" in url else "other")
        pdfs.append({"url": url, "kind": kind})

    return CouncilItem(reference=reference, title=title, decision_text=decision), pdfs
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_council_parse.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/sources/council.py scrapers/tests/fixtures/agenda_item.html scrapers/tests/test_council_parse.py
git commit -m "feat(scraper): add agenda-item parser (council decision + PDF links)"
```

---

### Task 3: `get_bytes` + `download_pdf` + `fetch_agenda_item` (headed Playwright)

**Files:**
- Modify: `scrapers/pyproject.toml` (via `uv add pyvirtualdisplay`)
- Modify: `scrapers/toronto_bids/config.py`
- Modify: `scrapers/toronto_bids/http.py`
- Modify: `scrapers/toronto_bids/sources/council.py`
- Create: `scrapers/tests/fixtures/tiny.pdf`
- Create: `scrapers/tests/test_council_download.py`

**Interfaces:**
- Consumes: `HttpClient`, `config.COUNCIL_ITEM_URL`.
- Produces:
  - `HttpClient.get_bytes(url, params=None, headers=None) -> bytes`
  - `council.download_pdf(http, url, dest_dir) -> dict` returning `{"local_path": str, "sha256": str, "text": str}` (downloads via `get_bytes`, writes to `dest_dir`, sha256 of the bytes, `pdftotext` extraction).
  - `council.fetch_agenda_item(reference, virtual_display=False) -> str` — headed-Playwright HTML fetch (live only; not unit-tested).
  - `config.COUNCIL_ITEM_URL`, `config.COUNCIL_DOCS_DIR`.

- [ ] **Step 1: Add the dep + config + get_bytes**

Run:
```bash
cd scrapers && uv add pyvirtualdisplay
```

Append to `scrapers/toronto_bids/config.py`:
```python
# TMMIS council agenda-item pages (Akamai-gated -> headed browser). Query param: ?item=<reference>.
COUNCIL_ITEM_URL = "https://secure.toronto.ca/council/agenda-item.do"
# Downloaded council PDFs.
COUNCIL_DOCS_DIR = DATA_DIR / "documents" / "council"
```

Add to `scrapers/toronto_bids/http.py` (after `get_text`):
```python
    def get_bytes(self, url, params=None, headers=None) -> bytes:
        return self._request("GET", url, params=params, headers=headers).content
```

- [ ] **Step 2: Create a tiny real PDF fixture**

Create `scrapers/tests/fixtures/tiny.pdf` — a minimal valid one-line PDF (generate it, don't hand-type binary):
```bash
cd scrapers && uv run python -c "
import subprocess, pathlib
# Use pdftotext's sibling: write a trivial PDF via reportlab-free minimal bytes.
pdf = b'''%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 44>>stream
BT /F1 12 Tf 20 100 Td (HELLO PDF) Tj ET
endstream endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f 
trailer<</Root 1 0 R/Size 6>>
startxref
0
%%EOF'''
pathlib.Path('tests/fixtures/tiny.pdf').write_bytes(pdf)
print('wrote tiny.pdf', pathlib.Path('tests/fixtures/tiny.pdf').stat().st_size, 'bytes')
print('pdftotext says:', subprocess.run(['pdftotext','tests/fixtures/tiny.pdf','-'],capture_output=True,text=True).stdout.strip())
"
```
Expected: prints the byte size and `pdftotext says: HELLO PDF` (confirming the fixture is a valid, extractable PDF). If `pdftotext` extracts nothing from this minimal PDF, regenerate using `uv run python` with `reportlab` (`uv add --dev reportlab`) to produce a guaranteed-extractable PDF; then the test below asserts on that text.

- [ ] **Step 3: Write the failing download tests**

`scrapers/tests/test_council_download.py`:

```python
from pathlib import Path

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.sources.council import download_pdf

FIXTURES = Path(__file__).parent / "fixtures"


def _http_serving(pdf_bytes):
    def handler(request):
        return httpx.Response(200, content=pdf_bytes)
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


def test_get_bytes_returns_body_bytes():
    http = _http_serving(b"\x89PDFdata")
    assert http.get_bytes("https://example.test/x") == b"\x89PDFdata"


def test_download_pdf_saves_hashes_and_extracts(tmp_path):
    pdf = (FIXTURES / "tiny.pdf").read_bytes()
    http = _http_serving(pdf)
    result = download_pdf(http,
                          "https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf",
                          tmp_path)
    saved = Path(result["local_path"])
    assert saved.exists() and saved.read_bytes() == pdf
    import hashlib
    assert result["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert "HELLO PDF" in result["text"]  # pdftotext extracted the fixture's text
```

- [ ] **Step 4: Run the download tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_council_download.py -v`
Expected: FAIL — `download_pdf` / `get_bytes` not defined.

- [ ] **Step 5: Implement `download_pdf` and `fetch_agenda_item`**

Add to `scrapers/toronto_bids/sources/council.py` (imports at top, functions below the parser):

```python
import hashlib
import subprocess
from pathlib import Path

from toronto_bids import config


def download_pdf(http, url: str, dest_dir) -> dict:
    """Download a PDF over plain HTTP, save it, hash it, and extract its text with pdftotext."""
    data = http.get_bytes(url)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1]
    path = dest_dir / name
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    proc = subprocess.run(["pdftotext", "-q", str(path), "-"], capture_output=True, text=True)
    text = proc.stdout.strip() or None
    return {"local_path": str(path), "sha256": digest, "text": text}


def fetch_agenda_item(reference: str, virtual_display: bool = False) -> str:
    """Fetch a TMMIS agenda-item page's HTML with a HEADED Chromium (Akamai blocks headless).

    On a headless server pass virtual_display=True to run Chromium under Xvfb (needs Xvfb
    installed). Not unit-tested — exercised by the live smoke.
    """
    from playwright.sync_api import sync_playwright

    display = None
    if virtual_display:
        from pyvirtualdisplay import Display
        display = Display(visible=False, size=(1440, 900))
        display.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False, args=["--disable-blink-features=AutomationControlled"]
            )
            try:
                page = browser.new_context().new_page()
                page.goto(f"{config.COUNCIL_ITEM_URL}?item={reference}",
                          wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1500)
                return page.content()
            finally:
                browser.close()
    finally:
        if display is not None:
            display.stop()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_council_download.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add scrapers/pyproject.toml scrapers/uv.lock scrapers/toronto_bids/config.py scrapers/toronto_bids/http.py scrapers/toronto_bids/sources/council.py scrapers/tests/fixtures/tiny.pdf scrapers/tests/test_council_download.py
git commit -m "feat(scraper): add get_bytes, PDF download+extract, headed agenda-item fetch"
```

---

### Task 4: `enrich_council` loop + `tb enrich-council` CLI + export + live smoke + docs

**Files:**
- Modify: `scrapers/toronto_bids/sources/council.py` (add `enrich_council`)
- Modify: `scrapers/toronto_bids/cli.py`
- Modify: `scrapers/toronto_bids/export/document.py`
- Create: `scrapers/tests/test_council_enrich.py`
- Modify: `scrapers/tests/test_export_document.py`
- Modify: `scrapers/README.md`

**Interfaces:**
- Consumes: `parse_agenda_item`, `download_pdf`, `fetch_agenda_item`, `db.upsert_row`, `CouncilItem`, `BackgroundPdf`, `build_export_document`.
- Produces:
  - `council.enrich_council(conn, http, fetch=fetch_agenda_item, dest_dir=None) -> int` — loops distinct non-empty `suspended_firm.council_authority`; for each, `fetch` the HTML, `parse_agenda_item`, upsert the `CouncilItem`, then `download_pdf` + upsert a `BackgroundPdf` per link. Returns the number of council items processed. `fetch` is injectable (unit-testable). Idempotent.
  - `tb enrich-council [--virtual-display]` CLI command.
  - export: top-level `council_items` (each with its `background_pdfs` nested) + `suspended_firms` gain a `council` link.

- [ ] **Step 1: Write the failing enrich test (stub fetch — no browser/network)**

`scrapers/tests/test_council_enrich.py`:

```python
from pathlib import Path

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.models import SuspendedFirm
from toronto_bids.sources.council import enrich_council
from toronto_bids.store import db

FIXTURES = Path(__file__).parent / "fixtures"


def _stub_fetch(reference):
    # Return the fixture HTML regardless of reference (simulates the headed fetch).
    return (FIXTURES / "agenda_item.html").read_text()


def _http_pdf():
    pdf = (FIXTURES / "tiny.pdf").read_bytes()
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=pdf))), backoff=0.0)


def test_enrich_builds_council_item_and_pdfs(conn, tmp_path):
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Capital Sewer", council_authority="2025.GG26.3",
                                      source="suspended_firms"), overwrite=True)
    conn.commit()
    n = enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)
    assert n == 1
    assert db.counts(conn)["council_item"] == 1
    # 3 distinct PDFs from the fixture, each downloaded + extracted
    assert db.counts(conn)["background_pdf"] == 3
    row = conn.execute("SELECT text FROM background_pdf LIMIT 1").fetchone()
    assert "HELLO PDF" in row["text"]


def test_enrich_skips_blank_authority_and_is_idempotent(conn, tmp_path):
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="No Auth", council_authority="",
                                      source="suspended_firms"), overwrite=True)
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Capital Sewer", council_authority="2025.GG26.3",
                                      source="suspended_firms"), overwrite=True)
    conn.commit()
    enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)
    enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)  # re-run
    assert db.counts(conn)["council_item"] == 1      # only the non-blank authority
    assert db.counts(conn)["background_pdf"] == 3     # no duplicate PDFs on re-run
```

- [ ] **Step 2: Run the enrich test to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_council_enrich.py -v`
Expected: FAIL — `enrich_council` not defined.

- [ ] **Step 3: Implement `enrich_council`**

Add to `scrapers/toronto_bids/sources/council.py` (add the model + db imports at the top):

```python
from toronto_bids.models import BackgroundPdf
from toronto_bids.store import db


def enrich_council(conn, http, fetch=fetch_agenda_item, dest_dir=None) -> int:
    """Fetch + archive the council decision and PDFs for each suspended firm's Authority.

    `fetch(reference) -> html` is injectable so the loop is testable without a browser.
    Idempotent. Returns the number of council items processed.
    """
    dest_dir = dest_dir if dest_dir is not None else config.COUNCIL_DOCS_DIR
    refs = [r["council_authority"] for r in conn.execute(
        "SELECT DISTINCT council_authority FROM suspended_firm "
        "WHERE council_authority IS NOT NULL AND council_authority != ''"
    )]
    processed = 0
    for ref in refs:
        html = fetch(ref)
        item, pdfs = parse_agenda_item(html, ref)
        db.upsert_row(conn, item, overwrite=True)
        for pdf in pdfs:
            info = download_pdf(http, pdf["url"], dest_dir)
            db.upsert_row(conn, BackgroundPdf(
                url=pdf["url"], reference=ref, kind=pdf["kind"],
                local_path=info["local_path"], sha256=info["sha256"], text=info["text"],
            ), overwrite=True)
        conn.commit()
        processed += 1
    return processed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_council_enrich.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing export test**

Add to `scrapers/tests/test_export_document.py`:

```python
def test_export_has_council_items_with_nested_pdfs(conn):
    from toronto_bids.models import CouncilItem, BackgroundPdf
    from toronto_bids.store import db as _db
    _db.upsert_row(conn, CouncilItem(reference="2025.GG26.3", title="Suspension",
                                     decision_text="Adopted."), overwrite=True)
    _db.upsert_row(conn, BackgroundPdf(url="https://x/bgrd/backgroundfile-260581.pdf",
                                       reference="2025.GG26.3", kind="bgrd", text="REPORT"),
                   overwrite=True)
    conn.commit()
    doc = build_export_document(conn, generated_at="t")
    assert len(doc["council_items"]) == 1
    ci = doc["council_items"][0]
    assert ci["reference"] == "2025.GG26.3"
    assert len(ci["background_pdfs"]) == 1
    assert ci["background_pdfs"][0]["kind"] == "bgrd"
    assert "text" not in ci["background_pdfs"][0]  # bulky extracted text excluded from the export


def test_export_council_items_empty_when_none(conn):
    doc = build_export_document(conn, generated_at="t")
    assert doc["council_items"] == []
```

- [ ] **Step 6: Add council data to the export**

In `scrapers/toronto_bids/export/document.py`, before the `return`, add (group PDFs under their item; drop the bulky `text` and internal `id` from the export — the text stays in the DB):

```python
    pdfs_by_ref: dict[str, list] = {}
    for pdf in _rows(conn, "SELECT * FROM background_pdf ORDER BY reference, url"):
        pdfs_by_ref.setdefault(pdf["reference"], []).append(_drop(pdf, "id", "text"))

    council_items = []
    for ci in _rows(conn, "SELECT * FROM council_item ORDER BY reference"):
        ci["background_pdfs"] = pdfs_by_ref.get(ci["reference"], [])
        council_items.append(ci)
```

Add `"council_items": council_items,` to the returned dict (alongside `suspended_firms`).

- [ ] **Step 7: Add the `enrich-council` CLI command**

In `scrapers/toronto_bids/cli.py`:

Register the subcommand in `build_parser` (after `export`):
```python
    p_enrich = sub.add_parser("enrich-council",
                              help="OPT-IN: fetch council decisions + staff-report PDFs for suspended firms (headed browser)")
    p_enrich.add_argument("--virtual-display", action="store_true",
                          help="Run the headed browser under Xvfb (headless servers; needs Xvfb installed)")
```

Add the handler (after `_cmd_export`):
```python
def _cmd_enrich_council(args) -> int:
    from functools import partial

    from toronto_bids.sources.council import enrich_council, fetch_agenda_item

    conn = _open_db()
    http = HttpClient()
    fetch = partial(fetch_agenda_item, virtual_display=args.virtual_display)
    try:
        n = enrich_council(conn, http, fetch=fetch)
        counts = db.counts(conn)
        print(f"Enriched {n} council items; background_pdf={counts['background_pdf']}")
    finally:
        http.close()
        conn.close()
    return 0
```

Add the dispatch in `main` (before the `print_help` fallthrough):
```python
    if args.command == "enrich-council":
        return _cmd_enrich_council(args)
```

- [ ] **Step 8: Run the full suite**

Run: `cd scrapers && uv run pytest -v`
Expected: every test PASSES, output pristine.

- [ ] **Step 9: Live smoke check (headed browser — network — manual, not a test)**

This uses a **real headed Chromium** (a window will open on macOS). It requires a synced DB with suspended firms.
```bash
cd scrapers && TB_DATA_DIR=/tmp/tb-p5b uv run tb sync --only suspended_firms
cd scrapers && TB_DATA_DIR=/tmp/tb-p5b uv run tb enrich-council
```
Expected: prints `Enriched <N> council items; background_pdf=<M>` (N ≈ the number of suspended firms with an Authority, ~3; M several). Then verify real content was archived:
```bash
sqlite3 /tmp/tb-p5b/bids.sqlite "SELECT reference, substr(title,1,40), length(decision_text) FROM council_item;"
sqlite3 /tmp/tb-p5b/bids.sqlite "SELECT reference, kind, sha256, substr(text,1,60) FROM background_pdf LIMIT 3;"
```
Expected: real references (e.g. `2025.GG26.3`), non-empty decision text, and PDF rows whose `text` starts like `REPORT FOR ACTION …`. Record the actual numbers in the report. On a headless server the equivalent is `uv run tb enrich-council --virtual-display` (with Xvfb installed). Do not block the commit on exact counts.

- [ ] **Step 10: Update the README**

In `scrapers/README.md`, add an **opt-in enrichment** note:
```markdown
- **Council enrichment** (`tb enrich-council`, OPT-IN) — for each suspended firm, fetches its
  City Council decision from TMMIS and the linked staff-report / communication PDFs
  (`council_item` + `background_pdf` tables, with extracted text). TMMIS is Akamai-gated and
  only served to a **real, headed browser**, so this command drives a headed Chromium
  (Playwright); the PDFs themselves download over plain HTTP + `pdftotext`. It is **not** part
  of `tb sync` — the core pipeline stays browser-free. On a headless server run
  `tb enrich-council --virtual-display` with `Xvfb` installed (`apt-get install -y xvfb`);
  `pdftotext` (poppler) is also required.
```

- [ ] **Step 11: Commit**

```bash
git add scrapers/toronto_bids/sources/council.py scrapers/toronto_bids/cli.py scrapers/toronto_bids/export/document.py scrapers/tests/test_council_enrich.py scrapers/tests/test_export_document.py scrapers/README.md
git commit -m "feat(scraper): add tb enrich-council (headed) + council export; opt-in"
```

---

## Self-Review

**1. Spec coverage (design §2.3, §3.2, §5, §10 P5b):**
- TMMIS agenda-item fetch, Akamai-gated → headed browser (§2.3) → Task 3 (`fetch_agenda_item`, headed + virtual-display). ✓
- Background-file PDFs via plain HTTP + pdftotext (§2.3) → Task 3 (`download_pdf`). ✓
- suspended↔council bridge on the Authority reference (§3.2) → Task 4 (`enrich_council` loops `council_authority`). ✓
- `council_item` + `background_pdf` tables (§5) → Task 1. ✓
- Opt-in, out of the default pipeline (core stays browser-free) → Task 4 (`tb enrich-council` separate command; never in `default_sources`). ✓
- Virtual-display path for headless servers → Task 3 (`virtual_display` via pyvirtualdisplay) + Task 4 (`--virtual-display`) + README. ✓
- Idempotent, never delete → Tasks 1, 4 (`council_item`/`background_pdf` keyed; re-run upserts). ✓
- Export inclusion, bulky text excluded → Task 4. ✓
- Out of scope by design (broad award→council linkage — awards carry no council ref; true fuzzy) → not in this plan. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step shows complete code; every command shows expected output. The `fetch_agenda_item` headed browser is explicitly live-only (not unit-tested), with the loop made testable via injectable `fetch`. The `tiny.pdf` step includes a fallback (reportlab) if the minimal PDF isn't extractable. ✓

**3. Type consistency:** `parse_agenda_item(html, reference) -> (CouncilItem, list[dict])` identical across Tasks 2, 4. `download_pdf(http, url, dest_dir) -> {"local_path","sha256","text"}` identical across Tasks 3, 4. `fetch_agenda_item(reference, virtual_display=False) -> str` matches the `enrich_council` default + the CLI `partial`. `enrich_council(conn, http, fetch=…, dest_dir=…)` matches its tests and the CLI caller. `CouncilItem`/`BackgroundPdf` fields match the dataclasses (Task 1), `_COUNCIL_ITEM_COLS`/`_BACKGROUND_PDF_COLS` (Task 1), the parser/enrich constructors (Tasks 2, 4), and the export (Task 4). `db.counts` gains `council_item`/`background_pdf` (Task 1), rendered by `tb status` and used in the CLI print. `HttpClient.get_bytes` matches its sole caller in `download_pdf`. The export `council_items[].background_pdfs` shape is used identically in the builder and its test. ✓
