# Public Bonfire listings (#110, #134)

`tb capture-bonfire-listings` captures public opportunity metadata for enabled Bonfire buyers (`--only toronto-public-library` runs a single buyer). It also runs as the isolated `bonfire listings` step in `tb nightly`.

Enabled buyers are configured in `BONFIRE_PORTALS` (with written permission recorded in [permissions/2026-10-22-bonfire.md](permissions/2026-10-22-bonfire.md)):

- Toronto Public Library (`tpl.ca` links to `torontopubliclibrary.bonfirehub.ca`)
- Shared TCHC / TSHC portal (`torontohousing.bonfirehub.ca`)
- Waterfront Toronto (`waterfrontoronto.bonfirehub.ca`)
- Exhibition Place (`explace.bonfirehub.ca`)
- The Atmospheric Fund (`taf.bonfirehub.ca`)

Other Bonfire buyers can be added after checking their live portal responses and updating config and fixtures.

## Endpoints and validation

Bonfire's public portal JS (`BFUtil.loadSection`) loads complete Open and Past collections with two anonymous GET requests (no server-side pagination):

- `/PublicPortal/getOpenPublicOpportunitiesSectionData`
- `/PublicPortal/getPastPublicOpportunitiesSectionData`

The adapter checks `success == 1`, the project map, required fields, public visibility, dates, and known workflow codes. Unknown envelope fields fail validation so newly introduced pagination cannot be missed silently. Empty collections are valid; missing collections, login HTML, redirects, or `success = false` fail the capture.

## Rate limits and backoff

- Each buyer capture makes three serial GET requests (landing page, Open, Past) with at least 1 second between requests (including retries) and an identifying project `User-Agent`.
- Transport errors, 429s, and 5xx errors retry up to 3 times with 2s, 4s, and 8s backoff, extended by `Retry-After` if present. Delays over 60 seconds abort with a failure and persist the deadline in `retry-not-before.txt` so subsequent runs wait. Other 4xx errors and redirects stop immediately.
- Although `robots.txt` generally disallows bots, access is covered by explicit permission. The adapter never logs in or downloads bid documents, private opportunities, or submissions.

## Storage, interpretation, and export

- **Raw archives**: Original bytes (`.bin`, content-addressed by SHA-256) and request manifests (URL, status, timestamp, SHA-256, `Content-Type`, `Retry-After`, no cookies/auth) are written to `<DATA_DIR>/agencies/bonfire/<buyer-slug>/` before parsing, including error responses.
- **Database**: Each public project becomes a `portal_listing` keyed by `(buyer, /opportunities/<ProjectID>)`. Printed `ReferenceID` values are optional, non-unique labels and are not joined to board reports. All records are validated before any DB write. Refreshes update non-null fields while keeping IDs, `first_seen`, and prior values for missing fields; absent listings are never deleted or closed.
- **Dates and statuses**: `DateClose` is interpreted as UTC (`+00:00`), matching the portal's JS. No posting date is invented. Statuses map to the portal UI's public labels (`Open`, `Closed`, `Awarded`, `Cancelled`) rather than raw internal codes. Cancelled listings keep their scheduled close date; reopened listings require unambiguous prior-open-period evidence. An `Awarded` badge never creates a supplier or dollar award row.
- **Exports**: Published under `buyers[].portal_listings` in JSON and in `portal_listing` for CSV/Parquet (with `source = 'bonfire'` and source attribution). Per-buyer failures surface in exit codes and `sync_run` without blocking supplier rebuild or export.
