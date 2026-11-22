# Ariba attachments capture

Implemented in `sources/ariba_attachments.py`, `sources/ariba_files.py`, and `sources/ariba_versions.py` (#117, #174).

The City posts competitive solicitations to Ariba Discovery, but the actual solicitation files (RFP documents, drawings, addenda, pricing forms) live inside the Sourcing event behind the **Respond** button. Clicking Respond registers our supplier account as a participant for archival download and never submits a bid. Written permission from PMMD was granted in July 2026.

## Scope and timing

- **Preview pages add nothing.** Every field on the public preview is already in the OData/Ariba feeds, and scraping previews fills zero titles. Only the attachments behind Respond add new data.
- **Recurring capture, not backfill.** Respond is disabled as soon as a posting closes, so capture only reaches open solicitations. A missed capture on a posting that later closes cannot be recovered, so per-event errors feed the nightly `failures` list (#223).
- **Open-event selection**: `open_solicitation_events` unions open rows from the solicitation spine with bridged `ariba_posting` rows whose `close_date` is today or later (if missing from the spine or carrying a later deadline). This keeps new postings visible even if the spine sync fails.
- **Completion check**: `bundle_complete` checks that a canonical `Doc<n>.zip` exists with no recorded omissions, collapsed links, or embedded missing-current-file markers. It is shared by capture and the nightly's `near_close_uncaptured` (`NEAR_CLOSE_DAYS`) Slack warning, which flags open postings closing soon with missing or incomplete bundles without failing the run (#223).

## Authentication and session handling

- Credentials come from `scrapers/.env` (`ARIBA_USERNAME` and `ARIBA_PASSWORD`, gitignored).
- The account must not have MFA; `login` fails immediately on a challenge page.
- `config._real_env` treats `.env.example` placeholder values as unset so unconfigured checkouts fail immediately instead of timing out in Playwright (#184).
- An explicit event permission denial gets one fresh login and one retry; a recovered session is reused for subsequent events. A second denial stays a failure. Closed or public-sector-rule restrictions do not trigger a fresh login.
- Post-Respond timeouts (neither an event page nor a recognized rejection) stay retryable failures. The error records URL, title, preview visibility, open tabs, and a text excerpt, and saves each tab's HTML and screenshot locally under `attachments/respond-timeouts/` (outside the `Doc*.zip` glob; unpruned and may contain session tokens; #304).

## Training events (#242)

`VERIFIED_TRAINING_EVENTS` maps reviewed document numbers to posting IDs. It is not a skip list: accessible training events are captured normally.

After clicking Respond, explicit Discovery messages (public-sector bidding rule or event closed) and Sourcing messages (permission or invalid link) raise `EventUnavailable`. Only a verified training event converts that into a nightly `ariba_training_restrictions` notice. Real procurement rejections, identity mismatches, anonymous sessions, unknown load errors, and attachment gaps remain failures. Restrictions never write a canonical zip and are rechecked while the posting remains open. Titles, year-2099 deadlines, and the API `isTest` flag are never used to decide unavailability.

Verified training events (checked 2026-10-20; all five carry 2099 deadlines and `isTest=false` in public metadata):

| Document | Posting ID | Observed outcome |
|---|---|---|
| `3861692164` | `15346406` | Discovery `public_sector_disabled` rejection |
| `3861744322` | `15346471` | Discovery `closed` rejection |
| `3949177873` | `15889645` | Captured (3 placeholder `.docx` files) |
| `3949149442` | `15889504` | Captured (3 placeholder `.docx` files) |
| `4044346425` | `16516115` | Captured (3 placeholder `.docx` files) |

## Per-file capture, bundling, and version merging

Ariba's built-in bundle download fails above 500 MB, so `AribaFileSource` downloads attachments one file at a time (#174) and builds `Doc<n>.zip` locally (`build_bundle`).

- **Naming and collisions**: Current filenames are disambiguated by `unique_names`. Conflicting file versions get hash-qualified paths (#256).
- **Partial runs and resume** (#243, #256): Per-file failures go into `Doc<n>.omitted.json`. Partial bundles stay eligible while the event is open, and unresolved gaps fail the nightly capture step. A raised error, zero files, or fewer than `_MIN_CAPTURE_RATIO` of listed files keeps a partial run pending (#182). A verified listing can clear a gap for a withdrawn file, but a missing current file stays missing even if an older version is already archived. Collision-only gaps require manual review.
- **Version merging**: `ariba_versions` preserves all older outer zip members byte for byte alongside newly downloaded versions, giving conflicting paths hash-qualified names. An embedded manifest maps the current listing's stable source keys and labels to stored paths and hashes (stored in the zip comment when small, or in a marked metadata member excluded from recursive indexing).
- **Atomic replacement**: Publication verifies all content hashes and rejects incomplete recursive counts (#199), replaces the zip and metadata atomically, and restores prior gap records on failure. Salvaging a closed event without a fresh listing keeps its conservative replacement guard.
- **Working directories**: Partial working directories are bound to the event fingerprint. A changed or unreadable fingerprint retires the old working directory instead of reusing its files. Complete files from retired snapshots can join a later merge as historical versions, not as proof of current coverage. Complete bundles are skipped by the capture selector; listing reconciliation runs only when capture is invoked.

## AribaWeb UI quirks

- **Two control types (`anchor_kind`)**: Popup-menu links (`bh=PML`) open a menu containing "Download this attachment"; popup-menu items (`bh=PMI`) download directly on click. Unknown anchors default to PML and fail if no menu appears.
- **Element identity**: `anchor_key` is stable across runs, not within one: downloading a file reparents menu containers and shifts keys. Use `anchor_key` for fingerprints, resume, and naming; re-locate elements mid-run by DOM handle (`pick_unclaimed`). Never include DOM handles or session IDs in `make_fingerprint`.
- **Request gate**: `save_as` can return while `ariba.Request.isRequestInProgress()` is still true. Wait for idle before the next action (#183) or visible controls may ignore clicks.
- **Popup menus**: References menus are mutually exclusive, and hidden items stay in the DOM, so link counts do not show visibility. `_ensure_clickable` reopens only the document's own `.awmenu` via the matching `_mid` trigger and checks visibility. `_close_popups` calls `Menu.hideActiveMenu()` and verifies dismissal (Escape is unreliable and Enter can activate an item). Keep `_await_menu_clear` so clicks cannot download from a previous document's menu (#183).
- **Incommensurable counts**: The picker's file count, the DOM tree count, and the zip leaf count do not match (#185). Capture logic must not compare against the picker count.

## Attachment index (`ariba_attachment`)

`ariba_attachment` is a derived index built from the local zip files (`--reindex`, offline), with one row per leaf file keyed by `(document_number, path)`. `index_zip` recurses into nested zips up to `_MAX_ZIP_DEPTH` and `_MAX_ZIP_ENTRIES`, reading sizes and CRCs from central directories (corrupt nested zips are indexed as opaque leaves). Nested paths prevent leaf-name collisions (#123). Zip files live under `<DATA_DIR>/ariba/attachments/` and are not committed or published.
