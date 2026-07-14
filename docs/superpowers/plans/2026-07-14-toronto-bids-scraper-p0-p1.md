# Toronto Bids Scraper — P0 + P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the robust, browser-free, auth-free core of the rewritten Toronto Bids scraper: a `uv`-managed Python package that pulls the OData solicitation spine and the three `tobids-*` CKAN datasets into a normalized SQLite store, all keyed on the normalized 10-digit `document_number`.

**Architecture:** A source-adapter pipeline. Each source implements a uniform `fetch() → normalize()` interface; `fetch()` does network I/O and `normalize()` is a pure function that turns raw records into typed dataclass rows. A `pipeline` orchestrator runs each source in isolation, routes rows to idempotent SQLite upserts, and records a `sync_run` per source. OData is the authoritative spine (its non-null fields win); CKAN backfills gaps only.

**Tech Stack:** Python 3.12+, `uv`, `httpx` (HTTP/2 client), SQLite (stdlib `sqlite3`), `pytest`. No browser, no auth, no cloud in this scope.

## Global Constraints

- **Python 3.12+**, managed with **uv** only (`uv add`, `uv run`). No conda, no `pip install`, no `environment.yml`. Do not pin dependency versions unless a concrete reason exists.
- The package lives at **`scrapers/`** with `scrapers/pyproject.toml`. All `uv`/`pytest` commands run from inside `scrapers/`.
- Package import name: **`toronto_bids`**. CLI entry point: **`tb`** (`[project.scripts] tb = "toronto_bids.cli:main"`), run as `uv run tb ...`.
- **The join key is the normalized 10-digit `document_number`**: strip non-digits → require exactly 10 digits → reject a placeholder denylist. Defined once in `toronto_bids/linking/document_number.py`; every source uses it.
- **Rows are never deleted.** Every table has `first_seen` / `last_seen` (TEXT, `datetime('now')`); upserts touch `last_seen`.
- **OData is the spine** (upsert with `overwrite=True` — its non-null fields win). **CKAN backfills** (upsert with `overwrite=False` — fills only NULL columns). Adapters normalize empty strings `""` and empty arrays `[]` to Python `None` so the COALESCE logic works.
- **Non-competitive awards are a separate keyspace** (`workspace_number`); they never join to `solicitation`.
- **Per-source isolation:** one source failing must not abort the run; record the failure in `sync_run` and continue.
- CKAN resource UUIDs **rotate on refresh** — always resolve them at runtime via `package_show?id=<slug>`; never hardcode a resource UUID.
- CKAN API base: `https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/`
- OData base: `https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/`

**Reference spec:** `docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md`

---

## File Structure

Created in this plan (all under `scrapers/`):

```
scrapers/
  pyproject.toml                      # uv project + deps + [project.scripts] tb
  .gitignore                          # ignore files/, .venv/, __pycache__
  toronto_bids/
    __init__.py                       # __version__
    config.py                         # URLs, slugs, DATA_DIR, DB_PATH, USER_AGENT, timeouts
    http.py                           # HttpClient: httpx wrapper with UA + retry/backoff
    cli.py                            # argparse: `tb sync`, `tb status`
    pipeline.py                       # orchestrate sources -> upsert -> sync_run
    models.py                         # Solicitation, Award, NonCompetitive dataclasses
    store/
      __init__.py
      schema.sql                      # solicitation, award, noncompetitive, sync_run
      db.py                           # connect/init, upsert_row (type-dispatched), counts, sync_run
    sources/
      __init__.py
      base.py                         # Source protocol + Row type alias
      ckan.py                         # CkanSource: resolve resource, fetch datastore, normalize
      odata.py                        # ODataSource: paginated fetch, normalize spine + noncomp
    linking/
      __init__.py
      document_number.py              # normalize_document_number
  tests/
    __init__.py
    conftest.py                       # fixtures dir helper, in-memory db helper
    fixtures/
      ckan_awarded.json
      ckan_solicitations.json
      ckan_noncompetitive.json
      odata_solicitation.json
      odata_noncompetitive.json
    test_document_number.py
    test_db.py
    test_http.py
    test_ckan.py
    test_odata.py
    test_pipeline.py
```

The old flat scripts in `scrapers/` (`rfp_scraper.py`, `ariba_driver.py`, `open_data.py`, `filemanage.py`, `transmit_json.py`, `azurefileshare.py`, `secret_manager.py`, `slack.py`, `config.json`, `environment.yml`, `Dockerfile`, `build_docker.sh`, `entrypoint.sh`) are removed in Task 10.

---

### Task 1: Project scaffold + CLI skeleton

**Files:**
- Create: `scrapers/pyproject.toml`
- Create: `scrapers/.gitignore`
- Create: `scrapers/toronto_bids/__init__.py`
- Create: `scrapers/toronto_bids/cli.py`
- Create: `scrapers/tests/__init__.py`
- Create: `scrapers/tests/test_smoke.py`

**Interfaces:**
- Produces: `toronto_bids.__version__` (str); `toronto_bids.cli.main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Create the uv project files**

`scrapers/pyproject.toml`:

```toml
[project]
name = "toronto-bids"
version = "0.1.0"
description = "Scraper for City of Toronto procurement data"
requires-python = ">=3.12"
dependencies = [
    "httpx",
]

[project.scripts]
tb = "toronto_bids.cli:main"

