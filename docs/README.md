# Documentation

Reference documentation for the Toronto Bids archive and scraper.

## Core guides

- [Project overview](../README.md)
- [Scraper package & data caveats](../scrapers/README.md)
- [Deployment, nightly schedule & publishing](../deploy/README.md)
- [Glossary](../GLOSSARY.md)
- [Procurement identity design](designs/2026-10-22-procurement-identity.md)
- [Engineering reference (`CLAUDE.md`)](../CLAUDE.md)

## Extraction & entity resolution

- [Document extraction](document-extraction.md): LLM extraction corpora, cache, classifier gate, backfill swap rules, and parsing discipline.
- [Asynchronous Claude batches](claude-batches.md): Batch queuing, hourly collection, lost-receipt recovery, and budget limits.
- [Supplier entity resolution](supplier-entity-resolution.md): `supplier_key` normalization rules and audit script.
- [Extraction schema by document type](protocols/extraction-schema-by-document-type.md): What fields are extracted or skipped for each document class.
- [Model selection protocol](protocols/model-selection-by-measured-ordering.md): Evaluating extraction models against ground truth.

## City of Toronto sources & historical recovery

- [City council, agenda, and award sources](city-sources.md): BA/BD agendas, titles, composite awards, Award Summary Forms, and committee awards.
- [Ariba attachments](ariba-attachments.md): Per-file attachment capture behind Respond, bundling, version merging, and AribaWeb UI quirks.
- [Reviewed City Call Number crosswalks](city-call-crosswalk.md): Linking pre-Ariba council items to numeric City document keys, plus reviewed cached bids.
- [Published City document aliases](city-document-aliases.md): Same-page Call Number to Ariba Doc aliases.
- [Scoped City bid responses](scoped-city-bids.md): Multi-lot, multi-category, and alternative bid tables (see [ADR 0001](adr/0001-scoped-bid-responses.md)).
- [Native-call participation in composite reports](city-native-participation.md): Recovering individual call participation from multi-contract reports.
- [Image-only City report text (OCR)](image-pdf-text.md): Tesseract OCR fallback and reviewed image-space witnesses for scanned BD reports.
- [Historical BD report recovery](bd-report-recovery.md): 2013-2016 Bid Committee report capture, identity repairs, and guarded admission.
- [City committee/Council bulk backfill](city-bulk-backfill.md): Multi-term agenda indexing, screening, and automated participation admission.

## Agency & corporation sources

- [Agency capture overview](agencies.md): TRCA, Toronto Zoo, Exhibition Place board reports, bids&tenders portals, and TRCA history.
- [Bonfire public listings](bonfire-listings.md): TPL, TCHC/TSHC, Waterfront, Exhibition Place, and TAF portals.
- [MERX public capture & TTC reports](merx.md): Five agency profiles on MERX and supplementary TTC reports.
- [TTC Board historical capture](ttc-history.md): 2000-2024 TTC archive capture, reviewed admission, and metadata withholding.
- [Waterfront Toronto awards](waterfront-awards.md): Monthly HTML award archive, extraction verification, and corroborated recipients.
- [Archival permissions](permissions/): Written permission records and access terms for agency portals.

## Contributor & agent workflows

- [Domain docs guide](agents/domain.md)
- [Issue tracker conventions](agents/issue-tracker.md)
- [Triage labels](agents/triage-labels.md)

## Evaluation data & test review snapshots

- [Human ground-truth labels](ground-truth/) and [machine-generated labels](machine-labels/)
- Test-pinned JSON review snapshots in `docs/` and `docs/research/` are loaded directly by `scrapers/tests/`.
