# TTC Board historical capture

`tb capture-ttc-history` reconciles explicitly requested years in the TTC site's
2000–2024 Board archive and preserves every PDF linked from the captured meeting
pages. It reproduces the public archive's observed Sitecore SXA requests. Search
configuration comes from the published page, rather than fixed scope GUIDs or
inferred meeting slugs. This command is separate from the nightly and does not
invoke model extraction, rebuild bidder facts, or publish data.

From `scrapers/`, with `TB_DATA_DIR` pointing at the intended archive:

```sh
uv run tb capture-ttc-history --year 2018 --discover-only
uv run tb capture-ttc-history --year 2018 --max-requests 1000
uv run tb capture-ttc-history --year 2018 --resume --max-requests 1000
uv run tb capture-ttc-history --from-year 2017 --to-year 2018 --resume
```

The request budget covers the entire invocation, including retries and both years
of a range. Every request waits at least 5.2 seconds. The shared public reader uses
bounded backoff and Retry-After deadlines; a denial, exhausted budget or transport
failure stops further network work. A missing PDF (404) remains a visible gap while
other independent links can be captured. Do not run overlapping manual capture
jobs: production operators use the existing data-directory pipeline lock.

## Discovery and retention

Each year reconciles the complete, paginated year cohort against the independently
filtered Board cohort. Counts, unique source IDs/URLs, page lengths and a fresh
first-page anchor must agree. The Board cohort can span multiple pages. Source
URLs and dates are preserved: a misfiled URL-year is retained as a discrepancy only
when the publisher's rendered result date supports the requested year. The year
cohort is not permission to fetch other meeting types. The actual Board meeting
page must identify the requested year.

Cancelled/rescheduled placeholders sometimes use the generic heading "Board
Meeting". They are accepted only with an explicit, unambiguous published Date
in the selected year and a Start Time field stating cancellation or
rescheduling. Their literal date/status and URL appear in `meeting_notices` in
the discovery report. They remain part of the source census; neither their URL
nor missing PDF links is treated as evidence that a meeting took place.

The source provides no transactional snapshot. Coverage describes the dated public
search cohort and links in held meeting pages, not every meeting or procurement
that ever existed. Zero results, source changes and missing bytes are distinct.

Under `<DATA_DIR>/agencies/ttc_history/`:

- `YEAR/census.json` records the checked source population and meeting IDs.
- `YEAR/inventory.json` retains every PDF link observation, parent meeting/item,
  anchor text, roles and topic hints, grouped by physical source URL. Fragment-only
  differences do not create extra downloads; source query/revision parameters stay.
- `YEAR/checkpoint.json` points to held meeting/PDF bytes by SHA-256.
- `YEAR/capture.json` and root `capture.json` expose requested scope, held/pending
  files, textless PDFs, reviewed-source changes, failures and unattempted years.
- `capture-<digest>.json` retains immutable per-attempt reports. Source responses,
  meeting HTML and PDF versions are retained by content hash.

Resume hash-checks held bytes, repairs missing/corrupt artifacts and always refreshes
live meeting discovery. It reuses held meeting-page snapshots, with their original
capture timestamps. Run without `--resume` to refresh those pages and document
versions. Previously observed files are never erased because a later source omits
them. Completeness uses the current requested cohort, not accumulated DB totals.

PDF bytes must have a valid header/EOF and pass `pdftotext`. A valid image-only PDF
is retained and reported as textless; capture success does not imply extraction
readiness. Unapproved external origins remain inventoried as unrequested gaps.

## Roles and extraction admission

All linked PDFs are archived, including decisions, agendas, minutes, amendments,
settlements, policy and recognition material. Link roles and heading topic hints
are provenance, not procurement facts. A recognition award is not a contract award;
a contract amendment is not an additional original award. Contradictory or unknown
roles remain explicit rather than causing a document to disappear.

New PDFs are indexed as `ttc_archive_report`, `ttc_archive_decision`,
`ttc_archive_agenda`, `ttc_archive_minutes` or `ttc_archive_document`, outside paid
extraction corpora. Previously reviewed documents retain their existing DB bytes
and kind; changed observed content is reported for review rather than silently
replacing the extraction input.

Review report contents before admitting them. Prepare a JSON array with an actual
published TTC PDF URL, a review basis and the SHA-256 of the reviewed bytes:

