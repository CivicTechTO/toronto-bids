# Shared TCHC/TSHC Bonfire capture (#107)

Toronto Community Housing's [current vendor page](https://torontohousing.ca/partner-tchc/procurement-opportunities/become-vendor) says all competitive opportunities are posted on Bonfire and links directly to [the shared public portal](https://torontohousing.bonfirehub.ca/portal/?tab=openOpportunities). The public portal title is “Toronto Community Housing and Toronto Seniors Housing”. Its department metadata includes TCHC, TSHC and joint departments.

## Implementation

Enable one `toronto-community-housing` buyer profile, named for both corporations, in the existing Bonfire adapter. This profile groups the shared portal; it does not attribute every posting to TCHC alone. The same buyer-scoped public-page identities, held responses, three serial requests, pacing, bounded backoff and persisted Retry-After deadline apply. The supplied Bonfire permission and this portal's inclusion are recorded in `docs/permissions/2026-10-22-bonfire.md`.

No adapter changes, schema migration, new extraction path or model requests are needed. The existing nightly and standalone `tb capture-bonfire-listings --only toronto-community-housing` select the new profile. Source attribution names both corporations. Closed and Awarded portal labels remain listing metadata, without inventing supplier/value award facts.

The issue's older Biddingo premise is superseded for current competitive opportunities by TCHC's explicit current direction to Bonfire. The inspected current procurement/vendor pages expose no Biddingo link. This work does not establish completeness of historical Biddingo records or procurement before the earliest captured posting. Documents and submissions requiring registration are outside this anonymous listing capture.

## Source-backed checks

The unchanged anonymous landing/Open/Past response bodies captured on **2026-10-23 at 16:29 UTC** are committed as fixtures, with source URLs and hashes in their README. They contain **961 distinct public opportunity pages**: 16 Open, 629 Awarded, 226 Closed and 90 Cancelled. Reused references remain separate postings; `RFP 25270-PP` occurs on six distinct pages with six distinct procurement identities. No private route identifiers are followed.

A fresh live capture at **2026-10-24T02:30:53.978625+00:00**, using the configured profile and unchanged adapter in an isolated production-archive snapshot, held **963 postings**:

| Public status | Postings |
| --- | ---: |
| Open | 17 |
| Awarded | 629 |
| Closed | 227 |
| Cancelled | 90 |

The change from the fixture is source evolution, not a parser count discrepancy. Earliest captured close is **2019-05-21T18:00:00+00:00**. Every page has a distinct procurement identity. Replaying the three held responses without network access preserves all listing facts, row IDs, procurement identities and first-seen dates.

Conservation checks compare full existing rows by their actual primary keys: 7,568 City solicitations, 15,557 City award lines, 19,009 bids, 611 agency solicitations, 1,012 agency awards, 1,458 agency bids, 508 published award claims, 510 original publication entries and 783 existing portal listings were unchanged. SQLite integrity and foreign-key checks pass. Production was read only; no new data was published.

## Export and frontend

The isolated JSON export's SHA-256 is `40d09fd4d3edb54a51b9972a0d225556af87b718e3bb641ec9a7f6b3ee4dfe48`. The already merged frontend successfully builds against it; the full internal-link crawl and data-independent checks for every buyer's portal/publication records pass. The new joint buyer page displays all 963 postings without frontend changes.

The nightly regression replays both the Library and shared-housing sources, checking the successful step history and all **1,373 fixture listings** in JSON, Parquet and the CSV archive. The full offline scraper suite passes **1,251 tests**.

Local evidence, source response manifests, archive snapshot, export, conservation report and validation/replay scripts are retained under `/home/alex/tb-data/operations/107/`. Full frontend build evidence is held in the frontend workbench's `.data/housing-build.log`. The production checkout remains on merged `main`; this profile is not deployed until its PR is merged and the checkout updated.
