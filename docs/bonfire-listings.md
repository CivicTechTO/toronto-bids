# Public Bonfire listings (#110, #134)

`tb capture-bonfire-listings` captures enabled Bonfire buyers; `--only toronto-public-library` selects the Library. The same adapter runs in the isolated `bonfire listings` nightly step. Configuration is in `BONFIRE_PORTALS`, with each enabled buyer's source and [recorded permission](permissions/2026-10-22-bonfire.md). The initial buyer is Toronto Public Library; other Bonfire buyers can use the adapter after checking their actual portal behavior and adding their buyer/configuration. No platform-wide crawl is performed.

## Source correction

The original #110 premise—that the Library's opportunities were on its own site—was stale. The [old URL](https://www.torontopubliclibrary.ca/about-the-library/business-with-the-library/) redirects to [tpl.ca](https://tpl.ca/about-the-library/business-with-the-library/), whose Current procurement requests section links [the Library's Bonfire portal](https://torontopubliclibrary.bonfirehub.ca/portal/?tab=openOpportunities). The inspected Library page contains a pointer, not procurement listings or a local award register. The [Purchasing Policy](https://tpl.ca/policies-and-terms-of-use/purchasing-policy/) establishes procurement/reporting responsibilities but is not itself a list of awards.

The public portal's own JavaScript identifies the two anonymous GETs used by its Open and Past tabs:

- `/PublicPortal/getOpenPublicOpportunitiesSectionData`
- `/PublicPortal/getPastPublicOpportunitiesSectionData`

These return complete project collections in the captured UI, rather than server-paginated DataTables pages. `BFUtil.loadSection` calls them with GET. The adapter validates the response success flag (the actual response uses integer `1`), project identity map, required listing fields, public visibility, dates and demonstrated workflow codes. Unknown collection/envelope fields fail for review so newly introduced pagination cannot be silently ignored. Explicit empty collections are legitimate; a missing collection, login HTML, redirect or `success=false` is a failure. New source layouts require source evidence and fixture updates.

## Access and backoff

A successful capture makes three serial GETs: landing page, Open collection, Past collection. Each request, including each retry, waits at least one second. The adapter uses an identifying project User-Agent. Transport failures, 429s and server errors have at most three retries with exponential delays of 2, 4 and 8 seconds. `Retry-After` seconds and HTTP dates can extend those waits; instructions above 60 seconds defer the capture with a visible failure instead of retrying early. Other client errors and redirects stop immediately. Deferred server deadlines are persisted per portal in `retry-not-before.txt`; subsequent commands/nightly runs make no request before that deadline. The current failure is never labelled an empty success.

The maintainer explicitly confirmed Bonfire permission with respectful access/backoff. The inspected robots.txt generally disallows automated access; the explicit permission is the basis for this capture, as recorded separately. The implementation neither registers an account nor downloads project documents, private/invited opportunities or bidder submissions.

## Archive and interpretation

Original response bytes and safe request provenance (URL, response status, capture timestamp, SHA-256, Content-Type and Retry-After) are retained under `<DATA_DIR>/agencies/bonfire/<buyer-slug>/` before parsing, including empty/error replies. Raw bodies use content-addressed `.bin` files; JSON manifests identify their request context. Interrupted writes cannot truncate an existing content-addressed response. No cookies or authentication headers are written to manifests.

Every public project becomes a `portal_listing`, keyed by buyer and `/opportunities/<ProjectID>`. Printed `ReferenceID` values are optional, nonunique source labels. They are not normalized into contract identity or joined to board reports. Repeated references remain separate public pages. All normalization completes before listing writes; a malformed later record/collection cannot publish a partial batch. A successful refresh updates non-null metadata while preserving the same listing/procurement IDs and first-seen timestamps. Missing fields preserve held values; absent pages are retained without inferring cancellation or deletion.

The portal's JavaScript interprets `DateClose` as UTC, so exports include an explicit `+00:00` offset. No opening/posting date is invented. Named statuses follow the anonymous UI's Open/Closed/Awarded/Cancelled rules, rather than mislabelling internal workflow codes such as Evaluating or Archived. Cancelled listings retain their published scheduled close even though the UI hides that date. For reopened projects, the original anonymous closing date must be unambiguous in the supplied prior-open-period evidence; otherwise capture fails. A visible Awarded badge never creates a supplier/value award fact.

Normalized listings appear in `buyers[].portal_listings`, in the shared `portal_listing` CSV/Parquet table and generated schema, with source `bonfire`. Source attribution is included in export metadata. City solicitation/award totals retain their meaning. Each buyer's failure reaches standalone exit status and nightly step/history; supplier rebuild and export still run after a failed Bonfire step.
