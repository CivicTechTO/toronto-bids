# Toronto Bids Project

The Toronto Bids Project archives public procurement data from the City of Toronto and its agencies. The City publishes solicitations and awards while they are open, but the data is fragmented and much of it becomes hard to reach once a bid closes. This project collects those records into a local SQLite database and exports them as public JSON, CSV, and Parquet datasets so the record stays accessible after closing dates pass.

## Quick start

All active code lives in [`scrapers/`](scrapers/), a `uv`-managed Python 3.12+ package that installs the `tb` CLI. Core syncing requires no browser, login, or API keys.

```shell
cd scrapers
uv sync
uv run tb sync      # fetch core sources into files/bids.sqlite (exits non-zero if any source fails)
uv run tb status    # row counts and last run per source
uv run tb export    # write the store to JSON
uv run pytest       # offline test suite (uses local fixtures)
```

See [`scrapers/README.md`](scrapers/README.md) for the source inventory, CLI details, and data caveats, [`deploy/README.md`](deploy/README.md) for nightly scheduling and publishing, and [`docs/README.md`](docs/README.md) for subsystem documentation.

## What it collects

- **Solicitations**: Open, awarded, and cancelled procurements from the City's OData feed, backfilled from CKAN Open Data.
- **Awards**: Winning suppliers and amounts, including split awards across multiple suppliers.
- **Non-competitive contracts**: Sole-source awards and their stated justifications.
- **Bids**: Participating suppliers (including losing bids) recovered from Bid Award Panel agendas, Bid Committee reports, Award Summary Forms, and committee reports.
- **Ariba Discovery postings and attachments**: Archived while open before postings disappear.
- **Suspended and disqualified firms**: The City's public supplier-suspension registry, linked to the City Council decisions that authorized each suspension.
- **Agency and corporation records**: Board reports and public portal listings for Toronto agencies and corporations (including TRCA, Toronto Zoo, Exhibition Place, TTC, Toronto Public Library, TCHC/TSHC, Waterfront Toronto, and others).
- **Suppliers**: A canonical supplier table linking awards, bids, non-competitive contracts, and suspensions across sources.

Competitive City records are keyed by their normalized 10-digit `document_number`.

## Attribution

Some agency procurement listings and records are archived under written permission from the respective bodies on condition that each body is credited as the data source. Those attribution notices are included in `meta.attribution` in the exported JSON, with links to the permission records in [`docs/permissions/`](docs/permissions/). Keep those notices intact if you republish agency data.

## Project status

The scraper in `scrapers/` runs on a nightly schedule and publishes data exports to `CivicTechTO/toronto-bids-data` for the static frontend at `CivicTechTO/toronto-bids-frontend`.

An earlier MySQL-backed Clojure/Angular application lived under `app/` until it was removed during cleanup; it remains in git history if needed.

## Contributing

This is a [CivicTechTO](https://civictech.ca/) project. Issues and pull requests are welcome.

The test suite runs offline against local fixtures with no database, credentials, or network access:

```shell
cd scrapers
uv sync
uv run pytest
```

Council-enrichment tests also invoke `pdftotext` and skip if it is missing. To run them locally, install Poppler (`brew install poppler` on macOS or `apt-get install -y poppler-utils` on Debian/Ubuntu).
