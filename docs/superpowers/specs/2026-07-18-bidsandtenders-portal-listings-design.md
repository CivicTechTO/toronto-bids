# bids&tenders portal listing capture (#135) — design

2026-07-18. The remaining half of #135: now that TRCA and the Zoo have granted written
permission (recorded in `docs/permissions/2026-07-18-{trca,toronto-zoo}.md`, gates flipped
`enabled: True` in `config.BIDS_TENDERS_PORTALS`), capture the public listing metadata from
their bids&tenders portals. The board-report capture (merged in #136) already gives awards and
bidders; this adds the solicitation *listing* side — status, dates, categories — for the same
bodies, and keeps it current as open bids come and go.

## The finding that shapes it

The portal is a JS single-page app, but its grid loads from a plain-HTTP JSON endpoint — **no
browser required** (unlike the council/Ariba scrapers). Probed live 2026-07-18 and fully solved:

- `GET /Module/Tenders/en` returns the landing HTML carrying a `NodeId` (a per-portal GUID) and,
  in `#bidDetailAntiForgery` / hidden fields, `__RequestVerificationToken` values plus a paired
  antiforgery **cookie** set on the response.
- `POST /Module/Tenders/en/Tender/Search/<NodeId>?status=<n>&limit=<n>&start=<n>&dir=desc&from=&to=`
  with body `{keywords: "", __RequestVerificationToken: <token>}` and the session cookie returns
  `{"success": true, "data": [ …records… ], "total": <n>}`.
- **Two live gotchas, both load-bearing:** (1) use the **FIRST** `__RequestVerificationToken` on
  the page — the `#bidDetailAntiForgery`-scoped one 302s. (2) **Never send the `sort` query param**
  (`sort=ClosingDate desc,Id`) — its space/comma triggers an unhandled server error that
  redirects to `Error?aspxerrorpath=…`. Omit `sort` entirely; the default order is fine.
- The record schema is documented in the grid JS (`Module/Tenders/Resources/scriptsV2/home/index.js`):
  `Id` (→ detail URL `/Module/Tenders/en/Tender/Detail/<Id>`), `Title`, a closing-date field, and
  `Documents` / `Addendums` / `PlanTakers` counts, with a separate Awarded section.
- Both portals expose the same endpoint, differing only by host and `NodeId`
  (TRCA `950af760-…`, Zoo `d572c998-…`).

Because it is plain HTTP, it can join the unattended nightly path.

## The empty-portal reality (drives the phasing)

**As of 2026-07-18 both permitted portals are completely empty** — `total=0` for every status
(Open, Awarded, Cancelled, …) across a 2010–2027 date range; TRCA's homepage reads "no open bids."
This is consistent with the board-report finding that TRCA's real history lives on eSCRIBE (already
captured, #136) and both bodies adopted bids&tenders only recently. Consequences that shape the
build (decided with the maintainer — "option A: arm the infrastructure now"):

- There is **no live record to record a fixture from today**, so the parser cannot be validated
  against real data yet. Building a parser validated only against a hand-built fixture is the exact
  anti-pattern that produced three wrong parsers earlier in #135 — so **`parse_listing` is written
  against the JS-documented schema but treated as PROVISIONAL** until a real record validates it.
- The fetch is verified working (it correctly returns `total=0`), so we **arm the infrastructure
  now**: fetch + a raw-JSON **recording mode** + isolated nightly hook that safely no-ops on empty.
  The moment a bid appears, its raw JSON is captured to a fixture and `parse_listing` is completed
  and validated against it. Nothing is lost when bids appear.

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

### Impure half — `fetch_listings(http, portal) -> Iterator[dict]`

1. Re-raise `PermissionError` if `not portal["enabled"]` (unchanged from the stub — a future
   portal added without a recorded grant stays blocked).
2. **Establish a session** on a fresh `httpx.Client` (cookies must persist across the landing GET
   and the search POSTs): `GET {portal_url}Module/Tenders/en` following redirects; extract `NodeId`
   and the **first** `__RequestVerificationToken` from the returned HTML.
3. **Fetch each status** in `_STATUS_CODES` (provisionally `range(0, 6)` — the exact status→label
   mapping is recorded when data first appears). Query params `status,limit,start,dir,from,to`
   (**no `sort`**); body `{keywords: "", __RequestVerificationToken: <token>}`. Page with
   `limit`/`start` until `start >= total`, yielding every record. A `limit` cap per page (e.g. 50)
   keeps requests modest.
4. **Rate-limit**: a deliberate `time.sleep` between every HTTP request (a small constant, e.g.
   1–2 s), so a full sweep stays low-impact — the explicit condition both bodies set.
5. Attach `buyer_slug` and `status_code` to each yielded record so the pure half needs no
   external context. On an empty portal this yields nothing and is a clean no-op.

The session dance lives in `fetch_listings` on its own `httpx.Client`, not `HttpClient` — the
antiforgery cookie+token pairing is specific to this source and does not belong in the shared
client. Retry/backoff on transient errors is still valuable but the token/cookie extraction is
bespoke.

### Recording mode — `record_listings(portal, out_dir) -> int`

Writes each raw JSON record fetched to `out_dir/<slug>-<status>-<n>.json`. Run manually
(`tb enrich-agencies --portal --record`) so that the FIRST time a portal has data, we capture real
records to turn into parser fixtures — the step that unblocks completing `parse_listing`. Returns
the count written (0 while portals are empty).

### Pure half — `parse_listing(record, buyer_id, buyer_slug) -> AgencySolicitation` (PROVISIONAL)

Maps one JSON record to a model. Written against the JS-documented field names (`Id`, `Title`,
closing-date field, detail URL `/Module/Tenders/en/Tender/Detail/<Id>`); `native_ref` is the
portal's bid identifier, normalized by the same trim/uppercase/whitespace-collapse the
board-report path uses, so a portal row and a board-report row for the same procurement share a
key and COALESCE into one enriched row. Fields: `title`, `status` (mapped from `status_code`),
`posted_date`, `closing_date`, `portal_url` (built from `Id`), `source="bids_tenders"`.

**Marked PROVISIONAL in a module docstring**: it is tested against a hand-built record matching
the documented schema (enough to lock the mapping mechanics), but the exact JSON field names,
date formats, and status representation are unverified until a real record is captured via the
recording mode. Completing/validating it against the first real fixture is an explicit deferred
task, not part of this build. The `agency_award`-from-awarded path is **not built now** — deferred
until a real awarded record shows whether a supplier/value field exists.

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

- Pure `parse_listing` tested against a **hand-built record** matching the JS-documented schema
  (`tests/fixtures/agencies/bids_tenders_record_sample.json`) — enough to lock the field-mapping
  mechanics (native_ref normalization, detail-URL construction, status mapping, model shape). This
  fixture is explicitly synthetic and labelled so in `SOURCES.md`; the parser stays PROVISIONAL
  until a real record replaces it.
- `store_listings` tested against an in-memory DB: a portal row COALESCE-enriching an existing
  board-report row (same `native_ref`), and a portal-only row. Storage logic is deterministic, so
  these tests are valid regardless of the empty-portal reality.
- The session/fetch path stays untested by unit tests (nothing to record without a live call),
  exactly as the Ariba/council fetch halves are. A live `tb enrich-agencies --portal` run is the
  real-world verification; **today it correctly returns 0 rows**, which is the honest verification
  available now. When a portal has data, `--record` captures a real fixture and — per the hard
  lesson of #136 — the parser is re-validated with recall *measured* against the endpoint's `total`,
  not assumed.

## Deferred to first-real-data (an explicit follow-up task, NOT this build)

When a TRCA or Zoo bid first appears, `tb enrich-agencies --portal --record` captures real records;
then a follow-up completes and re-validates the parser:

- The exact `status` code → Open/Awarded/Cancelled mapping (probe each code's `total`).
- Whether the portal bid number matches the board-report `native_ref` format (decides how often
  the COALESCE enrich fires vs. a distinct row — either is correct; measured, not assumed).
- Whether awarded records expose a supplier/value (decides if the `agency_award` path is built).
- The precise JSON field names and date/status formats — replace the synthetic fixture with the
  real one and adjust `parse_listing`.

## Out of scope

- Bid documents (Vendor clickwrap).
- Other bids&tenders bodies (Ontario municipalities) — outside the archive.
- Any write/login/submission on the portal — read-only, anonymous, as the grants require.

## Recording

On completion, comment on #135 that the portal half landed; #109 (Zoo) and #132 (TRCA) get a
pointer. #135 can then close.
