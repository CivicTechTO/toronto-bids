# Reviewed City Call Number crosswalks

A historical council item and a numeric City solicitation key can describe the same procurement while keeping different publisher identifiers. `linking/city_crosswalk.py` links only committed reviewed source proofs. Original bid, award and extraction payloads remain unchanged; resolution assigns their existing canonical identities and records redirects and proof hashes.

## Evidence boundary

Two proof classes remain distinct:

- `publisher_native_call`: an original publisher record explicitly names the same native call in its award-details Call Number field. The typed call must be unique and the source agenda, solicitation type and issued/closed dates must agree.
- `corroborated_compound`: the original agenda has one unambiguous native call and a named winner. Exact issued/closed dates, solicitation type, the full supplier name and the initial award's explicitly net-of-tax value identify one record in the complete original publisher population. Punctuation/legal-form variants are allowed; spelling, geography, joint-venture components, generic token overlap, date tolerances and rounded prices are not.

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
