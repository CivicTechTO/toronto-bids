# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Keeping this file current.** State rules, mechanisms and *why* — not snapshots. Anything that drifts on its own (row counts in the store, how many files are cached, test counts, line numbers, "currently empty", "the current reality") does not belong here; describe the shape instead ("a few dozen open postings", "the cached corpus"). A measurement that *justifies a decision* is fine, cited to the issue that measured it ("0 false positives at either threshold, #77"): it records why the rule exists and does not go stale by being historical. When code changes the behaviour a section describes, update or delete that section in the same change.

## What this is

A CivicTechTO project that archives City of Toronto procurement data (solicitations, awards, non-competitive contracts, Ariba Discovery postings, suspended firms, bids, and agency/corporation records) into a local SQLite store and exports it as one public JSON artifact — so the record stays available after bids close. All active code is the `scrapers/` Python package (`uv`-managed, Python 3.12+, installs a `tb` CLI).

The core `tb sync` needs no browser, login or API key. Two later stages do: **document extraction** reads bids and awards out of PDFs with an LLM via OpenRouter (`OPENROUTER_API_KEY`), and a few captures drive a headed Chromium (Ariba attachments, TMMIS discovery).

## Commands

Everything runs from `scrapers/`:

```shell
cd scrapers
uv sync                                   # install deps (dev group with pytest included)
uv run pytest                             # all tests — offline, fixture-based, no network, no API key
uv run pytest tests/test_odata.py        # one file
uv run pytest tests/test_odata.py::test_normalize_solicitation_yields_spine_and_award   # one test
uv run tb sync                            # fetch all sources into files/bids.sqlite (exit 1 if any source failed)
uv run tb sync --only odata_solicitations,ckan_awarded
uv run tb status                          # row counts + last run per source/step
uv run tb export                          # write JSON artifact (default <DATA_DIR>/export/bids.json)
uv run tb nightly                         # the full scheduled run (see Deployment) + Slack summary
uv run tb extract --corpus award_summary --dry-run   # LLM extraction for one corpus; see --help
```

- **No lint/format/typecheck is configured** (no ruff/mypy/black). Don't invent those commands.
- CI (`.github/workflows/tests.yml`) runs `uv sync --locked && uv run pytest` — after changing dependencies, re-lock and commit `uv.lock` or CI fails.
- Council tests skip silently without `pdftotext` (`brew install poppler`); CI installs poppler so they run there.
- `TB_DATA_DIR` relocates the DB and downloads (default `scrapers/files/`).
- The browser deps (playwright, pyvirtualdisplay, python-dotenv) are **mandatory base deps** — they run in-line in `tb nightly`, so `uv sync --locked` installs them and no sync variant can strip them (they were an optional extra until a `uv sync --locked` on the deploy box dropped them and broke the nightly). Only the browser BINARY is a separate step: `uv run playwright install chromium`.
- `tb sync` hits live City endpoints and takes minutes; the tests are the dev loop.
- Other opt-in commands, none part of `tb sync`: `enrich-council` (`--virtual-display` for Xvfb), `enrich-titles` (offline over cached agendas; `--reports` fetches staff-report PDFs), `enrich-awards` (`--download`), `enrich-committee-awards` (`--scrape`), `enrich-agencies` (`--only`, `--fetch`, `--scrape`, `--portal`, `--record`), `enrich-ariba-attachments` (`--capture`, `--ingest`, `--reindex`), `amounts`, `manifest`, `record-step`.

### Deployment

`tb nightly` runs `sync → award summaries → portal → Ariba attachments → agencies → council (monthly, 1st only) → supplier rebuild → export`, each step isolated so one failure never stops the rest and the export runs even after a partial sync. `tb-nightly-run.sh` (the systemd `ExecStart`) then chains `publish-data.sh` regardless of the nightly's exit code. The timer runs **Tuesday through Saturday** at 05:30 America/Toronto (#180: the City transacts on no weekend day, and a 05:30 run reports on the previous day). See `deploy/README.md` and `docs/superpowers/specs/2026-07-17-deployment-design.md`.

