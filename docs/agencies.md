# Agency and corporation sources

City agencies and corporations procure outside the PMMD OData feed. Their records are collected by `tb enrich-agencies` (and dedicated commands for Bonfire, MERX, TTC, and Waterfront) and run in `tb nightly`.

Board records keep `(buyer_id, native_ref)` ingest keys in `agency_solicitation`, `agency_award`, and `agency_bid`. Portal pages use `portal_listing` and canonical procurement identities (#124). The `buyer` table tracks `partnered` and `funding_share` so exports keep agency totals separate from City-of-Toronto totals.

For individual agency adapters, see:

- [bonfire-listings.md](bonfire-listings.md)
- [merx.md](merx.md)
- [ttc-history.md](ttc-history.md)
- [waterfront-awards.md](waterfront-awards.md)
- [document-extraction.md](document-extraction.md) (for `agency_derivation`, coverage checks, and reviewed agency identities)
- [permissions/](permissions/) (for written archival permissions and access rules)

## Board reports (TRCA, Toronto Zoo, Exhibition Place, Toronto Parking Authority, TCHC, TPL, TPSB)

Implemented in `buyers.py`, `sources/trca_board.py`, `sources/zoo_board.py`, `sources/ep_board.py`, `sources/tpa_board.py`, `sources/tchc_board.py`, `sources/tpl_board.py`, and `sources/tpsb_board.py` (#103, #135, #332, #333, #334, #335).

- **TRCA**: Discovered via eSCRIBE over plain HTTP (`GetCalendarMeetings` POST, then meeting detail pages).
- **Toronto Zoo (`ZB`), Exhibition Place (`EP`), and Toronto Parking Authority (`PA`)**: Discovered via TMMIS using the shared headed-Chromium prober (`auto_terms=True`); PDFs download over plain HTTP from legdocs.
- **TCHC**: Discovered over plain HTTP from `torontohousing.ca/board-and-governance/past-board-and-committee-meetings` (public BIFAC and Board of Directors meetings from 2021+, where Drupal hosts individual PDF attachments). Captures Quarterly PAC Awards reports (`$500K–$5M` awards, direct awards, and bid counts) plus individual BIFAC/Board Vendor/Contractor/Direct Award and Change Order reports, skipping combined meeting packages, agendas, minutes, deputations, charters, audits, and spend reconciliations. Historical meeting HTML older than the previous calendar year is cached locally under `TCHC_MEETINGS_DIR`.
- **TPL**: Discovered over plain HTTP from `tpl.ca/about-the-library/board/meetings/` (current year plus `board-meetings-YYYY/` archives from 2014+, aligned with TPL's Bonfire history). Skips unheld future meetings and non-procurement subcommittees (naming, performance review, selection, strategic planning), and indexes individual staff PDFs on `bibliocms.com` (site 164) for contract/tender awards, extensions, increases, and sole-source reports while excluding consolidated packages, agendas, minutes, deputations, and policies. Historical year/meeting HTML older than the previous calendar year is cached locally under `TPL_MEETINGS_DIR`.
- **TPSB**: Discovered over plain HTTP from `tpsb.ca/home/current-and-past-meetings/` and yearly archive pages from 2015+. Targets confirmed Public Board Minutes PDFs (which embed both approved Board motions and the full `PUBLIC REPORT` documents), archives each intact PDF under `TPSB_REPORTS_DIR`, and deterministically slices procurement reports (monthly `>$1M` Contract Awards / Extensions / Pre-Qualifications, Annual Non-Competitive Purchases `>$25K`, and Annual Co-operative Procurements) into per-report `#page=N` `background_pdf` rows with a text-free base-URL marker row so already-inspected Minutes PDFs are not re-downloaded. Historical archive HTML older than the previous calendar year is cached under `TPSB_MEETINGS_DIR`.
- Most EP board reports are not procurement awards; the document classifier and shared extraction prompt filter them.
- Reports that put contract values in a confidential attachment set `value_confidential = 1` rather than leaving an unexplained `NULL`.
- Exhibition Place has two separate keyspaces: pre-2019 records on the City OData spine (`Client_Division = "Exhibition Place"`) and post-2019 Board of Governors reports (#130).

### TRCA eSCRIBE rules and recovery

- **Anchor-scoped discovery** (#175): eSCRIBE serves stylesheets and logos through the same `FileStream.ashx?DocumentId=` endpoint as PDFs. `escribe_document_urls` extracts only `<a>` links, and `_prune_page_assets` removes unheld rows proven to be page assets on a freshly fetched page.
- **Download failures** (#244): Missing PDFs, non-PDF responses, and calendar/meeting errors report as `download:trca` failures while other documents and cached extractions continue. Missing files stay queued; a 403 or 404 is never treated as proof of confidentiality or permanent removal.
- **Verification pages**: Nightly TRCA capture falls back to a single anonymous headed Chromium session only when eSCRIBE returns a verification page. It checks FileStream identity and PDF structure, paces requests, and backs off on rate limits or `Retry-After`.
- **TRCA decision and stage reviews**: `award_review_context_json` preserves future-agenda context across date changes and URL aliases. Source-bound `award_review` entries admit carried claims, drop unallocated shared ceilings, and keep withheld proposals; `participation_review` separates procurement stages without modifying cached extraction payloads. Missing or changed evidence aborts the swap. Reviewed observations can be withdrawn even if nothing else derives, leaving unrelated history intact.
- **Manual document recovery** (`tb recover-trca-documents`, #259): Opt-in command using anonymous Chromium (1s spacing, 64 MiB / 60s cap) to capture reviewed public FileStream links from `scrapers/toronto_bids/data/trca_document_gaps.json`:

  ```sh
  TB_DATA_DIR=/path/to/data uv run tb recover-trca-documents
  TB_DATA_DIR=/path/to/data uv run tb recover-trca-documents --only 20812
  ```

  Validates `%PDF-` bytes, `pdfinfo` structure, and SHA-256 hashes, keeping alternatives under their own URLs while leaving missing originals queued (`download:trca` exits non-zero while any original stays unheld). Does not invoke extraction. To add a new alternative to `trca_document_gaps.json`, verify the original agenda item, the calendar link, and the PDF text/hash.

### TRCA historical archive (`tb capture-trca-history`, #224)

Standalone command (not in nightly, no extraction or publication) that archives the public Laserfiche Minute Books series and all pre-2019 folders over rate-limited anonymous HTTP.

- Native PDFs download directly; scanned records use Laserfiche's single-document export and poll for completion.
- Indexed as `background_pdf.kind = 'trca_history'` so whole historical books stay outside paid extraction corpora.
- Files are stored by SHA-256 under `TRCA_HISTORY_DIR`; `sha256 IS NULL` is the retry queue. `--resume` retries pending files without rediscovery and preserves prior incomplete-discovery errors until a full walk succeeds.
- `inventory.json` records source IDs, names, paths, extensions, and non-PDF exclusions; `capture.json` records counts and failures. Any pending file or unresolved error produces a non-zero exit code.

## bids&tenders portals (`sources/bids_tenders.py`)

Captures public listing grids for TRCA and Toronto Zoo over plain HTTP using a session cookie and antiforgery token.

- Match the portal UI parameters: empty `status` means All, named strings filter statuses, and `sort=DateClosing DESC,Id` stabilizes paging (#226). Numeric status codes silently return empty results and are rejected.
- Missing or malformed fields, changing totals, duplicate IDs, and incomplete pages fail immediately.
- Non-empty responses are saved before parsing (including partial fetches), preserving earlier versions.
- Zoo preserves published title-prefix identifiers (including numeric, dotted, and spaced forms); unrecognized layouts stay unlabelled while still capturing the page.
- Each portal page is stored in `portal_listing` keyed by `(buyer, page URL)`, with nullable, non-unique printed references (#124). Board records stay separate unless independent evidence links them.
- Refreshes update non-null status, dates, title, and reference while keeping IDs, `first_seen`, and prior raw snapshots (#140). Missing fields keep existing values, and absence from a fetch never closes or deletes a listing.
- Store named statuses verbatim; an `Awarded` badge never creates a supplier or dollar award row.
- Fetching requires a recorded permission grant and enabled config, honoring rate limits and the Zoo's off-peak schedule (see [permissions/](permissions/)). Bid documents are out of scope. Export attribution comes from portal config (#228).
