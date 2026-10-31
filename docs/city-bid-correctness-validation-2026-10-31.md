# City bid correctness and acquisition validation — 2026-10-31

The first implementation was rehearsed on the private post-19-recovery production snapshot. It is not a production data repair or a completed model backfill. Production data and provider credentials were not used for mutation or extraction.

## Source-backed correctness

- The real BD25.1 HTML regression now gives Bond Paving and Construction the first raw price `$ 286,536.36`, Serve Construction the next price `$359,463.32`, and Dig-Con the final price `$995,617.27`. All eight firms, prices, raw markers and including-HST basis match the held source table. No footnote becomes a supplier.
- Complete reparse calibration covered 891 cached agendas: 17,782 prior observations vs 17,755 parser outputs, 53 changed references, five gained references and twelve fully refused references. No BA reference changed. Those are parser-level outputs, not approved database growth/deletion counts. The new store retains prior rows on zero/partial reparses and surfaces failures; it does not treat this count difference as permission to erase facts.
- The matcher still requires the original unique legacy candidate. Stricter supplier/date guards cannot turn an originally ambiguous price/token match into a new join. Complete calibration removes exactly four reviewed associations from the existing 1,284-link set and introduces no new identity links.
- Two associations are source-proven different procurements; two supplier spelling/joint-venture variants are explicitly withheld pending independent proof. Reviewed source context changes abort all corrections transactionally.
- The production-copy rehearsal keeps every City document ID and all award, agency, raw-cache and Ariba-file payloads unchanged. It detaches the reviewed council-reference keys, preserves split provenance and corrects only the targeted eight-row table. Raw bid content stays unchanged during identity splitting; the subsequent item-scoped parser correction replaces the shifted observations.
- In that copy, the measured missing-bid population moves from 4,105 to 4,107. Correctness can increase the honest gap. This result includes no speculative native Call Number joins or model extraction.

## Acquisition

A read-only dry run reproduces the exact 586-report all-years inventory: 187 in 2013, 123 in 2014, 158 in 2015 and 118 in 2016. The initial network run uses an isolated database copy and separate content-addressed directory, with one serial request lane and no extraction provider. **All 586 PDFs captured successfully**, with 586 distinct hashes. An offline resume revalidated their hashes/structure with zero new requests. Of these, 568 have extracted text and 18 remain held without readable text, requiring explicit OCR/source follow-up rather than being treated as empty procurements. Final capture/hash verification is recorded in `.validation-systematic-bids-2026-10-31/implementation/`; downloaded PDFs and the private database are not committed.

Tests exercise frozen inventory conservation after bid facts appear, bounded/resumed capture, corrupt held bytes, changed source contexts, HTML/non-PDF responses, long Retry-After stopping the entire batch, paced retries, inventory locking, read-only CLI dry runs, atomic identity repairs and preservation of source/publisher metadata. The full offline suite passed **1,456 tests** before the final documentation-only clarification.

The implementation does not claim that 586 reports imply 586 newly covered City solicitations. Next admission work must preserve native call/contract, lot, stage and partial-disclosure scope and use the shared batched provider/cache. See [the operational contract](bd-report-recovery.md) and [the complete research plan](research/systematic-city-bid-recovery-2026-10-31.md).