- Credentials live only in `~/.config/toronto-bids/tb.env` (mode `0600`) on the server, never in git: `TB_SLACK_WEBHOOK` (unset → no Slack post, the run still works), `GH_TOKEN`, `ARIBA_USERNAME`/`ARIBA_PASSWORD`, `OPENROUTER_API_KEY`. `scrapers/.env.example` documents them for local use.
- Ariba attachments and the agency board-report scrapes drive a headed Chromium under Xvfb **in-line inside `tb nightly`** (#135/#146). There is no separate browser timer; a standalone `tb-ariba-attachments.timer` was removed from git but #180 found it still enabled on the live box, double-running Ariba capture — check the box, not just git.

## Architecture

Data flow: sources fetch/normalize → SQLite upsert (`store/db.py`) → linking passes → enrichment / extraction steps → export (`export/`).

### Source contract (`toronto_bids/sources/base.py`)

`Source` is a duck-typed Protocol: `name: str`, `overwrite: bool`, `fetch(http) -> Iterable[dict]` (does I/O), `normalize(raw) -> Iterable[Row]` (pure — testable against `tests/fixtures/` without network; keep it that way). There is no registry: sources are a hardcoded ordered list in `pipeline.default_sources()`. To add one: write the class in `sources/`, add its model to `models.py` and the `Row` union in `base.py` if new, append an instance to `default_sources()`, and declare any feed fields it reads in `sources/schema_check.py`.

### Ordering and overwrite semantics

Order in `default_sources()` matters: `schema_check` first (drift detection), then the OData spine (`overwrite=True`, authoritative), then CKAN backfill (`overwrite=False`), then Ariba and suspended firms. `ckan_pipeline` is the one CKAN source with `overwrite=True`: no spine covers the forward-looking `capital_project` table, so CKAN is authoritative there (#69). Both upsert modes COALESCE (`db._upsert_keyed`): with `overwrite=True` a new non-NULL value wins but NULL never wipes an existing value; with `overwrite=False` only currently-NULL columns get filled. Feed rows are never deleted — archive semantics with `first_seen`/`last_seen` on every data table. The exceptions are **derived** tables rebuilt from bytes we hold (supplier dimension, Ariba attachment index, extraction-backed bid/award tables — see below).

### Failure surfacing — a caller cannot learn a step failed by not catching something

`pipeline.run_source` catches all exceptions: one failing source never stops the others, and partial rows are committed. Failures are recorded in `sync_run`, printed to stderr as `FAILED <name>: ...`, and make `tb sync` exit non-zero. `pipeline.sync` therefore RETURNS `[(name, error)]` rather than raising — so anything wrapping it in try/except reads clean on a night every feed died (#178). A sync run writes one `sync_run` row per `default_sources()` entry plus one per `linking_passes()` entry; both lists are exported so the report counts them rather than restating them.

The same lesson recurs at every level of the nightly, and the pattern is fixed:

- `_run_step(steps, failures, name, fn, conn=conn)` records each nightly step (except `sync`, which has its own rows) as a `sync_run` row, so `tb status` reflects the whole nightly (#176).
- **`fn()` returning normally is not proof nothing failed.** Steps whose sub-units swallow errors — per portal, per agency body, per Ariba event, per extraction document — append them to the shared `failures` list instead of raising. Call `_mark_if_swallowed_failures(steps, failures, conn, run_id, before_len)` right after `_run_step`; it corrects both the Steps entry and the `sync_run` row if the list grew. It does nothing for a step that raised (that is already marked). New swallowing code must feed `failures`, or it reads ✅ forever (#178, #219, #223, #226).
- A genuinely *skipped* step (council, not the 1st) gets no `sync_run` row — "skipped" and "ran and failed" must stay distinct.
- "What's new" is two real `SELECT COUNT(*)`s diffed (`_bid_count`/`_source_row_counts`), never a store function's return value — rebuilds and dedup make those the corpus total or the upsert count (#177, #142, #218).
- `publish-data.sh` is bash in its own unit, so it records via `tb record-step NAME {ok,failed}` (helpers in `deploy/publish-lib.sh`, split out so `tests/test_publish_lib.py` can drive them). The best-effort R2 mirror gets its own `r2_mirror` row, recorded only on a real attempt — it stayed dead for 8 nights while `publish` read fine (#173). `verify_artifact_size` HEADs each published `bids.sqlite` (`curl -sIL`, final response's `Content-Length`) and compares to the local byte count — it checks what is *live*, not whether an upload exited 0. Skipped under `TB_PUBLISH_DRY_RUN=1`; read-only and best-effort.

### Schema drift detection (`sources/schema_check.py`)

Normalizers read feed fields with `raw.get(...)`, so a field the City renames silently NULLs a column forever. `schema_check.py` declares exactly the fields the OData and CKAN normalizers read and samples those feeds on each sync, failing loudly on missing keys. When a normalizer starts reading a new field, add it to the declared sets in the same change. Ariba and suspended-firms are outside its coverage (suspended-firms parsing raises on header drift itself; Ariba field drift is unguarded).

### Document extraction (`extraction.py`, `extract.py`, #205/#213)

Bids and awards inside PDFs — Award Summary Forms, committee staff reports, composite reports, TRCA/EP/Zoo board reports — are read by **one LLM prompt**, not per-source parsers. The incumbent regex/pdfplumber parsers were measured against the model and deleted (#205, e936004); don't rebuild them.

- **`extraction.CORPORA`** maps a corpus name to the `background_pdf` rows it covers (`trca`, `ep`, `zoo`, `award_summary`, `committee`, `composite`). A new document-backed source is a new corpus entry plus a branch in `backfill_from_extraction`, not a new parser. Body registration is still repeated in several places (`cli.py` choices and body lists, `_CORPUS_SOURCE`, `_CORPUS_BUYER_SLUG`) — grep for an existing corpus name when adding one.
- **`extract.py`**: `build_prompt`, `validate_extraction` (refuses a malformed response with `ValueError`), and `ExtractionClient` (OpenRouter; `MODELS` is tried in order). Per model: 429/5xx/transport/malformed output retry with backoff then fall through to the next model; any other 4xx (retired slug, no credit) skips straight to the next; 401/403 raise immediately since one key serves every model. Which model production should default to is an open maintainer decision (#208).
- **Cache**: `extraction_cache` keyed `(sha256, extractor_version)`. `EXTRACTOR_VERSION = v1-<hash of the prompt>`, so **any prompt edit invalidates the whole cache** and the next run re-extracts everything through the paid API. Treat a prompt change as a deployment event. Results are cached per document, so re-running is offline and free once cached.
- **`extract_corpus`**: skips documents with no text and documents the classification gate labels non-procurement (`config.CLASSIFICATION_LABELS_PATH`, a static machine-label snapshot — URLs not in it are always extracted, #229). Per-document errors are counted and logged, never fatal to the corpus. `check_declared_counts` flags a contract whose bid count falls SHORT of the count the document declares (overshoots are kept — a declaration sometimes covers only compliant bids). Flags are log-only; they are mostly noise on composite reports, which publish a count but no bidder list.
- **`extract_and_backfill`**: extracts uncached documents, then backfills. With no key it still runs when every eligible document is cached; otherwise it raises. Pass `failures` and it appends one `extract:<corpus>` entry when any document failed.
- **`backfill_from_extraction`** rebuilds each source's rows from the cache under a fixed contract (#215): **derive every row first, delete only on success**; a table whose derived set is empty is left untouched (a machine without the cache must not erase the archive); delete + insert run in one transaction; and it raises, writing nothing, if fewer than `_MIN_SWAP_COVERAGE` of the corpus's ever-extracted documents are cached at the current version (the state right after a prompt edit). Known gaps: the rebuild resets `first_seen` (#218); the `composite` corpus is every `bgrd` report, not just 2009-2012 composites, and stores `call_number` un-normalized (#216); reference-less agency contracts are dropped and `native_ref` is the raw printed string (#217).
- **`hst_basis` is load-bearing** — comparing prices across bases is wrong. The backfill maps the model's `amount_basis` onto it (`including_HST`→`including`, `plus_HST`→`excluding`; "net of taxes" and unknown → NULL, never a guess).

### Parsing discipline: prove clean extraction, or come back — never chase edge cases

This applies to the remaining rule-based parsers (agenda HTML tables, appendix fields, feed normalizers) and to judging the model's output. Scraped documents invite an endless patch-the-next-case loop, and this repo has run that loop more than once.

**When an extractor is wrong, do not fix the failing case. Ask whether the corpus can be extracted cleanly AT ALL by a small set of statable rules — and if the answer looks like no, STOP AND COME BACK TO THE HUMAN rather than grinding.** "No clean extraction exists here" is a legitimate and complete finding (#83 reached it for council staff reports). A parser held together by per-document exceptions is a liability.

- **Measure against ground truth the documents themselves carry, never against the code being replaced.** Award Summary Forms state `Number of Bids Received`; EP board reports state "four (4) submissions were received". Compare to *that*.
- **Count the rules, and watch whether the count moves.** Convergent: each new wrinkle collapses into a rule already written. Divergent: every fix reveals a new wrinkle and the rule count climbs (#151's four rejected regex attempts). **Divergence is the signal to stop and re-ask the question, not to write rule five.**
- **Refuse and log; never guess.** A refused row is a known gap someone can act on; a guessed row is silent corruption that reads as data.
- **A disagreement with ground truth is often the DOCUMENT's defect** (a table listing only the compliant subset; a malformed price like `$1,479,386,.57` in the City's own PDF). That is a finding, not something to parse around: the archive records what the City published.
- Whether cells, regex or a model is right is **measured per corpus** each time (#116: "read cells where the PDF has cells" held for Award Summary Forms and EP reports but not council staff reports). Ground-truth label sets live under `docs/ground-truth/`.

### Linking

- Everything competitive is keyed on the normalized 10-digit `document_number` (`linking/document_number.py`: strip non-digits, require exactly 10, reject a placeholder denylist). Non-competitive contracts live in a separate keyspace (`workspace_number`) — there is no join between them.
- **A third keyspace** is `composite_award.call_number` (`linking/call_number.py`, #96): 2009-2012 awards predate Ariba and identify themselves by Call Number, in two shapes, `3905-10-0097` (RFQ/RFP) and `317-2010` (Tender Call). The prefix vocabulary ("Request for Quotation", "RFQ", "Tender Call No.") carries no information — match on the shape, never the prefix — and a trailing `, Contract No. 10TE-17WS` is a *different* identifier. A call number can strip to exactly 10 digits, so code taking a 10-digit reference must check `normalize_call_number` first.
- **The fourth keyspace** is agencies: `(buyer_id, native_ref)` (see Agency capture). No join to the City keyspaces is attempted.
- `solicitation_link` (#165) records pre-Ariba council `reference` ↔ `document_number` pairs matched by `match_pre_ariba_solicitations`, and the export uses it to attach pre-Ariba bids and staff reports to solicitations.
- Linking passes run on every sync, regardless of `--only`, isolated like sources (`pipeline.linking_passes()`, failures recorded and returned):
  - `title_cleanup` (`title.py:clear_placeholder_titles`) runs first. The City often publishes the document number *as* the title (`Doc-3524228095`); `clean_title` NULLs those at ingest because a non-NULL placeholder clobbered real titles and blocked backfill (COALESCE guards NULL, not *worse*). The pass re-applies that to rows written before the rule existed, and clears any `title_source` left beside a NULL title. **`title IS NULL` means "no title published" — do not use `title LIKE 'Doc-%'`**, which both misses placeholders and catches real titles that lead with the doc number.
  - `ariba_bridge` (`linking/ariba.py:bridge_postings_to_spine`): `solicitation.ariba_posting_link` embeds the rfx id (`/RfxEvent/preview/<id>`), so the spine names the join outright. `sources/ariba.py` also bridges inline from the detail call's `externalRfxId`, but that call 500s often, so the pass fills the rest (NULLs only). The older `discovery.ariba.com/rfx/<id>` links are **not dead** (#117) — they redirect into the modern viewer, which accepts the legacy ids. The genuinely dead formats are merx, Lotus Notes `.nsf` and `n/a`. A posting with no matching `solicitation` row surfaces in the export's `unlinked_ariba_postings`.
  - `amount_backfill`, `amount_labels` — see Gotchas.
  - `supplier_dimension` (`linking/supplier.py:build_supplier_dimension`) rebuilds the supplier dimension from scratch: a normalized string key groups raw names across `award`/`noncompetitive`/`suspended_firm`/`bid`/`composite_award`/`agency_award`/`agency_bid`; FKs are cleared and re-backfilled each run. Legal suffixes (Inc, Ltd) are deliberately kept in the key. Including `bid` is the point (#87): most bidders never win, so a dimension built from winners alone cannot answer who loses or whether a suspended firm kept bidding. `bid` and `agency_bid` name their supplier in `bidder_name_raw` — see `_NAME_COLUMN`.

### Council agendas: titles, bids and staff reports (`sources/bid_award_panel.py`, `sources/legacy_titles.py`) — not Sources

`tb enrich-titles`. The City publishes no real title for most solicitations (#70), so several passes fill `title` — **all only ever touch a NULL; a title the City published always wins.**

**Provenance lives in `title_source`, never `source`.** The OData spine owns `source` and re-upserts every row each sync, so anything a title pass wrote there is clobbered while the title survives. `title_source` is deliberately absent from the `Solicitation` model so `db.upsert_row` cannot write it. It records the provenance of a REAL title only.

Two agenda series share one structure: **BA** = Bid Award Panel (2017 →), **BD** = Bid Committee, its predecessor (2009-2016). **The Bid Award Panel was abolished on 2025-10-01** (By-law 766-2025; award authority up to $30M went to the Chief Procurement Officer), so the cached agenda corpus is complete and final — there is no successor series to find. Awards above that limit go to a Standing Committee or Council (see Committee awards).

- **Scraping** needs a headed Chromium (TMMIS is Akamai-gated; headless is blocked), one browser for the whole run. Raw HTML is cached under `<DATA_DIR>/council/agendas/`, so re-parsing never re-drives a browser. **Meeting references cannot be derived** — the schedule omits meeting numbers in older terms and date order is wrong in both directions — so `discover_meetings` probes and confirms against each page's own stated date.
- **Closed council terms are never re-probed (#177).** Every `term_starts` entry but the LAST is a closed term by construction; `.closed_terms.json` in each agenda dir records its confirmed last meeting the first time it's found. Only the last term is probed live. Consequence: every term list (BA/BD, `ZB_TERM_STARTS`, `EP_TERM_STARTS`) must gain the next term after an election, and **adding it before the old term's last meeting is captured freezes the old term early** (#225).
- **Titles**: the legacy archive's Ariba posting pages (`<title>` is the solicitation's real title; offline) outrank Bid Award Panel headings — that precedence lives in `legacy_titles`' query, not call order. Ariba-era agenda items match on *any* 10-digit number, never on the word "Ariba" (the labels vary). Pre-Ariba items (`match_pre_ariba_titles`, #77/#90) name no document number and match on **(supplier, award value)**: the award value is the "net of all applicable taxes" figure (calibrated on Ariba-era items with ground truth); **the value carries the match, the supplier only confirms it** (`supplier_tokens`, looser than `supplier_key` because it only confirms, never merges), and only a *unique* match is taken (0 false positives in calibration, #77). Reach is bounded by history: there is no general join key for 2012-2018.
- **Bids**: agendas tabulate every bid, including losers, in real `<table>` markup — the record of *who lost* and *how competitive* an award was, which the rewrite spec had called unrecoverable (#84). `parse_bid_tables` reads both layouts (#94): BA is row-major (one row per bidder); BD puts whole columns in single cells, so `_cell_lines` reads the markup back as lines (`text_content()` fuses `<p>` runs and destroys the column) and the bidder and price columns are zipped positionally — **an unequal pair is refused**, since one stray line would misattribute every bid after it. The BD path runs only where the row-major path declined, scoped to direct-child rows. 2009-2012 agendas carry composite reports with a bid *count* and no bidder list, so those years have no losing bidders anywhere.
- **Two traps**: `hst_basis` is load-bearing; and **`bid_price` is not `award_amount`** — a bid excludes contingency. The City writes outcomes in the price column (`Non-Compliant`, `No bid`), so the raw string is kept and `bid_price_numeric` is NULL for exactly those.
- **Staff reports** (`background_pdf`, kind `bgrd`): the agendas *are* the index the spec said didn't exist; each report is attributed to the item that links it. `--reports` fetches them over plain HTTP. The download queue keys on **`sha256 IS NULL`, never `text IS NULL`** — image-only PDFs yield no text and would otherwise be re-downloaded every run (#96). What the reports can and cannot add was measured and closed in #83 — **don't reopen without new evidence**: they cannot fill titles (panel reports exist only for solicitations that already have titles; everything untitled was staff-delegated), and their bid lists are not machine-readable by rule-based extraction.

### Pre-feed awards (`composite_award`, #96)

The City's feed publishes almost no awards for 2009-2011, so for those years the composite-report appendices *are* the record, ingested as awards in their own keyspace (Call Number). Measured rules worth keeping regardless of extractor:

- The award value is the **FIRST net-of-taxes figure** — the initial term, excluding option years; it matched the feed's own `award_amount` on 98.6% of overlapping appendices. Option-year and "total potential" figures can be 2x larger.
- **Store the amount alone, never the matched phrase**: `amount.py:parse_amount` is strict, so `"$420,000.00 net of all applicable taxes"` leaves the numeric column NULL and silently zeroes every SUM.
- Split awards are one row per winner; a winner the appendix's bidder field never names is refused (the value section labels its *periods* identically to its winners).

Extraction now comes from the LLM `composite` corpus; its scope and keying regressions are tracked in #216.

### Award Summary Forms (`sources/award_summary.py`, #114) — the bid record after the panel

`tb enrich-awards --download` archives the Toronto Bids Portal's **Award Summary Form** per award; bids come from the `award_summary` extraction corpus.

- **No browser, same OData spine.** The portal runs on `feis_solicitation_published`; the PDF rides on the record in `uploadedFilesStaff[].bin_id` → `c3api_upload/retrieve/pmmd_solicitations/{bin_id}`. `secure.toronto.ca` is not Akamai-gated.
- **"Available for 18 months" is a client-side filter** (`Latest_Date_Awarded gt <today-18mo>`). We omit it deliberately and get awarded records back to 2010 — the City could enforce it server-side at any time.
- **`bid.reference` is nullable and `bid_key` COALESCEs both identifiers.** Which is present says where a bid came from: a panel bid has a council item and (pre-2019) no document number; an award-summary or committee bid is the reverse.
- **Coverage is bounded**: the form exists only for awards over $500,000 (the panel had no floor), so the bid record thins permanently for small awards.

### Committee/Council awards (`sources/committee_awards.py`, #164) — a thin slice, measured

`tb enrich-committee-awards` (offline by default; `--scrape` drives the browser). Route: voting-record CSV → agenda-item page → staff-report PDF → `committee` extraction corpus → `bid`, keyed like an award-summary bid. Self-bounding: it only chases items that name a spine solicitation and have no bids yet. Report bytes are content-addressed at `documents/committee_award/<sha256>.pdf`.

**It works end to end and reaches very little. That is the finding — the join key is the ceiling, not the parser.** Discovery keeps only items whose agenda title carries a 10-digit doc number, and very few award items do; most of the rest are amendments and non-competitive contracts with no bids by nature, and most competitive awards never reach committee at all. Don't reopen expecting a better extractor to help. A committee report can carry several contracts; each contract's bids key on that contract's own document number when it names one.

### Ariba attachments (`sources/ariba_attachments.py`, `sources/ariba_files.py`, #117/#174) — the documents behind Respond

The City posts every competitive solicitation to Ariba Discovery, but the **actual documents** (RFP parts, drawings, addenda, pricing forms) live inside the Sourcing event, reachable only as a participating supplier after clicking **Respond**. Authorized by PMMD in writing (2026-07). Respond registers our account as a participant for archival access and **never submits a bid**.

- **The preview itself is worthless as content** — every field it shows is already in the feed, and browser-scraping it fills zero titles. Don't rebuild a no-Respond detail scraper. The attachments are the only real gain.
- **Respond is disabled the moment a posting closes**, so this reaches only currently-open solicitations — a *recurring capture, not a backfill*. A capture failure on a posting that then closes is permanent loss, which is why per-event failures feed the nightly's `failures` list (#223). `open_solicitation_events` skips the City's 2099-dated mock training postings (`TRAINING_POSTINGS`).
- **Capture is per-file** (`AribaFileSource`, #174): Ariba's bundle download hard-stops above 500 MB and one real event's single picker row exceeded it, while every individual file was far smaller. The canonical `Doc<n>.zip` is built locally (`build_bundle`, flat, collisions disambiguated by `unique_names`).
- **Resume and refusal rules** (a bundle is permanent — an empty or near-empty zip reads as "archived" forever): a per-file failure is recorded in `Doc<n>.omitted.json` and the bundle is written anyway; a raised error, zero files, or under `_MIN_CAPTURE_RATIO` of the traversal's own listing keeps the event pending (#182 — measured: content trees that stopped resolving mid-run downloaded 1-2 of 39 files); a re-capture that would shrink an existing bundle is refused (#199). Once a posting closes, `finalise_partial` bundles what is on disk — gated on Respond reading disabled **stably** across several reads, because the preview renders buttons disabled while loading.
- **Two kinds of document control** (`anchor_kind`): a popup-menu LINK (`bh=PML`) opens a menu holding "Download this attachment"; a popup-menu ITEM (`bh=PMI`) downloads on a direct click. Treating every anchor as PML failed every PMI. An unrecognised anchor defaults to PML, which checks what it got and fails loudly.
- **Identity**: `anchor_key` is durable ACROSS runs but not WITHIN one — downloading a file re-parents menu containers and shifts most keys on an intact tree. Use it for the fingerprint, resume and naming; re-find elements mid-run by their DOM handle (`pick_unclaimed`). Neither handle nor session ids may reach `make_fingerprint` (AribaWeb re-mints them per session).
- **Escape hides the tree**: the menu-dismissal guard (`_await_menu_clear`, a real wrong-bytes protection) collapses the expanded reference sections; `_ensure_clickable` re-opens them and **verifies visibility rather than trusting the re-open**, because the mechanism is not understood. Point fixes to this widget have regressed at scale (#183).
- **The picker's file count, the tree's count and the bundle's leaf count are not commensurable** (#185) — no capture logic compares against the picker.
- **`ariba_attachment` is a derived INDEX of the on-disk zips**, one row per LEAF file keyed `(document_number, path)` with the full nested path (leaf names collide across nested zips, #123). `index_zip` recurses into nested zips (bounded by `_MAX_ZIP_DEPTH`/`_MAX_ZIP_ENTRIES`; a corrupt nested zip degrades to an opaque leaf) reading sizes/CRCs from central directories. Rebuilt from the bytes (`--reindex`, offline), not diff-upserted. Bytes live under `<DATA_DIR>/ariba/attachments/`, never committed.
- **Login**: a supplier account from `scrapers/.env` (`ARIBA_USERNAME`/`ARIBA_PASSWORD`, gitignored — the repo is public). **No MFA, by requirement** — an unattended login cannot answer a challenge, and `login` raises on a challenge page. `config._real_env` treats the `.env.example` placeholder values as unset, so an unfilled checkout fails fast instead of 30s into Playwright (#184).

### Agency capture (`buyers.py`, `sources/trca_board.py`, `sources/zoo_board.py`, `sources/ep_board.py`, `sources/bids_tenders.py`, #103/#135) — the fourth keyspace

`tb enrich-agencies`, also run in the nightly. Agencies and corporations procure outside the PMMD feed; their records live in `agency_solicitation`/`agency_award`/`agency_bid` keyed `(buyer_id, native_ref)`. The `buyer` dimension carries `partnered`/`funding_share` so exports segment rather than mix; headline counts stay City-only.

- **Award records come from board reports**, extracted by the `trca`/`ep`/`zoo` corpora: TRCA via eSCRIBE (plain HTTP), the Zoo (`ZB`) and Exhibition Place (`EP`) committees via TMMIS (headed-browser discovery with the shared prober, plain-HTTP legdocs PDFs). Most EP board reports are not procurement awards; the classification gate and the model sort that out. A report that routes values to a CONFIDENTIAL ATTACHMENT yields `value_confidential=1`, not a fake NULL.
- **On eSCRIBE, a document is what the page LINKS TO, never what it LOADS (#175).** Meeting pages serve their stylesheet and logo through the same `FileStream.ashx?DocumentId=` handler as their PDFs; `escribe_document_urls` is anchor-scoped and `_prune_page_assets` unqueues assets already written — a narrow exception to "rows are never deleted" (unheld AND shown as an asset by a page just loaded). Remaining 404s on minutes links are broken at source and stay queued.
- **The bids&tenders portal listings** are fetched over plain HTTP (`POST /Module/Tenders/en/Tender/Search/<NodeId>` with the session cookie and the FIRST antiforgery token; **never send `sort=`** — it errors). `fetch_listings` is gated (`PermissionError`) until a body's written grant is recorded in `docs/permissions/` and its `config.BIDS_TENDERS_PORTALS` entry is enabled — the PMMD/Ariba precedent. A broken reply (non-2xx, non-JSON, `success: false`) is a FAILURE, never "no open bids" (#226). Rows land with `overwrite=False` (backfill only). `parse_listing` is **provisional** — validated only against a synthetic fixture; `--record` dumps raw JSON to seed a real fixture (#140). Bid documents stay out of scope: they sit behind the Vendor clickwrap. Each grant's conditions (rate limit, attribution) are in its permission file; attribution statements are exported in `meta.attribution` from the portal config (#228).
- **TRCA is deadline-bound**: Bill 97 amalgamates it away on 2027-02-01. eSCRIBE covers 2019 onward; the pre-2019 Laserfiche back-catalogue is not yet captured (#224).
- The pre-2019 City-spine EP slice (Client_Division "Exhibition Place") and the post-2019 Board-of-Governors awards are separate coexisting keyspaces (#130).

### Council enrichment (`sources/council.py`) — not a Source

A separate opt-in step (`tb enrich-council`, monthly in the nightly). It fetches council decisions for each `suspended_firm.council_authority` (headed browser; TMMIS is Akamai-gated) and extracts staff-report PDFs with `pdftotext`.

### Export seam (`export/`)

`build_export_document(conn)` in `export/document.py` is deterministic given a `generated_at` (result-shaping queries ORDER BY, no file I/O); `export_json` is a thin serializer over it. A new publishing destination is another function over the same builder — keep all shaping logic in `document.py`. The per-solicitation `documents` array unions Ariba attachment leaves, Award Summary Forms and staff reports joined through an **exact** council-`reference` ↔ `document_number` bridge (dual-key `bid` rows plus `solicitation_link`) — no fuzzy matching. Internal hashes stay private; Ariba files carry no URL (bytes unpublished).

## Gotchas

- **Never aggregate `award_amount` / `contract_amount`** — they are `TEXT` holding the City's string verbatim (`"$1,317,169.92 CAD"`, `"kj"`, three amounts concatenated). `SUM()` coerces text prefixes to 0 or truncates, and SQLite sorts text above every number so `award_amount > 1000` matches *every* row. Aggregate `award_amount_numeric` / `contract_amount_numeric` (`REAL`, parsed by `amount.py`). A NULL numeric beside a non-NULL raw string means the raw value is not a single CAD amount — deliberate, not missing data.
- **Amounts come in three tiers, and they must not be mixed (#74):**

  | tier | column | meaning |
  |---|---|---|
  | raw | `award_amount` | what the City published, verbatim |
  | parsed | `award_amount_numeric` | machine-derived, conservative, no guesses |
  | labelled | `award_amount_labelled` + `award_amount_verdict` | human judgement, provenance in git |

  `SUM(award_amount_numeric)` is a defensible undercount; `SUM(COALESCE(award_amount_labelled, award_amount_numeric))` is fuller and opts into human calls knowingly. **A label never reaches `*_numeric`** — that column's contract is what makes `numeric IS NULL` a usable review queue. The labelled columns are deliberately absent from the `Award` / `NonCompetitive` models (same reason as `title_source`): every sync re-upserts these rows, so anything `db.upsert_row` can write, the feed can clobber.
- **Verdicts live in `toronto_bids/data/amount_labels.toml`**, keyed on the raw string, so any future row carrying a labelled string is covered on arrival, and a label simply stops matching if the City fixes the string. Vocabulary: `amount`, `not_an_amount` (a rate/formula — the NULL is correct), `corrupt` (mashed upstream), `unknown`, `not_an_award` (exclude from aggregates — e.g. a phantom OData row with supplier `'kj'`). **`'$1,311,936.00 USD'` is deliberately `unknown`** — it parses cleanly but is USD in a CAD archive. `tb amounts unlabelled` lists anything with no parse and no verdict and exits non-zero.
- **`*_numeric IS NULL` only means "not machine-parseable" because `amount_backfill` runs**: rows written before `amount.py` existed keep their NULL forever otherwise. The pass recomputes only where the raw already parses, so it adds no judgement.
- `award` rows are per-source (`source` is part of the key: OData spine plus `ckan_awarded` cross-check), so a naive `COUNT(*)`/`SUM(award_amount_numeric)` double-counts — filter to `source='odata'` or GROUP BY. Both hazards apply at once.
- Some award amounts are implausible in **both** feeds (doc `3901175008`: `9054510208` — $9.05B to an individual). Upstream data, not a parsing bug — don't "fix" it in a normalizer. `SUM(award_amount_numeric)` is *faithful* without being *trustworthy*.
- `award` holds one row per award **line**, not per (document, supplier): standing-offer call-ups award the same supplier many times on one document. Uniqueness is an expression index (`award_line_key`) that COALESCEs the nullable key parts, because SQLite treats NULLs as distinct. **`db._upsert_keyed`'s conflict target must match that expression exactly** — see `_CONFLICT_TARGETS`.
- Ariba detail calls return HTTP 500 a large fraction of the time; runs are idempotent and later runs fill the gaps. Expected, not a bug.
- CKAN resource UUIDs rotate on refresh — they are resolved at runtime via `package_show`, never hardcoded.
- The design spec (`docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md`) records dead-end data sources (retired CKAN datasets, Ariba HTML shell, Ariba public attachment API) — don't rebuild against them.

## Agent skills

### Issue tracker

GitHub issues on `CivicTechTO/toronto-bids`. **`gh issue create` is blocked here — create issues via `gh api repos/CivicTechTO/toronto-bids/issues`, and confirm before any outward write on this public repo.** See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, label string equal to role name (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — root `CONTEXT.md` + `docs/adr/`, both created lazily; until then this file, `docs/superpowers/specs/`, and the numbered issues carry the decisions. See `docs/agents/domain.md`.