[dependency-groups]
dev = [
    "pytest",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["toronto_bids"]
```

`scrapers/.gitignore`:

```
.venv/
__pycache__/
*.pyc
files/
.pytest_cache/
```

`scrapers/toronto_bids/__init__.py`:

```python
__version__ = "0.1.0"
```

`scrapers/tests/__init__.py`: empty file.

- [ ] **Step 2: Write the CLI skeleton**

`scrapers/toronto_bids/cli.py`:

```python
import argparse

from toronto_bids import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tb", description="Toronto Bids scraper")
    parser.add_argument("--version", action="version", version=f"tb {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Write the failing smoke test**

`scrapers/tests/test_smoke.py`:

```python
from toronto_bids import __version__
from toronto_bids.cli import main


def test_version_is_a_string():
    assert isinstance(__version__, str)


def test_main_with_no_args_returns_zero():
    assert main([]) == 0
```

- [ ] **Step 4: Sync the environment and run the test (expect PASS)**

Run:
```bash
cd scrapers && uv sync && uv run pytest tests/test_smoke.py -v
```
Expected: `uv sync` creates `.venv`; both tests PASS.

- [ ] **Step 5: Verify the CLI entry point**

Run:
```bash
cd scrapers && uv run tb --version
```
Expected: prints `tb 0.1.0`.

- [ ] **Step 6: Commit**

```bash
git add scrapers/pyproject.toml scrapers/.gitignore scrapers/toronto_bids scrapers/tests
git commit -m "feat(scraper): scaffold uv project and CLI skeleton"
```

---

### Task 2: `normalize_document_number`

The single join-key rule. Pure function, no dependencies. This is the most important primitive in the whole system.

**Files:**
- Create: `scrapers/toronto_bids/linking/__init__.py` (empty)
- Create: `scrapers/toronto_bids/linking/document_number.py`
- Create: `scrapers/tests/test_document_number.py`

**Interfaces:**
- Produces: `normalize_document_number(raw: str | None) -> str | None` — returns a canonical 10-digit string, or `None` if the input cannot yield a valid key.

- [ ] **Step 1: Write the failing tests (real dirty values from the spec)**

`scrapers/tests/test_document_number.py`:

```python
import pytest

from toronto_bids.linking.document_number import normalize_document_number


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3303123110", "3303123110"),            # already clean
        ("Doc5725384704", "5725384704"),         # Ariba Doc-prefixed
        ("3303-12-3110", "3303123110"),          # hyphenated in free text
        ("Doc5581608073 - Request for Quotations", "5581608073"),  # embedded in title
        ("﻿3674586673", "3674586673"),      # leading BOM
        ("4147794028﻿", "4147794028"),      # trailing BOM
        ("2821040966 )", "2821040966"),          # trailing junk
    ],
)
def test_valid_document_numbers_normalize_to_ten_digits(raw, expected):
    assert normalize_document_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "xxxxxxxx",                 # placeholder
        "xxxxxxx",
        "390513test",               # <10 digits after strip
        "Notice913418",
        "Summary67141",
        "No. 22436",
        "1111111111",               # denylisted test row
        "3.77E+1100",               # Excel scientific-notation corruption
        "3710106+0111",             # digits present but 11 after strip -> reject
        "123",                      # too short
        "123456789012",             # too long (12 digits)
    ],
)
def test_invalid_document_numbers_return_none(raw):
    assert normalize_document_number(raw) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_document_number.py -v`
Expected: FAIL with `ModuleNotFoundError: toronto_bids.linking.document_number`.

- [ ] **Step 3: Implement the function**

`scrapers/toronto_bids/linking/__init__.py`: empty file.

`scrapers/toronto_bids/linking/document_number.py`:

```python
import re

# Placeholder / junk values that survive digit-stripping but are not real doc numbers.
_DENYLIST = {"1111111111", "0000000000", "1234567890"}

_NON_DIGIT = re.compile(r"\D")


def normalize_document_number(raw: str | None) -> str | None:
    """Return the canonical 10-digit document number, or None if not derivable.

    Rule (see spec §3.3): strip all non-digits, require exactly 10 digits,
    reject a placeholder denylist. Excel scientific-notation corruption
    (e.g. "3.77E+1100") is unrecoverable and rejected because it does not
    strip to exactly 10 digits.
    """
    if raw is None:
        return None
    digits = _NON_DIGIT.sub("", str(raw))
    if len(digits) != 10:
        return None
    if digits in _DENYLIST:
        return None
    return digits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_document_number.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/linking scrapers/tests/test_document_number.py
git commit -m "feat(scraper): add normalize_document_number join-key rule"
```

---

### Task 3: Data models + SQLite store

Canonical row dataclasses, the schema, and an idempotent, type-dispatched upsert with the spine/backfill overwrite semantics.

**Files:**
- Create: `scrapers/toronto_bids/models.py`
- Create: `scrapers/toronto_bids/config.py`
- Create: `scrapers/toronto_bids/store/__init__.py` (empty)
- Create: `scrapers/toronto_bids/store/schema.sql`
- Create: `scrapers/toronto_bids/store/db.py`
- Create: `scrapers/tests/conftest.py`
- Create: `scrapers/tests/test_db.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `models.Solicitation`, `models.Award`, `models.NonCompetitive` (frozen dataclasses; field lists below).
  - `config.DATA_DIR: Path`, `config.DB_PATH: Path`, `config.CKAN_BASE: str`, `config.ODATA_BASE: str`, `config.USER_AGENT: str`, `config.HTTP_TIMEOUT: float`, `config.HTTP_RETRIES: int`, plus dataset slug constants.
  - `db.connect(path: Path | str) -> sqlite3.Connection`
  - `db.init_db(conn) -> None`
  - `db.upsert_row(conn, row, *, overwrite: bool) -> None` (dispatches on row type)
  - `db.counts(conn) -> dict[str, int]`
  - `db.start_sync_run(conn, source: str) -> int` and `db.finish_sync_run(conn, run_id, *, status, rows_fetched, rows_upserted, error=None) -> None`

- [ ] **Step 1: Write the models and config**

`scrapers/toronto_bids/models.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Solicitation:
    document_number: str
    status: str | None = None
    rfx_type: str | None = None
    noip_type: str | None = None
    title: str | None = None
    description: str | None = None
    issue_date: str | None = None
    submission_deadline: str | None = None
    category: str | None = None
    division: str | None = None
    buyer_name: str | None = None
    buyer_email: str | None = None
    buyer_phone: str | None = None
    wards: str | None = None
    ariba_posting_link: str | None = None
    odata_id: str | None = None
    source: str = ""


@dataclass(frozen=True)
class Award:
    document_number: str
    supplier_name_raw: str | None = None
    award_amount: str | None = None
    award_date: str | None = None
    source: str = ""


@dataclass(frozen=True)
class NonCompetitive:
    workspace_number: str
    supplier_name_raw: str | None = None
    reason: str | None = None
    contract_amount: str | None = None
    contract_date: str | None = None
    division: str | None = None
    council_authority_link: str | None = None
    odata_id: str | None = None
    source: str = ""
```

`scrapers/toronto_bids/config.py`:

```python
import os
from pathlib import Path

# Data directory: scrapers/files/ by default, overridable for tests / deployment.
DATA_DIR = Path(os.environ.get("TB_DATA_DIR", Path(__file__).resolve().parent.parent / "files"))
DB_PATH = DATA_DIR / "bids.sqlite"

CKAN_BASE = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/"
ODATA_BASE = "https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/"

# CKAN dataset slugs (resource UUIDs are resolved at runtime, never hardcoded).
CKAN_AWARDED_SLUG = "tobids-awarded-contracts"
CKAN_OPEN_SLUG = "tobids-all-open-solicitations"
CKAN_NONCOMP_SLUG = "tobids-non-competitive-contracts"

# OData entity sets.
ODATA_SOLICITATIONS = "feis_solicitation_published"
ODATA_NONCOMPETITIVE = "feis_non_competitive_published"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
HTTP_TIMEOUT = 60.0
HTTP_RETRIES = 4
```

- [ ] **Step 2: Write the schema**

`scrapers/toronto_bids/store/__init__.py`: empty file.

`scrapers/toronto_bids/store/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS solicitation (
    document_number      TEXT PRIMARY KEY,
    status               TEXT,
    rfx_type             TEXT,
    noip_type            TEXT,
    title                TEXT,
    description          TEXT,
    issue_date           TEXT,
    submission_deadline  TEXT,
    category             TEXT,
    division             TEXT,
    buyer_name           TEXT,
    buyer_email          TEXT,
    buyer_phone          TEXT,
    wards                TEXT,
    ariba_posting_link   TEXT,
    odata_id             TEXT,
    source               TEXT,
    first_seen           TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS award (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    document_number    TEXT NOT NULL,
    supplier_name_raw  TEXT,
    supplier_id        INTEGER,
    award_amount       TEXT,
    award_date         TEXT,
    source             TEXT,
    first_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (document_number, supplier_name_raw, source)
);

CREATE INDEX IF NOT EXISTS idx_award_docnum ON award (document_number);

CREATE TABLE IF NOT EXISTS noncompetitive (
    workspace_number        TEXT PRIMARY KEY,
    supplier_name_raw       TEXT,
    supplier_id             INTEGER,
    reason                  TEXT,
    contract_amount         TEXT,
    contract_date           TEXT,
    division                TEXT,
    council_authority_link  TEXT,
    odata_id                TEXT,
    source                  TEXT,
    first_seen              TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,
    rows_fetched   INTEGER DEFAULT 0,
    rows_upserted  INTEGER DEFAULT 0,
    error          TEXT
);
```

- [ ] **Step 3: Write the failing tests**

`scrapers/tests/conftest.py`:

```python
from pathlib import Path

import pytest

from toronto_bids.store import db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()
```

`scrapers/tests/test_db.py`:

```python
from toronto_bids.models import Award, NonCompetitive, Solicitation
from toronto_bids.store import db


def test_init_creates_tables(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"solicitation", "award", "noncompetitive", "sync_run"} <= names


def test_upsert_solicitation_is_idempotent(conn):
    sol = Solicitation(document_number="3303123110", status="Awarded", source="odata")
    db.upsert_row(conn, sol, overwrite=True)
    db.upsert_row(conn, sol, overwrite=True)
    assert db.counts(conn)["solicitation"] == 1


def test_overwrite_true_lets_new_nonnull_win(conn):
    db.upsert_row(conn, Solicitation("3303123110", title=None, source="ckan"), overwrite=False)
    db.upsert_row(conn, Solicitation("3303123110", title="Toner Cartridges", source="odata"), overwrite=True)
    row = conn.execute("SELECT title FROM solicitation WHERE document_number='3303123110'").fetchone()
    assert row["title"] == "Toner Cartridges"


def test_overwrite_false_only_fills_nulls(conn):
    db.upsert_row(conn, Solicitation("3303123110", division="Purchasing", source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation("3303123110", division="SOMETHING ELSE", source="ckan"), overwrite=False)
    row = conn.execute("SELECT division FROM solicitation WHERE document_number='3303123110'").fetchone()
    assert row["division"] == "Purchasing"  # backfill must not clobber existing value


def test_overwrite_false_backfills_a_null(conn):
    db.upsert_row(conn, Solicitation("3303123110", division=None, source="odata"), overwrite=True)
    db.upsert_row(conn, Solicitation("3303123110", division="Toronto Water", source="ckan"), overwrite=False)
    row = conn.execute("SELECT division FROM solicitation WHERE document_number='3303123110'").fetchone()
    assert row["division"] == "Toronto Water"


def test_upsert_award_dedupes_on_docnum_supplier_source(conn):
    a = Award("3303123110", supplier_name_raw="Computer Media Group", award_amount="26773.58", source="odata")
    db.upsert_row(conn, a, overwrite=True)
    db.upsert_row(conn, a, overwrite=True)
    assert db.counts(conn)["award"] == 1


def test_upsert_noncompetitive_is_idempotent(conn):
    nc = NonCompetitive("8614", supplier_name_raw="Accuworx Inc", reason="Emergency", source="odata")
    db.upsert_row(conn, nc, overwrite=True)
    db.upsert_row(conn, nc, overwrite=True)
    assert db.counts(conn)["noncompetitive"] == 1


def test_sync_run_lifecycle(conn):
    run_id = db.start_sync_run(conn, "odata")
    db.finish_sync_run(conn, run_id, status="ok", rows_fetched=10, rows_upserted=10)
    row = conn.execute("SELECT status, rows_fetched FROM sync_run WHERE id=?", (run_id,)).fetchone()
    assert row["status"] == "ok" and row["rows_fetched"] == 10
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_db.py -v`
Expected: FAIL (`toronto_bids.store.db` not importable).

- [ ] **Step 5: Implement the store**

`scrapers/toronto_bids/store/db.py`:

```python
import sqlite3
from importlib import resources

from toronto_bids.models import Award, NonCompetitive, Solicitation

# Column lists per table, in the order used for INSERT. Excludes auto/default columns.
_SOLICITATION_COLS = [
    "document_number", "status", "rfx_type", "noip_type", "title", "description",
    "issue_date", "submission_deadline", "category", "division", "buyer_name",
    "buyer_email", "buyer_phone", "wards", "ariba_posting_link", "odata_id", "source",
]
_NONCOMP_COLS = [
    "workspace_number", "supplier_name_raw", "reason", "contract_amount",
    "contract_date", "division", "council_authority_link", "odata_id", "source",
]


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn) -> None:
    schema = resources.files("toronto_bids.store").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    conn.commit()


def _upsert_keyed(conn, table, cols, values, key_cols, overwrite: bool) -> None:
    placeholders = ", ".join("?" for _ in cols)
    non_key = [c for c in cols if c not in key_cols]
    if overwrite:
        # New non-null value wins; keep existing when the new value is NULL.
        sets = ", ".join(f"{c} = COALESCE(excluded.{c}, {table}.{c})" for c in non_key)
    else:
        # Backfill only: keep existing value; fill in only where existing is NULL.
        sets = ", ".join(f"{c} = COALESCE({table}.{c}, excluded.{c})" for c in non_key)
    conflict = ", ".join(key_cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {sets}, last_seen = datetime('now')"
    )
    conn.execute(sql, values)


def upsert_row(conn, row, *, overwrite: bool) -> None:
    if isinstance(row, Solicitation):
        values = [getattr(row, c) for c in _SOLICITATION_COLS]
        _upsert_keyed(conn, "solicitation", _SOLICITATION_COLS, values,
                      ["document_number"], overwrite)
    elif isinstance(row, NonCompetitive):
        values = [getattr(row, c) for c in _NONCOMP_COLS]
        _upsert_keyed(conn, "noncompetitive", _NONCOMP_COLS, values,
                      ["workspace_number"], overwrite)
    elif isinstance(row, Award):
        cols = ["document_number", "supplier_name_raw", "award_amount", "award_date", "source"]
        values = [getattr(row, c) for c in cols]
        _upsert_keyed(conn, "award", cols, values,
                      ["document_number", "supplier_name_raw", "source"], overwrite)
    else:
        raise TypeError(f"Cannot upsert row of type {type(row).__name__}")


def counts(conn) -> dict:
    tables = ["solicitation", "award", "noncompetitive", "sync_run"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def start_sync_run(conn, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO sync_run (source, started_at, status) VALUES (?, datetime('now'), 'running')",
        (source,),
    )
    conn.commit()
    return cur.lastrowid


def finish_sync_run(conn, run_id, *, status, rows_fetched=0, rows_upserted=0, error=None) -> None:
    conn.execute(
        "UPDATE sync_run SET finished_at = datetime('now'), status = ?, "
        "rows_fetched = ?, rows_upserted = ?, error = ? WHERE id = ?",
        (status, rows_fetched, rows_upserted, error, run_id),
    )
    conn.commit()
```

Note on the `award` UNIQUE constraint and NULL: `supplier_name_raw` can be NULL, and SQLite treats NULLs as distinct in UNIQUE indexes. Adapters must pass a real supplier string for awards (they always have one — `Successful_Bidder` / `Successful Supplier`); this is asserted by the adapter tests in Tasks 5 and 7.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_db.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/models.py scrapers/toronto_bids/config.py scrapers/toronto_bids/store scrapers/tests/conftest.py scrapers/tests/test_db.py
git commit -m "feat(scraper): add models, schema, and idempotent SQLite store"
```

---

### Task 4: HTTP client

A thin `httpx` wrapper: browser User-Agent, timeout, retry-with-backoff for transient failures (5xx / network). Used by every source.

**Files:**
- Create: `scrapers/toronto_bids/http.py`
- Create: `scrapers/tests/test_http.py`

**Interfaces:**
- Consumes: `config.USER_AGENT`, `config.HTTP_TIMEOUT`, `config.HTTP_RETRIES`.
- Produces:
  - `http.HttpClient(client: httpx.Client | None = None, retries: int = config.HTTP_RETRIES, backoff: float = 0.0)`
  - `HttpClient.get_json(url, params=None) -> Any`
  - `HttpClient.post_json(url, json=None, params=None) -> Any`
  - Retries on `httpx.HTTPStatusError` where status >= 500 and on `httpx.TransportError`; re-raises after exhausting retries. 4xx raises immediately (no retry).

- [ ] **Step 1: Write the failing tests (using httpx.MockTransport — no network)**

`scrapers/tests/test_http.py`:

```python
import httpx
import pytest

from toronto_bids.http import HttpClient


def _client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport)
    return HttpClient(client=inner, backoff=0.0, **kwargs)


def test_get_json_returns_parsed_body():
    def handler(request):
        assert "User-Agent" in request.headers
        return httpx.Response(200, json={"ok": True})
    client = _client(handler)
    assert client.get_json("https://example.test/x") == {"ok": True}


def test_get_json_retries_on_500_then_succeeds():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"ok": True})
    client = _client(handler, retries=4)
    assert client.get_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 3


def test_get_json_does_not_retry_on_404():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, text="nope")
    client = _client(handler, retries=4)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://example.test/x")
    assert calls["n"] == 1


def test_get_json_raises_after_exhausting_retries():
    def handler(request):
        return httpx.Response(503, text="down")
    client = _client(handler, retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://example.test/x")


def test_post_json_sends_body():
    def handler(request):
        assert request.method == "POST"
        return httpx.Response(200, json={"echo": True})
    client = _client(handler)
    assert client.post_json("https://example.test/x", json={"a": 1}) == {"echo": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_http.py -v`
Expected: FAIL (`toronto_bids.http` not importable).

- [ ] **Step 3: Implement the client**

`scrapers/toronto_bids/http.py`:

```python
import time

import httpx

from toronto_bids import config


class HttpClient:
    def __init__(self, client: httpx.Client | None = None, retries: int = config.HTTP_RETRIES,
                 backoff: float = 0.5):
        self._client = client or httpx.Client(
            timeout=config.HTTP_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
            follow_redirects=True,
        )
        self._retries = retries
        self._backoff = backoff

    def _request(self, method, url, **kwargs):
        last_exc = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise  # client error: do not retry
                last_exc = exc
            except httpx.TransportError as exc:
                last_exc = exc
            if attempt < self._retries:
                time.sleep(self._backoff * (2 ** attempt))
        raise last_exc

    def get_json(self, url, params=None):
        return self._request("GET", url, params=params).json()

    def post_json(self, url, json=None, params=None):
        return self._request("POST", url, json=json, params=params).json()

    def close(self):
        self._client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_http.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/http.py scrapers/tests/test_http.py
git commit -m "feat(scraper): add httpx client with UA and retry/backoff"
```

---

### Task 5: Source protocol + CKAN fetch

The uniform source interface, and the CKAN half that does network I/O: resolve the datastore resource UUID at runtime, then page through `datastore_search`.

**Files:**
- Create: `scrapers/toronto_bids/sources/__init__.py` (empty)
- Create: `scrapers/toronto_bids/sources/base.py`
- Create: `scrapers/toronto_bids/sources/ckan.py`
- Create: `scrapers/tests/test_ckan.py`

**Interfaces:**
- Consumes: `HttpClient.get_json`, `config.CKAN_BASE`.
- Produces:
  - `base.Row` = `Solicitation | Award | NonCompetitive` (type alias)
  - `base.Source` (Protocol): attributes `name: str`, `overwrite: bool`; methods `fetch(http) -> Iterable[dict]`, `normalize(raw: dict) -> Iterable[Row]`.
  - `ckan.resolve_resource_id(http, slug: str) -> str`
  - `ckan.fetch_datastore(http, resource_id: str, page_size: int = 10000) -> Iterator[dict]`
  - The `CkanSource` class and its `normalize` are completed in Task 6; this task delivers `resolve_resource_id` and `fetch_datastore` plus the class skeleton.

- [ ] **Step 1: Write the base protocol**

`scrapers/toronto_bids/sources/__init__.py`: empty file.

`scrapers/toronto_bids/sources/base.py`:

```python
from typing import Iterable, Protocol, runtime_checkable

from toronto_bids.models import Award, NonCompetitive, Solicitation

Row = Solicitation | Award | NonCompetitive


@runtime_checkable
class Source(Protocol):
    name: str
    overwrite: bool

    def fetch(self, http) -> Iterable[dict]:
        ...

    def normalize(self, raw: dict) -> Iterable[Row]:
        ...
```

- [ ] **Step 2: Write the failing tests for CKAN fetch**

Create the awarded fixture `scrapers/tests/fixtures/ckan_awarded.json` (real records, trimmed to 2 — one clean, one with a dirty document number):

```json
{
  "success": true,
  "result": {
    "resource_id": "e211f003-5909-4bea-bd96-d75899d8e612",
    "total": 2,
    "records": [
      {
        "_id": 1,
        "Document Number": "3303123110",
        "RFx (Solicitation) Type": "RFQ",
        "High Level Category": "Goods and Services",
        "Successful Supplier": "Computer Media Group",
        "Award": "26773.58",
        "Award Authority Obtained Date": "2012-10-04",
        "Division": "Purchasing & Materials Management",
        "Buyer Name": "Justin Diptee",
        "Buyer Email": "supplychain@toronto.ca",
        "Buyer Phone Number": "416-397-4141",
        "Solicitation Document Description": "For the non-exclusive supply and delivery of various OEM toners",
        "Supplier Address": null,
        "Wards": null
      },
      {
        "_id": 2,
        "Document Number": "xxxxxxxx",
        "RFx (Solicitation) Type": "RFT",
        "High Level Category": "Construction Services",
        "Successful Supplier": "Some Roadworks Ltd",
        "Award": "0",
        "Award Authority Obtained Date": null,
        "Division": "Engineering & Construction Services",
        "Buyer Name": "Nicole Di Petta",
        "Buyer Email": "nicole.dipetta@toronto.ca",
        "Buyer Phone Number": "4163385583",
        "Solicitation Document Description": "Road resurfacing",
        "Supplier Address": null,
        "Wards": null
      }
    ]
  }
}
```

`scrapers/tests/test_ckan.py` (fetch portion):

```python
import json

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.sources import ckan
from tests.conftest import FIXTURES


def _http(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


def test_resolve_resource_id_picks_datastore_active_resource():
    def handler(request):
        assert "package_show" in str(request.url)
        return httpx.Response(200, json={
            "success": True,
            "result": {"resources": [
                {"id": "aaa", "format": "PDF", "datastore_active": False},
                {"id": "e211f003-5909-4bea-bd96-d75899d8e612", "format": "CSV", "datastore_active": True},
            ]},
        })
    http = _http(handler)
    assert ckan.resolve_resource_id(http, "tobids-awarded-contracts") == "e211f003-5909-4bea-bd96-d75899d8e612"


def test_fetch_datastore_paginates_until_empty():
    pages = [
        {"success": True, "result": {"records": [{"_id": 1}, {"_id": 2}]}},
        {"success": True, "result": {"records": []}},
    ]
    seen_offsets = []
    def handler(request):
        offset = int(dict(request.url.params).get("offset", "0"))
        seen_offsets.append(offset)
        return httpx.Response(200, json=pages[0] if offset == 0 else pages[1])
    http = _http(handler)
    records = list(ckan.fetch_datastore(http, "res-id", page_size=2))
    assert [r["_id"] for r in records] == [1, 2]
    assert seen_offsets == [0, 2]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_ckan.py -v`
Expected: FAIL (`toronto_bids.sources.ckan` not importable).

- [ ] **Step 4: Implement CKAN fetch + the class skeleton**

`scrapers/toronto_bids/sources/ckan.py`:

```python
from typing import Iterable, Iterator

from toronto_bids import config
from toronto_bids.sources.base import Row


def resolve_resource_id(http, slug: str) -> str:
    """Resolve the datastore-active resource UUID for a CKAN dataset slug.

    Resource UUIDs rotate on refresh, so this must be called at runtime.
    """
    data = http.get_json(config.CKAN_BASE + "package_show", params={"id": slug})
    resources = data["result"]["resources"]
    for res in resources:
        if res.get("datastore_active"):
            return res["id"]
    raise LookupError(f"No datastore-active resource for CKAN dataset '{slug}'")


def fetch_datastore(http, resource_id: str, page_size: int = 10000) -> Iterator[dict]:
    """Yield every record from a CKAN datastore resource, paging by offset."""
    offset = 0
    while True:
        data = http.get_json(
            config.CKAN_BASE + "datastore_search",
            params={"resource_id": resource_id, "limit": page_size, "offset": offset},
        )
        records = data["result"]["records"]
        if not records:
            return
        yield from records
        offset += len(records)


class CkanSource:
    """A CKAN dataset adapter. `normalize` is implemented in Task 6."""

    overwrite = False  # CKAN backfills; OData is the spine.

    def __init__(self, name: str, slug: str):
        self.name = name
        self.slug = slug

    def fetch(self, http) -> Iterable[dict]:
        resource_id = resolve_resource_id(http, self.slug)
        yield from fetch_datastore(http, resource_id)

    def normalize(self, raw: dict) -> Iterable[Row]:  # completed in Task 6
        raise NotImplementedError
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_ckan.py -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/sources scrapers/tests/test_ckan.py scrapers/tests/fixtures/ckan_awarded.json
git commit -m "feat(scraper): add Source protocol and CKAN datastore fetch"
```

---

### Task 6: CKAN normalize (awarded / open / non-competitive)

Turn CKAN records into typed rows, dropping records whose `document_number` cannot be normalized.

**Files:**
- Modify: `scrapers/toronto_bids/sources/ckan.py` (implement `CkanSource.normalize` via three module functions)
- Create: `scrapers/tests/fixtures/ckan_solicitations.json`
- Create: `scrapers/tests/fixtures/ckan_noncompetitive.json`
- Modify: `scrapers/tests/test_ckan.py` (add normalize tests)

**Interfaces:**
- Consumes: `normalize_document_number`, `models.*`.
- Produces:
  - `ckan.normalize_awarded(raw) -> Iterable[Row]` — yields one `Solicitation` (status `"Awarded"`) + one `Award`, or nothing if the doc number is invalid.
  - `ckan.normalize_open(raw) -> Iterable[Row]` — yields one `Solicitation` (status `"Open"`).
  - `ckan.normalize_noncompetitive(raw) -> Iterable[Row]` — yields one `NonCompetitive`.
  - `CkanSource` gains a `kind` ("awarded" | "open" | "noncompetitive") selecting which normalizer to use.

- [ ] **Step 1: Create the two remaining CKAN fixtures**

`scrapers/tests/fixtures/ckan_solicitations.json`:

```json
{
  "success": true,
  "result": {
    "records": [
      {
        "_id": 1,
        "Document Number": "9117105139",
        "RFx (Solicitation) Type": "RFP",
        "NOIP (Notice of Intended Procurement) Type": "Notice of Intended Procurement",
        "Issue Date": "2010-10-28",
        "Submission Deadline": "2010-11-24",
        "High Level Category": "Professional Services",
        "Solicitation Document Description": "Consulting services",
        "Division": "City Planning",
        "Buyer Name": "Jane Buyer",
        "Buyer Email": "jane.buyer@toronto.ca",
        "Buyer Phone Number": "416-000-0000",
        "Wards": "None"
      }
    ]
  }
}
```

`scrapers/tests/fixtures/ckan_noncompetitive.json`:

```json
{
  "success": true,
  "result": {
    "records": [
      {
        "_id": 1,
        "Workspace Number": "8614",
        "Reason": "Emergency",
        "Contract Date": "2015-07-29",
        "Supplier Name": "Accuworx Inc",
        "Contract Amount": "67896.4",
        "Division": "Toronto Water",
        "Supplier Address": null,
        "Wards": "None"
      }
    ]
  }
}
```

- [ ] **Step 2: Write the failing normalize tests**

Add to `scrapers/tests/test_ckan.py`:

```python
from toronto_bids.models import Award, NonCompetitive, Solicitation


def _records(fixture_name):
    data = json.loads((FIXTURES / fixture_name).read_text())
    return data["result"]["records"]


def test_normalize_awarded_yields_solicitation_and_award():
    rows = list(ckan.normalize_awarded(_records("ckan_awarded.json")[0]))
    sol = next(r for r in rows if isinstance(r, Solicitation))
    award = next(r for r in rows if isinstance(r, Award))
    assert sol.document_number == "3303123110"
    assert sol.status == "Awarded"
    assert sol.rfx_type == "RFQ"
    assert sol.category == "Goods and Services"
    assert sol.source == "ckan_awarded"
    assert award.document_number == "3303123110"
    assert award.supplier_name_raw == "Computer Media Group"
    assert award.award_amount == "26773.58"
    assert award.award_date == "2012-10-04"


def test_normalize_awarded_skips_invalid_document_number():
    rows = list(ckan.normalize_awarded(_records("ckan_awarded.json")[1]))  # "xxxxxxxx"
    assert rows == []


def test_normalize_open_yields_open_solicitation():
    rows = list(ckan.normalize_open(_records("ckan_solicitations.json")[0]))
    sol = rows[0]
    assert isinstance(sol, Solicitation)
    assert sol.document_number == "9117105139"
    assert sol.status == "Open"
    assert sol.rfx_type == "RFP"
    assert sol.noip_type == "Notice of Intended Procurement"
    assert sol.issue_date == "2010-10-28"
    assert sol.submission_deadline == "2010-11-24"
    assert sol.source == "ckan_open"


def test_normalize_noncompetitive_yields_row():
    rows = list(ckan.normalize_noncompetitive(_records("ckan_noncompetitive.json")[0]))
    nc = rows[0]
    assert isinstance(nc, NonCompetitive)
    assert nc.workspace_number == "8614"
    assert nc.supplier_name_raw == "Accuworx Inc"
    assert nc.reason == "Emergency"
    assert nc.contract_amount == "67896.4"
    assert nc.source == "ckan_noncomp"


def test_ckan_source_dispatches_to_kind():
    src = ckan.CkanSource(name="ckan_awarded", slug="tobids-awarded-contracts", kind="awarded")
    rows = list(src.normalize(_records("ckan_awarded.json")[0]))
    assert any(isinstance(r, Award) for r in rows)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_ckan.py -v`
Expected: the new tests FAIL (`normalize_awarded` not defined / `CkanSource` has no `kind`).

- [ ] **Step 4: Implement the normalizers**

Add to `scrapers/toronto_bids/sources/ckan.py` (imports at top, functions below `fetch_datastore`, and update `CkanSource`):

```python
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import Award, NonCompetitive, Solicitation
```

```python
def _clean(value):
    """Normalize CKAN empties to None. Treats '', 'None', and null as missing."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text == "None":
        return None
    return text


def normalize_awarded(raw: dict):
    doc = normalize_document_number(raw.get("Document Number"))
    if doc is None:
        return
    yield Solicitation(
        document_number=doc,
        status="Awarded",
        rfx_type=_clean(raw.get("RFx (Solicitation) Type")),
        category=_clean(raw.get("High Level Category")),
        description=_clean(raw.get("Solicitation Document Description")),
        division=_clean(raw.get("Division")),
        buyer_name=_clean(raw.get("Buyer Name")),
        buyer_email=_clean(raw.get("Buyer Email")),
        buyer_phone=_clean(raw.get("Buyer Phone Number")),
        source="ckan_awarded",
    )
    supplier = _clean(raw.get("Successful Supplier"))
    if supplier is not None:
        yield Award(
            document_number=doc,
            supplier_name_raw=supplier,
            award_amount=_clean(raw.get("Award")),
            award_date=_clean(raw.get("Award Authority Obtained Date")),
            source="ckan_awarded",
        )


def normalize_open(raw: dict):
    doc = normalize_document_number(raw.get("Document Number"))
    if doc is None:
        return
    yield Solicitation(
        document_number=doc,
        status="Open",
        rfx_type=_clean(raw.get("RFx (Solicitation) Type")),
        noip_type=_clean(raw.get("NOIP (Notice of Intended Procurement) Type")),
        issue_date=_clean(raw.get("Issue Date")),
        submission_deadline=_clean(raw.get("Submission Deadline")),
        category=_clean(raw.get("High Level Category")),
        description=_clean(raw.get("Solicitation Document Description")),
        division=_clean(raw.get("Division")),
        buyer_name=_clean(raw.get("Buyer Name")),
        buyer_email=_clean(raw.get("Buyer Email")),
        buyer_phone=_clean(raw.get("Buyer Phone Number")),
        wards=_clean(raw.get("Wards")),
        source="ckan_open",
    )


def normalize_noncompetitive(raw: dict):
    workspace = _clean(raw.get("Workspace Number"))
    if workspace is None:
        return
    yield NonCompetitive(
        workspace_number=workspace,
        supplier_name_raw=_clean(raw.get("Supplier Name")),
        reason=_clean(raw.get("Reason")),
        contract_amount=_clean(raw.get("Contract Amount")),
        contract_date=_clean(raw.get("Contract Date")),
        division=_clean(raw.get("Division")),
        source="ckan_noncomp",
    )


_NORMALIZERS = {
    "awarded": normalize_awarded,
    "open": normalize_open,
    "noncompetitive": normalize_noncompetitive,
}
```

Replace the `CkanSource` class with:

```python
class CkanSource:
    """A CKAN dataset adapter."""

    overwrite = False  # CKAN backfills; OData is the spine.

    def __init__(self, name: str, slug: str, kind: str):
        if kind not in _NORMALIZERS:
            raise ValueError(f"Unknown CKAN kind: {kind}")
        self.name = name
        self.slug = slug
        self.kind = kind

    def fetch(self, http) -> Iterable[dict]:
        resource_id = resolve_resource_id(http, self.slug)
        yield from fetch_datastore(http, resource_id)

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from _NORMALIZERS[self.kind](raw)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_ckan.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/sources/ckan.py scrapers/tests/test_ckan.py scrapers/tests/fixtures/ckan_solicitations.json scrapers/tests/fixtures/ckan_noncompetitive.json
git commit -m "feat(scraper): add CKAN normalizers for awarded/open/non-competitive"
```

---

### Task 7: OData source (spine)

Paginated fetch of the OData entity sets and normalization into the spine rows. OData solicitations expand their nested `Awarded_Suppliers[]` into `Award` rows.

**Files:**
- Create: `scrapers/toronto_bids/sources/odata.py`
- Create: `scrapers/tests/fixtures/odata_solicitation.json`
- Create: `scrapers/tests/fixtures/odata_noncompetitive.json`
- Create: `scrapers/tests/test_odata.py`

**Interfaces:**
- Consumes: `HttpClient.get_json`, `config.ODATA_BASE`, `config.ODATA_SOLICITATIONS`, `config.ODATA_NONCOMPETITIVE`, `normalize_document_number`, `models.*`.
- Produces:
  - `odata.fetch_entityset(http, entityset: str, page_size: int = 1000) -> Iterator[dict]`
  - `odata.normalize_solicitation(raw) -> Iterable[Row]` (yields `Solicitation` + zero-or-more `Award`)
  - `odata.normalize_noncompetitive(raw) -> Iterable[Row]` (yields `NonCompetitive`)
  - `odata.ODataSolicitationSource` and `odata.ODataNonCompetitiveSource` (both `overwrite = True`)

- [ ] **Step 1: Create the OData fixtures (real records, trimmed)**

`scrapers/tests/fixtures/odata_solicitation.json`:

```json
{
  "@odata.count": 1,
  "value": [
    {
      "Solicitation_Document_Number": "3303123110",
      "Status": "Awarded",
      "Solicitation_Document_Type": "RFQ",
      "Solicitation_Form_Type": "Awarded Contracts",
      "Posting_Title": "Toner Cartridges",
      "Solicitation_Document_Description": "For the non-exclusive supply and delivery of various OEM toners",
      "High_Level_Category": "Goods and Services",
      "Issue_Date": "2012-07-13",
      "Closing_Date": "2012-07-30",
      "Client_Division": ["Purchasing & Materials Management"],
      "Buyer_Name": "Justin Diptee",
      "Buyer_Email": "supplychain@toronto.ca",
      "Buyer_Phone_Number": "416-397-4141",
      "Wards": [],
      "Ariba_Discovery_Posting_Link": "",
      "Award_Amount": "",
      "Awarded_Suppliers": [
        {
          "Award_Amount": "26773.58",
          "AwardedDate": null,
          "Date_Awarded": "2012-10-04",
          "Successful_Bidder": "Computer Media Group",
          "city": "MISSISSAUGA",
          "province": "ON"
        }
      ],
      "id": "da83db29-e4fc-4651-a9a3-d6bedd042e8c"
    }
  ]
}
```

`scrapers/tests/fixtures/odata_noncompetitive.json`:

```json
{
  "@odata.count": 1,
  "value": [
    {
      "Non_Competitive_Reference_Number": "8614",
      "Non_Competitive_Reason": "Emergency",
      "Solicitation_Document_Number": "",
      "Posting_Title": "Environmental service request",
      "Solicitation_Document_Description": "For the provision of an environmental service request",
      "Client_Division": ["Toronto Water"],
      "Buyer_Name": "Justin Diptee",
      "Buyer_Email": "supplychain@toronto.ca",
      "Council_Authority_Link_to_Staff_Report": "",
      "Status": "Awarded",
      "Awarded_Suppliers": [
        {
          "Award_Amount": "67896.4",
          "Date_Awarded": "2015-07-29",
          "Successful_Bidder": "Accuworx Inc"
        }
      ],
      "id": "66e002b8-5d66-4df0-b0d8-6cb628fda467"
    }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`scrapers/tests/test_odata.py`:

```python
import json

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.models import Award, NonCompetitive, Solicitation
from toronto_bids.sources import odata
from tests.conftest import FIXTURES


def _http(handler):
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


def _value(fixture_name):
    return json.loads((FIXTURES / fixture_name).read_text())["value"][0]


def test_fetch_entityset_pages_with_skip_top():
    page = json.loads((FIXTURES / "odata_solicitation.json").read_text())
    empty = {"@odata.count": 1, "value": []}
    seen_skip = []
    def handler(request):
        params = dict(request.url.params)
        skip = int(params.get("$skip", "0"))
        seen_skip.append(skip)
        return httpx.Response(200, json=page if skip == 0 else empty)
    records = list(odata.fetch_entityset(_http(handler), "feis_solicitation_published", page_size=1))
    assert len(records) == 1
    assert records[0]["Solicitation_Document_Number"] == "3303123110"
    assert seen_skip == [0, 1]


def test_normalize_solicitation_yields_spine_and_award():
    rows = list(odata.normalize_solicitation(_value("odata_solicitation.json")))
    sol = next(r for r in rows if isinstance(r, Solicitation))
    award = next(r for r in rows if isinstance(r, Award))
    assert sol.document_number == "3303123110"
    assert sol.status == "Awarded"
    assert sol.rfx_type == "RFQ"
    assert sol.title == "Toner Cartridges"
    assert sol.category == "Goods and Services"
    assert sol.division == "Purchasing & Materials Management"  # first of Client_Division array
    assert sol.submission_deadline == "2012-07-30"
    assert sol.ariba_posting_link is None  # "" normalized to None
    assert sol.odata_id == "da83db29-e4fc-4651-a9a3-d6bedd042e8c"
    assert sol.source == "odata"
    assert award.supplier_name_raw == "Computer Media Group"
    assert award.award_amount == "26773.58"
    assert award.award_date == "2012-10-04"
    assert award.source == "odata"


def test_normalize_solicitation_skips_invalid_docnum_but_still_none_safe():
    raw = dict(_value("odata_solicitation.json"))
    raw["Solicitation_Document_Number"] = ""
    assert list(odata.normalize_solicitation(raw)) == []


def test_normalize_noncompetitive_uses_workspace_number():
    rows = list(odata.normalize_noncompetitive(_value("odata_noncompetitive.json")))
    nc = rows[0]
    assert isinstance(nc, NonCompetitive)
    assert nc.workspace_number == "8614"
    assert nc.reason == "Emergency"
    assert nc.supplier_name_raw == "Accuworx Inc"
    assert nc.contract_amount == "67896.4"
    assert nc.contract_date == "2015-07-29"
    assert nc.source == "odata"
    assert nc.odata_id == "66e002b8-5d66-4df0-b0d8-6cb628fda467"


def test_sources_are_spine_overwrite_true():
    assert odata.ODataSolicitationSource().overwrite is True
    assert odata.ODataNonCompetitiveSource().overwrite is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_odata.py -v`
Expected: FAIL (`toronto_bids.sources.odata` not importable).

- [ ] **Step 4: Implement the OData source**

`scrapers/toronto_bids/sources/odata.py`:

```python
from typing import Iterable, Iterator

from toronto_bids import config
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import Award, NonCompetitive, Solicitation
from toronto_bids.sources.base import Row

# odata.metadata=none returns {"@odata.count": N, "value": [...]}, records in "value".
_FORMAT = "application/json;odata.metadata=none"


def _clean(value):
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def fetch_entityset(http, entityset: str, page_size: int = 1000) -> Iterator[dict]:
    """Yield every record from an OData entity set, paging with $skip/$top."""
    skip = 0
    while True:
        data = http.get_json(
            config.ODATA_BASE + entityset,
            params={"$format": _FORMAT, "$top": page_size, "$skip": skip},
        )
        records = data.get("value", [])
        if not records:
            return
        yield from records
        skip += len(records)


def normalize_solicitation(raw: dict) -> Iterable[Row]:
    doc = normalize_document_number(raw.get("Solicitation_Document_Number"))
    if doc is None:
        return
    yield Solicitation(
        document_number=doc,
        status=_clean(raw.get("Status")),
        rfx_type=_clean(raw.get("Solicitation_Document_Type")),
        noip_type=_clean(raw.get("Solicitation_Form_Type")),
        title=_clean(raw.get("Posting_Title")),
        description=_clean(raw.get("Solicitation_Document_Description")),
        issue_date=_clean(raw.get("Issue_Date")),
        submission_deadline=_clean(raw.get("Closing_Date")),
        category=_clean(raw.get("High_Level_Category")),
        division=_clean(raw.get("Client_Division")),
        buyer_name=_clean(raw.get("Buyer_Name")),
        buyer_email=_clean(raw.get("Buyer_Email")),
        buyer_phone=_clean(raw.get("Buyer_Phone_Number")),
        wards=_clean(raw.get("Wards")),
        ariba_posting_link=_clean(raw.get("Ariba_Discovery_Posting_Link")),
        odata_id=_clean(raw.get("id")),
        source="odata",
    )
    for supplier in raw.get("Awarded_Suppliers") or []:
        name = _clean(supplier.get("Successful_Bidder"))
        if name is None:
            continue
        yield Award(
            document_number=doc,
            supplier_name_raw=name,
            award_amount=_clean(supplier.get("Award_Amount")),
            award_date=_clean(supplier.get("Date_Awarded")) or _clean(supplier.get("AwardedDate")),
            source="odata",
        )


def normalize_noncompetitive(raw: dict) -> Iterable[Row]:
    workspace = _clean(raw.get("Non_Competitive_Reference_Number"))
    if workspace is None:
        return
    suppliers = raw.get("Awarded_Suppliers") or []
    first = suppliers[0] if suppliers else {}
    yield NonCompetitive(
        workspace_number=workspace,
        supplier_name_raw=_clean(first.get("Successful_Bidder")),
        reason=_clean(raw.get("Non_Competitive_Reason")),
        contract_amount=_clean(first.get("Award_Amount")),
        contract_date=_clean(first.get("Date_Awarded")),
        division=_clean(raw.get("Client_Division")),
        council_authority_link=_clean(raw.get("Council_Authority_Link_to_Staff_Report")),
        odata_id=_clean(raw.get("id")),
        source="odata",
    )


class ODataSolicitationSource:
    name = "odata_solicitations"
    overwrite = True

    def fetch(self, http) -> Iterable[dict]:
        yield from fetch_entityset(http, config.ODATA_SOLICITATIONS)

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from normalize_solicitation(raw)


class ODataNonCompetitiveSource:
    name = "odata_noncompetitive"
    overwrite = True

    def fetch(self, http) -> Iterable[dict]:
        yield from fetch_entityset(http, config.ODATA_NONCOMPETITIVE)

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from normalize_noncompetitive(raw)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_odata.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/sources/odata.py scrapers/tests/test_odata.py scrapers/tests/fixtures/odata_solicitation.json scrapers/tests/fixtures/odata_noncompetitive.json
git commit -m "feat(scraper): add OData spine source with awarded-suppliers expansion"
```

---

### Task 8: Pipeline orchestration

Run each source in isolation (a failure records to `sync_run` and does not abort the run), route rows to the store with the source's overwrite mode, and record per-source `sync_run` rows. OData sources run before CKAN so the spine populates first.

**Files:**
- Create: `scrapers/toronto_bids/pipeline.py`
- Create: `scrapers/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `db.*`, `HttpClient`, the `Source` protocol, all four source classes.
- Produces:
  - `pipeline.default_sources() -> list[Source]` (OData spine first, then the three CKAN sources)
  - `pipeline.run_source(conn, http, source) -> tuple[int, int]` (returns `(rows_fetched, rows_upserted)`; catches exceptions, records `sync_run`, never raises)
  - `pipeline.sync(conn, http, sources=None, only=None) -> None`

- [ ] **Step 1: Write the failing tests (fake sources — no network)**

`scrapers/tests/test_pipeline.py`:

```python
from toronto_bids import pipeline
from toronto_bids.models import Award, Solicitation
from toronto_bids.store import db


class FakeSource:
    def __init__(self, name, rows, overwrite=True, boom=False):
        self.name = name
        self._rows = rows
        self.overwrite = overwrite
        self._boom = boom

    def fetch(self, http):
        if self._boom:
            raise RuntimeError("network exploded")
        return [{"i": i} for i in range(len(self._rows))]

    def normalize(self, raw):
        return [self._rows[raw["i"]]]


def test_run_source_upserts_and_records_ok(conn):
    src = FakeSource("odata_solicitations", [
        Solicitation("3303123110", status="Awarded", source="odata"),
        Award("3303123110", supplier_name_raw="Computer Media Group", source="odata"),
    ])
    fetched, upserted = pipeline.run_source(conn, http=None, source=src)
    assert (fetched, upserted) == (2, 2)
    assert db.counts(conn)["solicitation"] == 1
    assert db.counts(conn)["award"] == 1
    run = conn.execute("SELECT status FROM sync_run WHERE source='odata_solicitations'").fetchone()
    assert run["status"] == "ok"


def test_run_source_isolates_failure(conn):
    src = FakeSource("odata_solicitations", [], boom=True)
    fetched, upserted = pipeline.run_source(conn, http=None, source=src)
    assert (fetched, upserted) == (0, 0)
    run = conn.execute("SELECT status, error FROM sync_run WHERE source='odata_solicitations'").fetchone()
    assert run["status"] == "failed"
    assert "network exploded" in run["error"]


def test_sync_runs_all_and_one_failure_does_not_stop_others(conn):
    good = FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")])
    bad = FakeSource("ckan_open", [], boom=True)
    also_good = FakeSource("ckan_awarded", [Solicitation("5749398870", source="ckan_awarded")], overwrite=False)
    pipeline.sync(conn, http=None, sources=[good, bad, also_good])
    assert db.counts(conn)["solicitation"] == 2
    assert db.counts(conn)["sync_run"] == 3


def test_sync_only_filters_sources(conn):
    good = FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")])
    other = FakeSource("ckan_open", [Solicitation("5749398870", source="ckan_open")])
    pipeline.sync(conn, http=None, sources=[good, other], only=["odata_solicitations"])
    assert db.counts(conn)["solicitation"] == 1
    assert db.counts(conn)["sync_run"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_pipeline.py -v`
Expected: FAIL (`toronto_bids.pipeline` not importable).

- [ ] **Step 3: Implement the pipeline**

`scrapers/toronto_bids/pipeline.py`:

```python
from toronto_bids.sources.ckan import CkanSource
from toronto_bids.sources.odata import ODataNonCompetitiveSource, ODataSolicitationSource
from toronto_bids.store import db
from toronto_bids import config


def default_sources():
    """OData spine first (overwrite=True), then CKAN backfill (overwrite=False)."""
    return [
        ODataSolicitationSource(),
        ODataNonCompetitiveSource(),
        CkanSource(name="ckan_awarded", slug=config.CKAN_AWARDED_SLUG, kind="awarded"),
        CkanSource(name="ckan_open", slug=config.CKAN_OPEN_SLUG, kind="open"),
        CkanSource(name="ckan_noncomp", slug=config.CKAN_NONCOMP_SLUG, kind="noncompetitive"),
    ]


def run_source(conn, http, source):
    """Run one source; record a sync_run. Never raises — returns (fetched, upserted)."""
    run_id = db.start_sync_run(conn, source.name)
    fetched = upserted = 0
    try:
        for raw in source.fetch(http):
            fetched += 1
            for row in source.normalize(raw):
                db.upsert_row(conn, row, overwrite=source.overwrite)
                upserted += 1
        conn.commit()
        db.finish_sync_run(conn, run_id, status="ok",
                           rows_fetched=fetched, rows_upserted=upserted)
    except Exception as exc:  # per-source isolation
        conn.commit()
        db.finish_sync_run(conn, run_id, status="failed",
                           rows_fetched=fetched, rows_upserted=upserted, error=str(exc))
    return fetched, upserted


def sync(conn, http, sources=None, only=None) -> None:
    sources = sources if sources is not None else default_sources()
    if only:
        wanted = set(only)
        sources = [s for s in sources if s.name in wanted]
    for source in sources:
        run_source(conn, http, source)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scrapers && uv run pytest tests/test_pipeline.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/toronto_bids/pipeline.py scrapers/tests/test_pipeline.py
git commit -m "feat(scraper): add pipeline orchestration with per-source isolation"
```

---

### Task 9: CLI `tb sync` / `tb status` + full-suite green

Wire the pipeline to the CLI, creating the data dir and DB on demand, and add a `status` command that prints table counts.

**Files:**
- Modify: `scrapers/toronto_bids/cli.py`
- Modify: `scrapers/tests/test_smoke.py` (add CLI wiring tests)

**Interfaces:**
- Consumes: `pipeline.sync`, `pipeline.default_sources`, `db.connect/init_db/counts`, `HttpClient`, `config.DATA_DIR/DB_PATH`.
- Produces: `tb sync [--only a,b]` and `tb status`; `cli.main` returns 0 on success.

- [ ] **Step 1: Write the failing CLI tests**

Add to `scrapers/tests/test_smoke.py`:

```python
from toronto_bids.cli import main
from toronto_bids.models import Solicitation
from toronto_bids.store import db
import toronto_bids.pipeline as pipeline_mod


def test_status_on_empty_db_prints_zero_counts(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("toronto_bids.config.DB_PATH", tmp_path / "bids.sqlite")
    monkeypatch.setattr("toronto_bids.config.DATA_DIR", tmp_path)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "solicitation" in out


def test_sync_uses_pipeline_and_persists(tmp_path, monkeypatch):
    db_path = tmp_path / "bids.sqlite"
    monkeypatch.setattr("toronto_bids.config.DB_PATH", db_path)
    monkeypatch.setattr("toronto_bids.config.DATA_DIR", tmp_path)

    def fake_sync(conn, http, sources=None, only=None):
        db.upsert_row(conn, Solicitation("3303123110", source="odata"), overwrite=True)
        conn.commit()
    monkeypatch.setattr(pipeline_mod, "sync", fake_sync)

    assert main(["sync"]) == 0
    conn = db.connect(db_path)
    assert db.counts(conn)["solicitation"] == 1
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scrapers && uv run pytest tests/test_smoke.py -v`
Expected: the two new tests FAIL (no `status`/`sync` handling).

- [ ] **Step 3: Implement the CLI commands**

Replace `scrapers/toronto_bids/cli.py` with:

```python
import argparse

from toronto_bids import __version__, config, pipeline
from toronto_bids.http import HttpClient
from toronto_bids.store import db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tb", description="Toronto Bids scraper")
    parser.add_argument("--version", action="version", version=f"tb {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser("sync", help="Fetch all sources into the local store")
    p_sync.add_argument("--only", help="Comma-separated source names to run")

    sub.add_parser("status", help="Show row counts in the local store")
    return parser


def _open_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    return conn


def _cmd_sync(args) -> int:
    conn = _open_db()
    http = HttpClient()
    only = args.only.split(",") if args.only else None
    try:
        pipeline.sync(conn, http, only=only)
    finally:
        http.close()
    counts = db.counts(conn)
    print("Sync complete:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    conn.close()
    return 0


def _cmd_status(args) -> int:
    conn = _open_db()
    for table, n in db.counts(conn).items():
        print(f"{table:16s} {n}")
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "sync":
        return _cmd_sync(args)
    if args.command == "status":
        return _cmd_status(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the full test suite**

Run: `cd scrapers && uv run pytest -v`
Expected: every test across all files PASSES.

- [ ] **Step 5: Live end-to-end smoke check (network — manual, not a test)**

Run:
```bash
cd scrapers && TB_DATA_DIR=/tmp/tb-live uv run tb sync --only odata_solicitations,ckan_awarded && uv run tb status
```
Expected: completes without error; `tb status` shows non-zero `solicitation` and `award` counts (thousands). This exercises real OData + CKAN endpoints. If OData returns HTTP 403 (Akamai from this IP), the `sync_run` for `odata_solicitations` will be `failed` but `ckan_awarded` still populates — confirming per-source isolation. Record the outcome; do not block the commit on OData reachability.

- [ ] **Step 6: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_smoke.py
git commit -m "feat(scraper): wire tb sync and tb status commands"
```

---

### Task 10: Remove dead scripts + document

Delete the superseded flat scripts and give `scrapers/` a README describing the new package. This is the "immediate cleanup" from spec §11.

**Files:**
- Delete: `scrapers/rfp_scraper.py`, `scrapers/ariba_driver.py`, `scrapers/open_data.py`, `scrapers/filemanage.py`, `scrapers/transmit_json.py`, `scrapers/azurefileshare.py`, `scrapers/secret_manager.py`, `scrapers/slack.py`, `scrapers/config.json`, `scrapers/environment.yml`, `scrapers/Dockerfile`, `scrapers/build_docker.sh`, `scrapers/entrypoint.sh`
- Modify: `scrapers/README.md`

**Interfaces:** none (cleanup only).

- [ ] **Step 1: Confirm nothing in the new package imports the old modules**

Run:
```bash
cd scrapers && grep -rEn "rfp_scraper|ariba_driver|open_data|filemanage|transmit_json|azurefileshare|secret_manager|import slack|from slack" toronto_bids tests
```
Expected: no output (the new package is self-contained).

- [ ] **Step 2: Delete the dead scripts**

```bash
cd scrapers && git rm rfp_scraper.py ariba_driver.py open_data.py filemanage.py transmit_json.py azurefileshare.py secret_manager.py slack.py config.json environment.yml Dockerfile build_docker.sh entrypoint.sh
```

- [ ] **Step 3: Rewrite the README**

`scrapers/README.md`:

```markdown
# Toronto Bids scraper

A `uv`-managed Python package that pulls City of Toronto procurement data into a
local SQLite store. No browser, no login, no cloud.

## Sources (P0/P1)

- **OData `feis_solicitation_published`** — the solicitation lifecycle spine
  (open / awarded / cancelled), the authoritative source.
- **OData `feis_non_competitive_published`** — non-competitive (sole-source) awards.
- **CKAN `tobids-awarded-contracts` / `tobids-all-open-solicitations` /
  `tobids-non-competitive-contracts`** — backfill for the spine.

Everything competitive is keyed on the normalized 10-digit `document_number`.
Non-competitive awards are a separate keyspace (`workspace_number`).

## Usage

```shell
cd scrapers
uv sync
uv run tb sync            # fetch all sources into files/bids.sqlite
uv run tb sync --only odata_solicitations,ckan_awarded
uv run tb status          # row counts
uv run pytest             # tests (offline; uses fixtures)
```

Set `TB_DATA_DIR` to change where `bids.sqlite` and downloads live (default `scrapers/files/`).

See `docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md` for the
full design, source inventory, and the later phases (Ariba Discovery JSON, attachments,
council/PDF enrichment).
```

- [ ] **Step 4: Verify the suite still passes after deletions**

Run: `cd scrapers && uv run pytest -v`
Expected: all PASS (deletions touch nothing the package imports).

- [ ] **Step 5: Commit**

```bash
git add scrapers/README.md
git commit -m "chore(scraper): remove dead flat scripts, document new package"
```

---

## Self-Review

**1. Spec coverage (P0 + P1 scope):**
- P0 scaffold (uv project, package layout, CLI) → Task 1. ✓
- `normalize_document_number` + tests (spec §3.3, all dirty cases) → Task 2. ✓
- SQLite schema + `db.py` (spine/backfill overwrite, idempotency, `sync_run`) → Task 3. ✓
- `http.py` (UA, retry/backoff) → Task 4. ✓
- Source-adapter interface (spec §4.3) → Task 5. ✓
- OData spine `feis_solicitation_published` + `feis_non_competitive` (spec §2.1, §3) → Task 7. ✓
- CKAN adapters awarded / open / non-competitive (spec §2.1) → Tasks 5–6. ✓
- Linking on `document_number`; OData-spine / CKAN-backfill (spec §3.1) → the overwrite semantics in Task 3 + run order in Task 8. ✓
- Per-source isolation, `sync_run` provenance (spec §7, §6) → Task 8. ✓
- Runtime CKAN resource-ID resolution (spec constraint) → Task 5 (`resolve_resource_id`). ✓
- Never-delete + `first_seen`/`last_seen` (spec §6) → schema in Task 3. ✓
- Remove dead `open_data.py` and other flat scripts (spec §11) → Task 10. ✓
- Out of scope by design (Ariba Discovery JSON, attachments, TMMIS/PDF, export seam, supplier fuzzy dim, adjacent pcard/consulting/pipeline) → later phases; not in this plan. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step shows complete code; the only `NotImplementedError` (Task 5 `CkanSource.normalize`) is explicitly completed in Task 6 and not exercised before then. ✓

**3. Type consistency:** `normalize_document_number` signature matches across Tasks 2/6/7. `db.upsert_row(conn, row, *, overwrite)` used identically in Tasks 3/8. `Source` attributes (`name`, `overwrite`) and methods (`fetch`, `normalize`) match across base (Task 5), CKAN (5/6), OData (7), pipeline (8), and the fakes in tests. `HttpClient.get_json/post_json` consistent across Tasks 4/5/7. `db.counts` keys (`solicitation`, `award`, `noncompetitive`, `sync_run`) consistent across Tasks 3/8/9. CKAN `kind` values (`awarded`/`open`/`noncompetitive`) match the `_NORMALIZERS` map and the `CkanSource` constructor. ✓

Note carried forward for later phases: `award.source` participates in its UNIQUE key, so an OData award and a CKAN award for the same (doc, supplier) coexist as two rows with different provenance. This is intentional for P1 (OData is the cross-checkable spine); a future phase may collapse provenance if desired.
