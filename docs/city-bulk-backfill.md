# City committee/Council bulk discovery and backfill

This workload indexes complete agendas across terms, instead of selecting recorded
votes whose titles contain a document number. It preserves item-scoped original
and supplementary PDF links, downloads immutable report bytes and native text,
screens for participation language, and queues the existing shared Sonnet prompt.
It requires no per-year approval or source-specific bidder parser.

## One inventory for the historical terms

Run from `scrapers/`. Use a durable job directory outside the public export. The
archive is opened read-only for discovery, screening and queue preparation.

```sh
python -m toronto_bids.city_bulk run \
  --directory /path/to/city-bulk-job \
  --database /path/to/bids.sqlite
```

The first run resolves CSV resources through the official meeting-schedule CKAN
package and pins their bytes and hashes. It deduplicates multi-day meetings by
term/body/number. The default covers dated meetings from 2010 through the current
year for Council, Executive, Government Management/General Government and Public
Works/Infrastructure bodies. The inventory is explicitly limited to these bodies;
it does not claim all City boards are covered.

`sources.city_agendas seed --from-year ... --through-year ...` can seed a different
range in a separate job. `run` resumes the same pinned schedule snapshot; to add a
new term/calendar snapshot, start a new inventory rather than silently rewriting
the historical enumeration inputs.

Each meeting is validated against its published date, body and meeting number.
Zero parsed item headings cannot count as a complete agenda. A canceled meeting
must also identify the scheduled meeting. Future, pending, held, canceled and
failed states remain distinct. A held source missing or changing on disk fails
resume verification. The current job does not refresh archived agenda revisions.

The anonymous browser uses one headed Chromium context under Xvfb and a serial
request lane. Maintenance/verification pages, HTTP 403/429 and server failures
defer capture with persisted backoff. PDF requests use the existing serial paced
HTTP helper with bounded retries and Retry-After. Long delays stop that lane;
already held sources remain processable. Individual missing PDFs stay visible.

`--meeting-limit` and `--report-limit` bound a pass across terms. Omit them for the
whole inventory. Each completed source is checkpointed. Separate nonblocking locks
prevent two collectors from owning the same agenda or PDF lane.

## Economical extraction and operational collection

```sh
python -m toronto_bids.city_bulk run \
  --directory /path/to/city-bulk-job \
  --database /path/to/bids.sqlite \
  --queue --submit --limit 50 \
  --budget-database /installation/data/llm-usage.sqlite
```

The explicit budget path must name the installation's existing shared queue and
spending authority. A private validation archive must not create another budget.
The existing monthly guard, reviewed model pricing, high-effort Sonnet request
profile, batch discount and submission receipts remain authoritative. The shared
prompt/version is unchanged. Preparation deduplicates PDF hashes, reuses current
cache entries, and excludes maps/other text without participation language from
paid processing. The screen is discovery triage, not proof that excluded reports
contain no bids.

By default, paid work requires a printed or agenda Doc discovery target in the
current awarded-City missing-bid population. Native-call-only and unlocated
reports remain archived without speculative model spending. Use
`--include-unlocated` only for a bounded research cohort. Events outside the
missing population are skipped. Discovery targets are never identity approvals.

Collect through the normal operational command, against the authoritative archive
under the installation's pipeline lock:

```sh
tb extraction-batches collect
```

Do **not** collect the shared queue into a private copy: a global acknowledgement
must follow an authoritative raw cache write. Copy immutable current cache entries
into the validation archive afterward. There is no automatic paid retry. Collection
does not itself authorize procurement-fact publication.

## Admission and measurement

```sh
python -m toronto_bids.city_bulk inventory \
  --directory /path/to/city-bulk-job --database /path/to/validation/bids.sqlite
python -m toronto_bids.city_bulk backfill \
  --directory /path/to/city-bulk-job --database /path/to/validation/bids.sqlite
python -m toronto_bids.bid_coverage \
  --database /path/to/validation/bids.sqlite --out /path/to/coverage-after
```

Automated admission requires a modern Doc agreed by the model contract and
published source roles, literal named firms in a received-firm list or explicitly
labelled received-bid table, and no unsupported participation stage or no-bid
outcome. The primary agenda title can establish the event; when it names only a
native call or project, a unique printed primary report subject must establish the
Doc instead. Mixed-event reports additionally require an explicit local Doc in the
submission context and consistent current-event footers. An incidental historical
mention does not establish a current event. The source quotation is retained.

Derived participation carries no price or compliance status. Standalone literal
supplier rows can preserve wrapped names and footnote markers without interpreting
financial columns. Prequalification, invitations and sole winner descriptions do
not establish actual submitted bids. Unsupported layouts remain exceptions.

Before writing, admission rechecks PDF bytes, native text, agenda bytes, parsed item
text and the exact attachment link. Accepted proofs are exported separately as
`city_bulk_bid_evidence`; they are not human-reviewed cache projections. New bids
use the separate `city_bulk` source with exact document keys. Other sources and
their IDs survive. Replaying preserves IDs and `first_seen`. A later Council copy
can add provenance without replacing supported participation. This inventory's
missing or changed previously admitted evidence refuses the operation; immutable
evidence outside a new job's scope survives. The caller owns commit; the CLI
resolves canonical identities and rebuilds suppliers in the same transaction.

One coherent named list is selected per event rather than unioning differently
named suppliers across supplements. The first pass chooses the largest supported
list, then a deterministic URL/contract tie-break. That selection remains pinned
on resume. Other reports remain in the exception ledger; a larger later list does
not silently invent supplier aliases or replace earlier facts.

`extraction-inventory.json` records every report's screen, contract results,
participation proposals and exceptions. `run.json` records the phase scoreboard.
Historical native calls, complex tables, joint-venture typography and contradictory
publisher fields still require shared admission classes or independent reviewed
identity proofs. They do not become Doc identities through numeric encodings.

The coverage outcome is the difference in **awarded City solicitations without
bids**, together with canonical attribution parity. Report and name counts are
secondary. Keep the production baseline, already merged but unpublished recovery,
and new bulk recovery separate when reporting results.
