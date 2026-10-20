# Agency extraction field recovery (#217)

The City HST mapping and per-contract committee document keys were already fixed by
`b6c6e7d`. This follow-up restores the remaining available cached metadata without
editing the prompt or making model calls.

## References and history

Known TRCA eight-digit identifiers shed their label prefixes. EP references prefer a
published `EP<n>-<year>` code when present, otherwise a labeled contract number sheds
its label. Other printed identifiers retain their spelling apart from uppercase and
whitespace normalization. URLs remain case-sensitive; portal GUIDs remain opaque.
Multiple distinct recognized identifiers are refused rather than assigned arbitrarily.
Portal records share the same normalizer.

A single reference-less contract uses a council item reference when available. A
meeting-level reference such as `2025.EP1` cannot identify one of several reports, so
the report URL is used instead. Multiple contracts lacking references cannot safely
share a fallback: they are refused, counted and logged. No positional identities or
supplier/title/amount matching are introduced.

Stored source-specific identifier aliases are reconciled inside the backfill transaction
before derived bids/awards are rebuilt. A canonical row keeps its id and non-null
metadata, takes missing values from its proven alias, and retains the earliest
`first_seen` and latest `last_seen`. Only proven equivalent aliases are consolidated;
unrecognized and ambiguous identities stay held. This is a narrow correction to the
usual backfill-only `agency_solicitation` rule, not a wholesale rebuild of that history.

## Fields

City and agency bids retain extracted `status` independently of price. Agency bids also
retain the mapped HST basis. A verbatim `price_header` is copied when it exists in the
cached record; existing records without one remain NULL. The header cannot be recovered
by inventing wording from `amount_basis`, and this change does not invalidate the paid
extraction cache. Agency posted/closed dates and award decision dates are copied when
returned. New nullable columns migrate through the existing additive schema mechanism.

## Production-snapshot verification

Production was opened read-only and backed up to `/tmp/tb-217/bids.sqlite`. All changes
below occurred on that copy. `/tmp/tb-217/audit.json` retains the detailed measurements.

| Corpus | Held solicitation rows before | Canonical-format duplicate groups before | Rows after | Duplicate groups after |
| --- | --- | --- | --- | --- |
| TRCA | 641 | 224 | 366 | 0 |
| EP | 141 | 36 | 148 | 0 |
| Zoo | 83 | 0 | 93 | 0 |

These are exact identifier-format groups, not a claim that all possible cross-identifier
equivalences have been established. Net row changes include both alias consolidation
and restoration of safely identifiable contracts.

The copied corpus reports 1,192 TRCA, 6 EP and 31 Zoo ambiguous contracts refused.
Most TRCA cases are reference-less contracts in multi-contract packages, previously
silently dropped. This change makes that limit visible; it does not guess their identity.
A second backfill changed no ids or `first_seen` values across the three agency tables
and normalized no further aliases. Production tables were not modified.

Regression coverage includes canonical keys, council-item and URL fallbacks, ambiguous
multi-contract refusal, available metadata, exact-alias history preservation, and
nullable-column migration. Full suite: **907 passed**.
