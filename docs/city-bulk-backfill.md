# City committee and Council bulk discovery and backfill

Unlike `enrich-committee-awards` (which only follows voting-record items whose titles contain a document number), this job indexes full meeting agendas across terms, downloads linked staff-report PDFs, screens them for bid participation language, and queues them for the shared Claude extraction pipeline.

## Indexing agendas and downloading reports

Run from `scrapers/`, using a durable job directory outside the export directory. The archive database is opened read-only during discovery, screening, and queue preparation:

```sh
uv run python -m toronto_bids.city_bulk run \
  --directory /path/to/city-bulk-job \
  --database /path/to/bids.sqlite
```

- **Schedule inventory**: On the first run, resolves CSV resources from the City's CKAN meeting-schedule package, pins their bytes and hashes, and deduplicates multi-day meetings by `(term, body, number)`. Defaults to 2010 through the current year for City Council, Executive, Government Management/General Government, and Public Works/Infrastructure committees. Use `sources.city_agendas seed --from-year ... --through-year ...` in a new job directory for a different range.
- **Validation**: Each agenda page is verified against its published date, body, and meeting number; zero parsed headings fails validation, and cancelled meetings must still identify the scheduled meeting. Future, pending, held, cancelled, and failed states are tracked separately. Missing or altered local files fail resume verification.
- **Pacing and limits**: Agenda scraping uses one headed Chromium browser under Xvfb; PDF downloads use serial paced HTTP with retries and `Retry-After` backoff. Maintenance pages, 403/429s, and long delays pause that lane with persisted backoff. Pass `--meeting-limit` and `--report-limit` to bound a pass; each completed source is checkpointed. Separate locks guard the agenda and PDF lanes.

## Queuing and collecting extraction

```sh
uv run python -m toronto_bids.city_bulk run \
  --directory /path/to/city-bulk-job \
  --database /path/to/bids.sqlite \
  --queue --submit --limit 50 \
  --budget-database /installation/data/llm-usage.sqlite
```

- Point `--budget-database` at the installation's shared `llm-usage.sqlite` so all spending shares one queue and monthly cap.
- Preparation deduplicates PDF hashes, reuses existing cache entries, and filters out maps or documents without participation language.
- By default, only reports whose printed/agenda Doc number or proven historical Call Number (`NativeCallIdentities`, matching primary report subject and agenda title with no conflicting footers) targets an awarded City solicitation currently missing bids are sent to Claude. Pass `--include-unlocated` only for bounded research cohorts.
- Collect results through the normal operational command against the authoritative database (holding `.pipeline.lock`):

```sh
uv run tb extraction-batches collect
```

Do not collect the shared queue directly into a disposable copy, because acknowledging a batch before writing to the authoritative cache drops the handoff. Copy validated cache entries to a validation database afterward.

## Admission and coverage measurement

```sh
uv run python -m toronto_bids.city_bulk inventory \
  --directory /path/to/city-bulk-job --database /path/to/validation/bids.sqlite
uv run python -m toronto_bids.city_bulk backfill \
  --directory /path/to/city-bulk-job --database /path/to/validation/bids.sqlite
uv run python -m toronto_bids.bid_coverage \
  --database /path/to/validation/bids.sqlite --out /path/to/coverage-after
```

### Automated admission rules

- **Modern Doc identities**: Model contract and published source roles must agree. The agenda title can identify the event; if it names only a native call or project, a unique printed report subject must supply the Doc number. Multi-event reports also require a local Doc number near the submission list and consistent footers.
- **Received-bid tables and lists**:
  - Accepts explicit headings (including counted headings like `Summary of two (2) Bids Received`).
  - `Suppliers and Fixed Bid Prices` tables require an immediately preceding counted statement referencing that table number and a heading naming the current Doc.
  - Original table receipt counts cannot be smaller than the number of admitted bidders (even if the model omitted its count). For native received lists, lead counts also bound admitted names independently of model output (independent lead-count checks for older modern received lists are deferred to preserve existing proofs).
  - Final tender-opening tables can prove participation even when the procurement included prequalification, provided the text explicitly states bids were opened (or uses a received-bid table) and each model party appears as a full table row whose immediately following non-empty line carries its dollar value or `No Bid`. `No Bid` rows stay in evidence and are excluded from participation. Prequalification, invitation lists, and sole-winner mentions never establish submitted bids.
- **Historical Call Numbers**: Require an independent archived-award proof (exact printed award date, full supplier name, and amount uniquely matching the frozen and current populations) plus matching agenda and report-subject Call Numbers. Never convert Call Numbers numerically into Doc numbers.
- **Output shape**: Derived rows store unpriced participation (`source = 'city_bulk'`, exact document keys, no price or compliance status). One coherent named list is chosen per event (largest supported list, then deterministic URL/contract tie-break) and pinned on resume rather than merging differently named lists across supplements.

### Verification and export

- Before writing, backfill rechecks PDF bytes, native text, agenda bytes, parsed item text, and the attachment link. Missing or changed evidence aborts the run.
- Accepted proofs are stored in `city_bulk_bid_evidence`. Replaying preserves bid IDs and `first_seen`. The CLI runs canonical resolution and supplier rebuild in the same transaction.
- JSON exports attach each approved report directly to its proven City solicitation via `city_bulk_bid_evidence` (even when the PDF has no council reference) without spreading a multi-contract report across sibling solicitations.
- `extraction-inventory.json` records screening, contracts, proposals, and exceptions; `run.json` records phase counts.
- Measure impact by the reduction in **awarded City solicitations without bids** plus canonical attribution parity, keeping baseline, merged-but-unpublished, and new recovery counts separate.

## Historical source notes

- **Dead ends (do not re-scrape):** Retired CKAN call/award packages point to offline Lotus Notes (`wx.toronto.ca`) endpoints with no active datastore. The City's MERX mirror (`merx.com/cityoftoronto`) contains only 3 closed notices, and its 2 bid-result tabs hold QA test suppliers (`5.1.0 QA Test Supplier`).
- **Remaining bulk routes:** Archived Lotus Notes award views survive on the Wayback Machine (`wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView`, HST-exclusive award amounts) and power `archived_publisher_award` crosswalks (see [city-call-crosswalk.md](city-call-crosswalk.md)). Beyond public web archives, the City's PMMD Routine Disclosure Plan provides an official request route for older solicitation results.
