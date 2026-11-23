# TTC Board historical capture

`tb capture-ttc-history` archives requested years from the TTC's 2000-2024 public Board archive and downloads every PDF linked from those meeting pages. It reads search parameters directly from the published Sitecore SXA page rather than hardcoding GUIDs or URL slugs. This command runs standalone (not in `tb nightly`) and does not invoke LLM extraction or publication.

## Commands

Run from `scrapers/` with `TB_DATA_DIR` set:

```sh
uv run tb capture-ttc-history --year 2018 --discover-only
uv run tb capture-ttc-history --year 2018 --max-requests 1000
uv run tb capture-ttc-history --year 2018 --resume --max-requests 1000
uv run tb capture-ttc-history --from-year 2017 --to-year 2018 --resume
```

- `--max-requests` covers the entire run, including retries and all years in a range.
- Every request waits at least 5.2s with bounded backoff and `Retry-After` handling. Denials, transport errors, or an exhausted budget stop network work; a 404 on an individual PDF is logged as a gap while other downloads continue.
- Do not run overlapping manual captures; coordinate on the data directory's `.pipeline.lock`.

## Discovery and storage

- Each year reconciles the full paginated year listing against the Board-filtered listing (checking counts, unique IDs/URLs, page sizes, and a fresh first-page check). Misfiled URL years are recorded as discrepancies only when the rendered date matches the requested year.
- Cancelled or rescheduled placeholders with the generic heading "Board Meeting" are accepted only with an explicit date in that year and a Start Time noting cancellation/rescheduling; they are recorded under `meeting_notices` in discovery reports.
- Files live under `<DATA_DIR>/agencies/ttc_history/`:
  - `YEAR/census.json`: Checked source population and meeting IDs.
  - `YEAR/inventory.json`: Every PDF link grouped by physical URL (ignoring `#fragment` differences while keeping query/revision params), with parent meeting/item, anchor text, roles, and topic hints.
  - `YEAR/checkpoint.json`: SHA-256 pointers to held meeting HTML and PDFs.
  - `YEAR/capture.json` and root `capture.json`: Scope, held/pending counts, textless PDFs, source changes, failures, and unattempted years.
  - `capture-<digest>.json`: Per-attempt reports alongside content-addressed responses, HTML, and PDFs.
- `--resume` hash-checks held files, repairs missing/corrupt artifacts, refreshes live meeting discovery, and reuses held meeting HTML snapshots. Omit `--resume` to refresh meeting pages and document versions. Previously archived files are never deleted.
- Downloaded PDFs must have a valid PDF header/EOF and pass `pdftotext`. Valid scanned PDFs without text are kept and reported as textless. External non-TTC origins stay listed as unrequested gaps.

## Archival roles and promoting reports to extraction

All linked PDFs are archived and indexed outside paid extraction corpora using `background_pdf.kind` values: `ttc_archive_report`, `ttc_archive_decision`, `ttc_archive_agenda`, `ttc_archive_minutes`, or `ttc_archive_document`. Previously reviewed documents keep their existing DB bytes and kind; content changes are flagged for review.

To promote reviewed procurement reports into the `ttc` extraction corpus (`kind = 'ttc_board'`) without re-downloading, create a JSON array pinning each URL, review note, and exact SHA-256:

```json
[
  {
    "url": "https://cdn.ttc.ca/published-report.pdf",
    "basis": "Reviewed public procurement report; recommendation is not approval",
    "sha256": "FULL_64_CHARACTER_LOWERCASE_SHA256"
  }
]
```

Then promote, extract, and inspect coverage:

```sh
uv run tb capture-ttc-reports --inventory reviewed-ttc-reports.json
uv run tb extract --corpus ttc --batch
uv run tb agency-coverage --only ttc
```

- Changed bytes fail hash verification. Running `tb capture-ttc-reports` with no arguments uses the default seed list in `data/ttc_reports.json`.
- **Bid and award rules**: `*_bid_facts` counts all named bidder and non-bid rows; `*_no_bid_declarations` counts `no_bid` rows separately; `*_submitted_bid_facts` excludes them. Firms listed only under `prequalified` or `invited_to_tender` do not become `bid` rows. Unknown prices stay `NULL`. Staff award recommendations stay withheld until matched against an independent Board decision, and no MERX aliases are inferred.

## Reviewed identities and withheld bid metadata

### Procurement identities

Administrative footer codes are not procurement identifiers. Entries in `data/agency_identity_evidence.json` can bind a TTC extraction to a printed solicitation/contract number, or use `report_url_fallback` (the exact PDF URL) when a single-contract report has no public call number.

Each entry pins the buyer, PDF hash, extraction version, full contract fingerprint, report URL, and contract count. Changed contexts abort the TTC rebuild before writing. Multi-contract reports without references stay withheld.

### Withholding unsupported bid fields

When a model extracts the right bidder name and price but adds an unsupported HST basis or compliance status, a fully bound TTC evidence entry can include `withheld_bid_fields` (listing `bid_index`, `field`, `expected_value`, and `reason`):

- Allowed fields: `amount_basis` and affirmative `compliant`/`non_compliant` statuses only. The derived value is set to `NULL`.
- Cannot alter prices, names, ranks, bidder presence, or `no_bid`/withdrawal statuses.
- Generic "taxes included" wording does not prove HST specifically, and failing a qualitative score does not prove commercial non-compliance. Keep supported metadata intact; do not wipe all bidders because one claim is unsupported.
- Raw `extraction_cache` and `contract_json` stay unchanged. `identity_evidence_json` records the suppression proof, and `tb agency-coverage --only ttc` reports `withheld_bid_metadata_fields` separately. Mismatched guards abort before swapping derived rows.

### Decision-verified award admissions (#341)

Staff award recommendations in `ttc_board` stay withheld by default unless an entry in `data/agency_identity_evidence.json` includes a source-bound `award_review` citing an independently held `ttc_archive_decision` PDF (`role = "decision"` with explicit ISO `decision_date`). `agency_claim_review.admit_claims` verifies every cited PDF's SHA-256 and verbatim passages against `background_pdf`, requires explicit Board `adopted`/`approved`/`carried` wording (rejecting amended, deferred, rejected, or received-for-information outcomes), and checks that the decision binds the contract reference, supplier (or report-defined short name), and dollar value before admitting rows into `agency_award`. Uncorroborated `2000–2013` recommendations (where `ttc.ca` published no per-item decision sheets), `2018-04-11` misfiled reports, `Notice of Award` items received for information, amended recommendations (e.g., `P32PL17757`), and decisions omitting dollar amounts (e.g., `M60-88`) remain explicitly withheld.

## Regression fixtures

Offline tests replay a held 2017 archive snapshot (52 all-type records, 13 Board meetings, 472 PDF link observations across 440 unique URLs) along with multi-page cohorts, drift, denied/missing files, corrupt checkpoints, resume, request budgets, role preservation, hash-pinned admission, and no-bid accounting.
