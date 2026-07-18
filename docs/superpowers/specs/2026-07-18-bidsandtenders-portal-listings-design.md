# bids&tenders portal listing capture (#135) — design

2026-07-18. The remaining half of #135: now that TRCA and the Zoo have granted written
permission (recorded in `docs/permissions/2026-07-18-{trca,toronto-zoo}.md`, gates flipped
`enabled: True` in `config.BIDS_TENDERS_PORTALS`), capture the public listing metadata from
their bids&tenders portals. The board-report capture (merged in #136) already gives awards and
bidders; this adds the solicitation *listing* side — status, dates, categories — for the same
bodies, and keeps it current as open bids come and go.

## The finding that shapes it

The portal is a JS single-page app, but its grid loads from a plain-HTTP JSON endpoint — **no
browser required** (unlike the council/Ariba scrapers). Probed live 2026-07-18:

- `GET /Module/Tenders/en` returns the landing HTML carrying a `NodeId` (a per-portal GUID) and
  an ASP.NET `__RequestVerificationToken` (hidden field + a paired cookie set on the response).
- `POST /Module/Tenders/en/Tender/Search/<NodeId>?status=<n>&limit=<n>&start=<n>&dir=desc&from=&to=&sort=ClosingDate desc,Id`
  with body `{keywords: "", __RequestVerificationToken: <token>}` and the session cookie returns
  `{"success": true, "data": [ …records… ], "total": <n>}`.
- Without the session cookie + token the endpoint 302s to an error page. The pairing is standard
  ASP.NET antiforgery: the cookie set on the landing GET must accompany the hidden-field token.
- Both portals expose the same endpoint, differing only by host and `NodeId`
  (TRCA `950af760-…`, Zoo `d572c998-…`).

Because it is plain HTTP, it can join the unattended nightly path.

## Decisions (settled in brainstorming)

- **Scope: everything** — Open, Closing, Closed, and Awarded (every `status` code the endpoint
  accepts). The fullest archive. Kept polite despite the breadth (see rate-limiting).
- **Scheduling: nightly, off-peak**, folded into `tb nightly`, isolated like every other step;
  also runnable on demand. Matches the permissions' "nightly, off-peak, low-impact" wording and
  the "open bids vanish" rationale.
- **Storage: the existing `agency_solicitation`** keyspace, not a new table — the payoff of the
  shared buyer-keyed design (#135).
- **Awards: solicitation now; award only if cleanly present.** Write `agency_award` from a
  portal-awarded row *only if* the awarded JSON exposes a supplier/value field at fixture-record
  time. Do not half-build an award path on a maybe.
- **No bid documents, ever** — they sit behind the Vendor clickwrap; the grants cover listing
  metadata only.

## Architecture

Pure/impure split in `sources/bids_tenders.py` (replacing the current gate-only stub, whose
`PermissionError`-on-disabled behaviour is retained):

### Impure half — `fetch_listings(http, portal) -> Iterable[dict]`

1. Re-raise `PermissionError` if `not portal["enabled"]` (unchanged from the stub — a future
   portal added without a recorded grant stays blocked).
2. **Establish a session**: `GET {portal_url}Module/Tenders/en`, following redirects and
   accumulating cookies on the client; extract `NodeId` and `__RequestVerificationToken` from the
   returned HTML.
3. **Fetch each status** in `_STATUS_CODES` (the set the endpoint accepts — confirmed at fixture
   time; provisionally the codes behind Open/Closing/Closed/Awarded). For each, page with
   `limit`/`start` until `start >= total`, yielding every record. A `size` cap per page (e.g. 50)
   keeps requests modest.
4. **Rate-limit**: a deliberate `time.sleep` between every HTTP request (a small constant, e.g.
   1–2 s), so a full sweep stays low-impact — the explicit condition both bodies set.
5. Attach `buyer_slug` and `status_code` to each yielded record so the pure half needs no
   external context.

`HttpClient` gains a small `post_form(url, params, data)` helper if it lacks one (it has
`post_json`; the portal needs form-encoded body + query params + persisted cookies — httpx.Client
already persists cookies, so this is a thin addition, not a new client).

### Pure half — `parse_listing(record, buyer_id) -> AgencySolicitation` (and optional `AgencyAward`)

Maps one JSON record to a model. `native_ref` is the portal's bid number, normalized by the same
trim/uppercase/whitespace-collapse the board-report path uses, so a portal row and a board-report
row for the same procurement share a key and COALESCE into one enriched row. Fields:
`title`, `status` (mapped from the status code / the record's own status text), `posted_date`,
`closing_date`, `portal_url` (the record's own detail URL), `source="bids_tenders"`. Tested
against recorded JSON fixtures — no network, no browser.

### Store — `store_listings(conn, buyer_id, records) -> dict`

Upserts each parsed `AgencySolicitation` with `overwrite=True`: the portal owns the listing
fields (status/dates), so a nightly re-fetch keeps an open bid current, while COALESCE still
protects a board-report-supplied title from being nulled. If (and only if) an awarded record
carries a supplier/value, also upsert an `AgencyAward` (`source="bids_tenders"`), giving a second
source that cross-checks the board-report award parser. Returns counts.

### CLI / scheduling

- `tb enrich-agencies --portal` runs the capture on demand (per-body isolated: TRCA failing never
  stops Zoo, the `_cmd_enrich_agencies` pattern already in place).
- `tb nightly` gains an isolated portal step — a failure records to `sync_run` and never stops
  sync/export, exactly as the award-summary and sync steps are isolated today.

## Data flow

`fetch_listings` (session → paged JSON per status, rate-limited) → `parse_listing` (pure, per
record) → `store_listings` (COALESCE upsert into `agency_solicitation`, optional `agency_award`)
→ `build_supplier_dimension` picks up any new award suppliers → export's `buyers` section already
surfaces `agency_solicitation`/`agency_award`, so listings appear with no export change.

## Error handling

- Gate: `PermissionError` if a portal is disabled — unchanged.
- Session failure (landing GET fails, token/NodeId not found): raise a clear error naming the
  portal; the per-body isolation in the CLI/nightly records it and moves on.
- A single status/page failing (HTTP error) is logged and skipped so the rest of the sweep still
  lands, mirroring the board-report download's per-item resilience (#135 live-fix).
- The whole capture is per-body isolated; partial rows commit (archive semantics).

## Testing

- Pure `parse_listing` tests against recorded JSON fixtures under
  `tests/fixtures/agencies/` (real responses captured under the granted permission — the
  fixture-recording step the gate always anticipated). Cover: an open row, a closed row, an
  awarded row (with and, if it occurs, without a supplier field), and a row missing optional
  fields.
- `store_listings` tested against an in-memory DB: a portal row COALESCE-enriching an existing
  board-report row (same `native_ref`), and a portal-only row.
- The session/fetch path stays untested by unit tests (nothing to record without a live call),
  exactly as the Ariba/council fetch halves are; a live `tb enrich-agencies --portal` run is the
  real-world verification, and — per the hard lesson of #136 — its recall is *measured* against
  the live `total` the endpoint reports, not assumed.

## Open items resolved at fixture-record time (not blockers)

- The exact `status` code → Open/Closing/Closed/Awarded mapping (probe each code's `total`).
- Whether the portal bid number matches the board-report `native_ref` format (decides how often
  the COALESCE enrich fires vs. a distinct row — either is correct; measured, not assumed).
- Whether awarded records expose a supplier/value (decides if the `agency_award` path is built
  now or deferred).
- The precise JSON field names for title/dates/status/detail-URL.

## Out of scope

- Bid documents (Vendor clickwrap).
- Other bids&tenders bodies (Ontario municipalities) — outside the archive.
- Any write/login/submission on the portal — read-only, anonymous, as the grants require.

## Recording

On completion, comment on #135 that the portal half landed; #109 (Zoo) and #132 (TRCA) get a
pointer. #135 can then close.