```json
[
  {
    "url": "https://cdn.ttc.ca/published-report.pdf",
    "basis": "Reviewed public procurement report; recommendation is not approval",
    "sha256": "FULL_64_CHARACTER_LOWERCASE_SHA256"
  }
]
```

```sh
uv run tb capture-ttc-reports --inventory reviewed-ttc-reports.json
uv run tb extract --corpus ttc --batch
uv run tb agency-coverage --only ttc
```

The reviewed inventory promotes matching held archive reports to `ttc_board`
without another download. Changed bytes fail hash admission and cannot replace
reviewed source data. The no-argument report command retains its reviewed seed
inventory. Normal batch budgets, cache admission and publication handling apply;
manual extraction review should use the established quarantined workflow before
publishing unfamiliar report types.

The shared prompt and cache version are unchanged. No-bid policy remains explicit:
`*_bid_facts` includes named bidder/non-bid observations; `*_no_bid_declarations`
counts extracted `no_bid` rows separately and `*_submitted_bid_facts` excludes them.
These are extracted-fact counts, not proof that every declaration or submission was
captured. Names appearing only in `prequalified` or `invited_to_tender` do not become
bid rows. Unknown prices remain null. TTC staff recommendations remain withheld
from award rows until independent decision binding is implemented; no MERX alias
is inferred from matching names, titles, digits or amounts.

## Reviewed procurement identities

Administrative footer codes are not procurement identifiers. The shared identity
registry can bind a TTC extraction to a source-labelled solicitation or subject
contract number. Where a reviewed report has one procurement collection and no
public call identifier, `report_url_fallback` uses its exact held PDF URL as a
report-specific identity. The registry retains the footer's administrative role;
the URL is not presented as a printed call number or an alias of that footer.

Each TTC decision binds the buyer, document hash, extraction version, entire
contract fingerprint, exact report URL and collection count. Changed contexts
abort the TTC rebuild before replacing existing facts. Alternative URLs are not
automatically equated, and reference-less multi-contract reports remain withheld.
These decisions do not change raw extraction results or approve award claims.

Deploy registry and resolver changes together before promoting reviewed archive
documents. Identity approval alone cannot admit unsupported prices, tax bases,
statuses, omitted responses or unresolved quote options. Source evidence and
regression results for the reviewed recovery are recorded in
[`ttc-identity-recovery-validation-2026-10-29.md`](ttc-identity-recovery-validation-2026-10-29.md).

## Reviewed bid metadata

A correct bidder name and submitted price can coexist with an unsupported model
claim about HST or commercial compliance. Source review may withhold those exact
fields from derived bidder observations. Add `withheld_bid_fields` to an otherwise
fully bound TTC evidence decision, recording each `bid_index`, `field`,
`expected_value` and source-based `reason`. Only `amount_basis` and affirmative
`compliant`/`non_compliant` status claims can be withheld, and the derived value is
always null. The mechanism cannot replace a value, change a price, name or rank,
remove a bidder, or erase an explicit `no_bid`/withdrawal.

Review the complete report, including appendices and final evaluation outcomes.
Generic tax inclusion does not prove an HST-specific basis, and failing a qualitative
score does not prove commercial noncompliance. Keep explicitly supported metadata;
do not clear every bidder because one claim is unsupported. Missing named
responses, unresolved price scopes, quote alternatives and identity ambiguity still
prevent admission. Non-exported model arrays and award recommendations are not
approved by bidder-only review; record any source limitations with the proof.

The raw extraction cache and ledger `contract_json` retain the original model
claims. `identity_evidence_json` records the suppression proof, and
`tb agency-coverage --only ttc` reports `withheld_bid_metadata_fields` separately
from bid-fact counts. Mismatched context or field guards abort before the derived
row swap, preserving earlier verified facts. Deploy evidence and resolver together,
then rehearse against a fresh production copy before admitting the held reports.
See [`ttc-metadata-recovery-validation-2026-10-29.md`](ttc-metadata-recovery-validation-2026-10-29.md)
for the source-bound replay and preservation checks.

## Regression evidence

The held 2017 source replay independently expects 52 all-type records, 13 Board
meetings, 472 PDF-link observations and 440 distinct PDF URLs. Tests also exercise
multi-page Board cohorts, source drift, scope validation, denied/missing files,
corrupt checkpoints, resume, request budgets, role preservation, reviewed hash
admission and no-bid accounting. This fixture benchmark does not assert complete
procurement-fact extraction for that year.
