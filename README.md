# Toronto Bids Project

The Toronto Bids Project promotes transparency and accountability in City of Toronto
procurement. The City publishes solicitations and awards while they are open, but the
data is hard to use and much of it becomes difficult to reach once a bid closes. This
project pulls that data into a local, queryable store and exports it as a single public
artifact — so the record stays available after the due date has passed.

## Quick start

Everything lives in [`scrapers/`](scrapers/), a `uv`-managed Python 3.12 package that
installs a `tb` command. No browser, no login, no cloud, no API keys.

```shell
cd scrapers
uv sync
uv run tb sync      # fetch every source into files/bids.sqlite
uv run tb status    # row counts
uv run tb export    # write the whole store to one JSON artifact
uv run pytest       # tests (offline; uses fixtures)
```

See [`scrapers/README.md`](scrapers/README.md) for the source inventory, the data model,
and the opt-in council/PDF enrichment.

## What it collects

- **Solicitations** — the lifecycle spine (open / awarded / cancelled), from the City's
  OData feed, backfilled from CKAN Open Data.
- **Awards** — who won, for how much, including divisible awards split across suppliers.
- **Non-competitive contracts** — sole-source awards and their stated justification.
- **Ariba Discovery postings** — archived while open, before they disappear.
- **Suspended & disqualified firms** — the City's public supplier-suspension registry,
  bridged to the City Council decision that authorized each suspension.
- **Suppliers** — a canonicalized supplier dimension linking the above together, so you
  can ask "which contracts belong to this firm?"

Everything competitive is keyed on the normalized 10-digit `document_number`.

## Project status

The scraper (`scrapers/`) is the active, working part of this repo, and `tb export`
produces the public artifact.

A previous MySQL-backed application — a Clojure API plus Clojure and Angular frontends —
lived under `app/` and was dormant from mid-2023. It was removed in the repo cleanup;
it remains in git history if anyone wants to revive it. Publishing the exported JSON is
the open next step, and it does not depend on that older stack.

## Contributing

This is a [CivicTechTO](https://civictech.ca/) project. Issues and pull requests welcome.
The scraper's tests run offline against fixtures, so `cd scrapers && uv sync && uv run pytest`
is enough to get a working development setup — it needs no database, credentials, or network.
