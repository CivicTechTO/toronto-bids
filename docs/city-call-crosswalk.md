# Reviewed City Call Number crosswalks

A historical council item and a numeric City solicitation key can describe the same procurement while keeping different publisher identifiers. `linking/city_crosswalk.py` links only committed reviewed source proofs. Original bid, award and extraction payloads remain unchanged; resolution assigns their existing canonical identities and records redirects and proof hashes.

## Evidence boundary

Source assertions, ordinary compound proofs and explicit reviewed contexts remain distinct:

- `publisher_native_call`: an original publisher record explicitly names the same native call in its award-details Call Number field. The typed call must be unique and the source agenda, solicitation type and issued/closed dates must agree.
- `corroborated_compound`: the original agenda has one unambiguous native call and a named winner. Exact issued/closed dates, solicitation type, the full supplier name and the initial award's explicitly net-of-tax value identify one record in the complete original publisher population. Punctuation/legal-form variants are allowed; spelling, geography, joint-venture components, generic token overlap, date tolerances and rounded prices are not.

Two additional compound proof classes require committed review annotations; discovery keeps the ordinary gates:

- `reviewed_type_discrepancy`: the annotation records both conflicting original methods and why identity is still established. Exact issued/closed dates, full supplier identity and the initial net award must identify one record across **all** publisher methods, in both the original population and current held facts. The raw types remain unchanged. A method disagreement alone never authorizes a match.
- `reviewed_initial_award`: a combined title names several winners, while a separately named first initial award identifies one full supplier and its exact amount. Literal recommendation, supplier heading and net award quotations keep the relation in one scope; later options, other winners and combined totals cannot substitute for it. All ordinary date, method, global uniqueness and competing-call checks still apply.

Literal `Request of Quotation`, colonless labelled dates and a typed native call printed with spaces are source wording/typography variants. They still require the exact date values and agreement with the labelled Call Number field. These recognizers do not map a numeric document key from a call.

The net award witness is bounded to the labelled initial award section. A later option value cannot be selected to match the feed. No tax conversion or contract-period aggregation is performed. The publisher's contract-execution/award-authority date is not a council-decision date and is not used as that proof.

Digit stripping and tender-number padding only create search candidates. Admission never reads that transformation. Enriched solicitation titles, old matcher decisions, model extraction caches and `Parent_Id` cannot supply independent proof. A proposal file has no authority to write links.

## Reproducible review

Capture the original OData solicitation records with the existing paged endpoint, serial pacing and normal backoff. Retain exact responses, request URLs, timestamps and hashes; save a JSON snapshot with a `rows` array. The first pass's pinned capture is described in [the publisher investigation](research/city-call-publisher-keys-2026-11-01.md).

From `scrapers/`:

```sh
uv run python -m toronto_bids.city_crosswalk \
  --database /path/to/bids.sqlite \
  --publisher-snapshot /path/to/original-odata.json \
  --agendas /path/to/council/agendas \
  --out /tmp/city-crosswalk-review
```

The database opens read-only. Discovery refreshes the currently missing historical candidate inventory; optional `--candidates /path/to/frozen.json` replays a frozen inventory. The command writes candidates, proposals, refusal reasons and a portable review bundle outside the database. It makes no source requests or model calls and never applies an equivalence.

Review the proposed source relationships before updating `data/city_call_crosswalk.json` and its compact original publisher population. The gzip population retains every source record's identity fields, so global uniqueness can be replayed without storing every full record. Full original publisher records for approved proofs, page/item hashes and literal source witnesses remain in the review bundle. This changes neither the shared extraction prompt nor its cache version.

## Application and changed context

The OData normalizer stores the latest identity projection in `solicitation.publisher_identity_json`, including the current supplier/value pairs and publisher call details. Empty supplier arrays replace that projection while older `award` rows remain archived. This prevents source corrections/removals from being concealed by an old matching award row. Irrelevant bookkeeping changes and supplier-array ordering do not invalidate identity evidence.

**First deployment requires the original publisher identity snapshots before resolving the new links.** An ordinary OData sync populates them. A controlled recovery can populate only this newly introduced column from a fresh, pinned original response after checking the record ID, document key, dates, type and reviewed facts. Never seed it from derived titles or reconstruct a current supplier set from the award archive. The production-copy rehearsal seeds the new field from its pinned official capture; it does not change pre-existing source fields.

The resolver revalidates current source fields, global ambiguity, agenda title/call context, original bytes when held, and competing source identities before storing any new link. A previously accepted proof can travel with a released database when raw agenda files are absent; its canonical JSON and hash must still agree. A new approval requires the held original agenda. Changed contexts or silently removed registry entries fail visibly and require explicit review/repair.

Accepted links use `solicitation_link.method='city_call_crosswalk'`. `city_crosswalk_evidence` is an immutable history table included in SQLite, CSV and Parquet exports; `procurement.merge_evidence` names the proof class and hash when an identity is merged. Legacy supplier/value rebuilds replace only their own rows. An existing canonical ID and its redirects remain archive history; removing a link does not undo a merge, so rejected identities need an explicit repair.

The reviewed first-pass measurements and negative controls are in [the validation report](research/city-call-crosswalk-validation-2026-11-01.md).

## Reviewed cached City bids

`linking/city_cached_bids.py` applies `data/city_cached_bid_reviews.json` during ordinary procurement resolution, including nightly export. Explicit BA/BD backfills use the same projections. Each approval binds the original PDF and text hashes, whole extraction result, contract position, council title, independent procurement identity and literal submission table/row quotations. Historical identities pass the crosswalk gate above; modern identities require a primary Doc assertion. Participation in a prequalification belongs to that event, not a later tender.

This is a reviewed projection over immutable cache payloads, not another extractor. Wrapped names and price footnotes come from bounded source rows; inferred compliance and unsupported tax/price claims are withheld. Duplicate submissions, lots and contradictory document roles remain held until their scope can be represented and proved. The shared extraction prompt/version stays unchanged.

A new approval requires the held original PDF. Moved archives may use the conventional council download path only after matching its pinned hash. Applied `city_bid_evidence` proofs travel with the public database, allowing portable replay without local PDFs. A changed source, cache, identity, applied proof or conflicting link aborts before replacing bids; the canonical resolver also rolls back the projections and evidence if later resolution fails. Surviving bid IDs and `first_seen` values are preserved. Re-review and explicit repair are required to amend an applied proof.

When extending this registry, freeze the entire target inventory, review identity independently of submission/price scope, and account for every case, including holds. Rehearse against a fresh production copy and compare original facts, canonical identities, immutable caches and exports before deploying. The [November 5 review and validation](research/city-cached-bid-validation-2026-11-05.md) links the complete case inventory and source investigation.

The [43-case identity-hold follow-up](research/city-identity-holds-validation-2026-11-05.md) records the reviewed discrepancy and named-award decisions, all remaining holds, and the production-copy rehearsal. Reviewed hourly prices retain `/Hour` and have no parsed numeric total; a report declaring more bids than it names retains an explicit completeness discrepancy in its exported proof.
