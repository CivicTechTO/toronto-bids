# Ariba discovery search migration — October 20, 2026 (#235)

The nightly used a legacy search mechanism that no longer matches the public Discovery
website. The fix uses the website's current anonymous search and detail APIs. More retries
against the old endpoint are not the recovery.

## Evidence

All times below are UTC, on October 20, 2026.

- Production recorded `ariba_discovery` failed at 09:54:34–09:54:43, with zero rows fetched
  or upserted. The configured HTTP client makes five attempts. The last successful run was
  October 17 (zero matching Toronto postings); October 16 fetched three.
- At 12:39, a replay of the original POST to
  `https://service.ariba.com/Network/discoveryweb/search/public/v1/doIndexedSearch?siteName=Quote`
  returned five consecutive 503 responses in 8.45 seconds. Page sizes 10 and 100 also failed.
  Its generic HTML page mentions maintenance or capacity; that does not establish SAP's
  internal cause. A further replay during implementation still failed.
- The service homepage and a known posting's legacy detail URL returned 200. However, the
  legacy detail API returned 400 / `Event not found for Posting ID` for migrated posting
  `1110027713`, which exists in the current public website.
- A fresh anonymous headed Chromium session followed `https://discovery.ariba.com/` to
  the sign-in page, then its **Explore Leads** link to
  `https://portal.us.bn.cloud.ariba.com/dashboard/public/appext/comsapsbncdiscoveryui#/leads/search?anId=ANONYMOUS`.
  Search worked from the same host as the failing scraper.
- The website sends GET requests to
  `https://portal.us.bn.cloud.ariba.com/tpx/ingress/tps/discovery-core/public/rfx/v1/search`,
  with a base64-encoded JSON `filter` query parameter. It receives `records`,
  `totalNumberOfRecords`, and a `searchAfter` cursor. The old API returned `solarRecords`.
- Replaying the new request with plain HTTP and no cookies or credentials returned 415 until
  `Content-Type: application/json` was supplied; it then returned 200. No browser or session
  bootstrap is required. The scraper also supplies `Accept: application/json`.
- A free-text search for `City of Toronto` returned 603 matches and is not an exact buyer
  filter. The website's public JavaScript supports `orgAnid`. Using Toronto's public buyer
  ID `AN01050912625` returned 52 postings. Many migrated search records omit `customerName`.
  The new detail API confirms both `anid: AN01050912625` and `orgUnitName: City of Toronto`.
- Current details are served by
  `https://portal.us.bn.cloud.ariba.com/tpx/ingress/tps/discovery-core/odata/public/discovery/v1/rfx/RfxEvent('1110027713')`.
  The website expands `rfxEventSupCommodity,rfxEventTerritory,humanitarianAid`.
  The new fields include `rfxIdExternal`, `stateName`, `rfxType`, `orgUnitName`, and
  `lowerLimit_amount` / `upperLimit_amount` / `upperLimit_currency`.

This demonstrates a working replacement mechanism from the same network, including a new
detail contract. It argues against a general host/IP block or a requirement to log in.
It does **not** prove that the legacy endpoint has been permanently retired, nor establish
whether the legacy 503 is an outage or a policy decision on that route. No second authorized
network was available in this session; that comparison was not performed.

## Implementation and verification

The source now uses the observed anonymous GET contract with the exact organization filter.
It follows page numbers and returned cursors until all reported records have been consumed;
missing fields, premature empty pages, repeated IDs, and missing/nonadvancing cursors fail
visibly rather than becoming a successful zero-result run. Explicit non-Toronto search
records are excluded; a detail with a mismatched posting or buyer ID fails visibly.

Details are mapped from the new OData fields into the existing posting model, while the
original detail response is retained as the JSON snapshot. A failed detail request still
archives organization-scoped search metadata, and the existing NULL-preserving upserts keep
any earlier document bridge or snapshot. The new public detail response does not expose
`sourcingUrl`; an existing non-NULL stored URL is retained. No schema migration, login,
browser dependency on the sync path, or additional retry policy was introduced.

Regression coverage includes request method/headers/buyer filter, pagination, new detail
fields, missing buyer names, empty success versus malformed response, source-failure
isolation, and preservation of snapshots on detail failure.

A live run through `pipeline.run_source` at 12:51:39–12:52:05 used an **in-memory copy** of all
109 production posting rows. It finished `ok`, fetched/upserted 52 postings, and produced 152
archived rows. All 52 current postings had document-number bridges and detail snapshots.
The production database was opened read-only and was not updated by this verification.

Run offline checks from the repository root:

```sh
scrapers/.venv/bin/python -m pytest scrapers/tests/test_ariba.py scrapers/tests/test_pipeline_integration.py scrapers/tests/test_pipeline.py scrapers/tests/test_db.py -q
```

After deployment, a targeted recovery can use `tb sync --only ariba_discovery` with the
production data directory configured. That command also runs the usual linking passes; it
does not export or publish. Verify the new `sync_run` outcome, then use the normal export and
publication process if an immediate public refresh is required.

If the new API later fails, compare it with an anonymous browser search again before
changing headers, credentials, or endpoints. If the browser and plain HTTP both fail, collect
UTC timestamps, request method/URL, status, redacted response, and a posting ID, and contact
SAP Business Network support through the Help Center. Keep discovery marked failed and
preserve the archive while awaiting external recovery; a successful response with zero
records must include the expected search response structure and a zero total.
